"""
build_topic_vocabulary.py
--------------------------
LLM identify-and-invent normalisation pass over the topic field.

Step 1 -- Seed pass
    Derives canonical vocabulary from freq>=N labels per domain.
    Output: canonical_topics_v1.json

Step 2 -- Mapping pass
    Maps every label to a canonical topic (seed or invented).
    Output: topic_cluster_map.json

Quality features:
    --dry-run       Run one batch per domain, print input/output, stop.
                    Equivalent to test_mode in topic_extraction_v2.py.
    --check-map     Post-run consistency check on topic_cluster_map.json.
                    Flags near-duplicate invented topics (Jaccard >= 0.6).
    --review-map    Generates mapping_review.txt: every canonical topic
                    with its frequency and top-5 raw labels. Precision audit
                    for human or Serina review before apply_topic_map.py.

Instrumentation added vs prior version:
    - Dry-run mode (Gap 1)
    - Output validation per batch: empty, overlength, non-lowercase (Gap 2)
    - Post-run quality breakdown: seed maps, invented, self-maps, fallbacks (Gap 3)
    - Live invention alerts during run when a batch invents >N topics (Gap 4)
    - Dry-run results written to mapping_dryrun_results.txt (Gap 5)
    - Consistency check for near-duplicate invented topics across batches (New 1)
    - Coverage projection from dry-run sample (New 2)
    - Canonical topic review file for precision audit (New 3)

Usage:
    python build_topic_vocabulary.py --step 1
    python build_topic_vocabulary.py --step 2
    python build_topic_vocabulary.py --step 2 --dry-run
    python build_topic_vocabulary.py --step 1 --reset-domains "Software Engineering" AI
    python build_topic_vocabulary.py --check-map
    python build_topic_vocabulary.py --review-map

Config: config_vocab.yaml
Author: Ananth / Vectorial AI -- June 2026
"""

import argparse
import csv
import json
import logging
import os
import re
import sys
import io
import time
from collections import defaultdict
from pathlib import Path

import yaml
from openai import OpenAI
from tqdm import tqdm

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

csv.field_size_limit(10_000_000)

# ── Defaults ──────────────────────────────────────────────────────────────────

DEFAULTS = {
    "input_path":             "tagged_posts_v2_clean.csv",
    "vocab_output":           "canonical_topics_v1.json",
    "map_output":             "topic_cluster_map.json",
    "normalised_output":      "tagged_posts_v2_normalised.csv",
    "checkpoint_seed":        "checkpoint_vocab_seed.json",
    "checkpoint_map":         "checkpoint_vocab_map.jsonl",
    "log_path":               "build_topic_vocabulary.log",
    "dryrun_output":          "mapping_dryrun_results.txt",
    "review_output":          "mapping_review.txt",
    "model":                  "gpt-4.1-mini",
    "temperature":            0.0,
    "max_tokens_seed":        4000,
    "max_tokens_map":         6000,
    "seed_freq_threshold":    2,
    "domain_seed_thresholds": {},
    "mapping_batch_size":     60,
    "invention_alert_threshold": 3,   # print alert if a batch invents > N topics
    "consistency_jaccard_threshold": 0.6,  # flag invented pairs above this
    "domains": [
        "Software Engineering",
        "AI",
        "Security",
        "EdTech",
        "Career",
        "Business & Strategy",
        "Data & Analytics",
        "Leadership & Management",
        "Open Source",
    ],
}

# ── Config ────────────────────────────────────────────────────────────────────

def load_config(path: str) -> dict:
    cfg = DEFAULTS.copy()
    cfg["domain_seed_thresholds"] = {}
    config_path = Path(os.path.dirname(os.path.abspath(__file__))) / path
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            overrides = yaml.safe_load(f) or {}
        cfg.update(overrides)
    else:
        print(f"  No config file found at {config_path} -- using defaults.")
    return cfg

def resolve(here: str, p: str) -> str:
    return os.path.join(here, p)

# ── Logger ────────────────────────────────────────────────────────────────────

def setup_logger(log_path: str) -> logging.Logger:
    logger = logging.getLogger("build_vocab")
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG)
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    ch = logging.StreamHandler()
    ch.setLevel(logging.WARNING)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    fh.setFormatter(fmt)
    ch.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger

# ── Corpus helpers ────────────────────────────────────────────────────────────

def load_corpus(path: str) -> list[dict]:
    posts = []
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            posts.append(row)
    return posts

def get_label_freq(posts: list[dict], domain: str) -> dict[str, int]:
    freq: dict[str, int] = defaultdict(int)
    for p in posts:
        if p.get("topic_broad") != domain:
            continue
        label = (p.get("topic") or "").strip().lower()
        if label and label != "unclassified":
            freq[label] += 1
    return dict(freq)

# ── API helpers ───────────────────────────────────────────────────────────────

def call_llm(system_prompt, user_message, client, cfg, max_tokens, logger, tag=""):
    t0 = time.time()
    stream = client.chat.completions.create(
        model=cfg["model"],
        temperature=cfg["temperature"],
        stream=True,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_message},
        ],
        max_tokens=max_tokens,
    )
    chunks = []
    for chunk in stream:
        delta = chunk.choices[0].delta.content
        if delta:
            chunks.append(delta)
    raw = "".join(chunks).strip()
    logger.debug(f"API_CALL tag={tag} chars={len(raw)} elapsed={time.time()-t0:.2f}s")
    return raw

def safe_json_parse(raw: str):
    text = raw.strip()
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    return json.loads(text)

def safe_call_with_retry(system_prompt, user_message, client, cfg, max_tokens,
                         logger, tag="", retries=3):
    for attempt in range(retries):
        try:
            raw = call_llm(system_prompt, user_message, client, cfg,
                           max_tokens, logger, tag)
            return safe_json_parse(raw)
        except json.JSONDecodeError as e:
            logger.warning(f"JSON_ERROR tag={tag} attempt={attempt+1} error={e} "
                           f"raw_head={raw[:120]!r}")
            time.sleep(2 ** (attempt + 1))
        except Exception as e:
            logger.warning(f"API_ERROR tag={tag} attempt={attempt+1} "
                           f"type={type(e).__name__} error={e}")
            time.sleep(2 ** (attempt + 1))
    logger.error(f"CALL_FAILED tag={tag} all_attempts_exhausted=True")
    return None

# ── Step 1: Seed pass ─────────────────────────────────────────────────────────

SEED_SYSTEM = """You are building a canonical topic vocabulary for a cross-platform behavioural research study. The topic field captures the STIMULUS of a post: the underlying subject or event that caused the person to write. It does not capture the author's opinion, angle, or evaluation -- those are extracted separately.

You will receive a list of topic labels extracted from posts in a specific domain.
Each label was generated by an LLM from an individual post. Synonyms and near-duplicates are common.

The decision rule for whether two labels are the same topic:
  Two posts written in response to the same underlying thing must share a topic label,
  regardless of what opinion or angle each author took.

Your task is identify-and-invent:
  1. Read ALL the labels carefully.
  2. Identify the distinct underlying stimuli they represent.
  3. For each distinct stimulus, invent a clean canonical name (2-5 words, lowercase, noun phrase).

COLLAPSE these into one canonical topic:
  - Surface-form variants and word-order swaps:
      "microservices vs monolith" + "monolith vs microservices" -> one topic
  - The same stimulus with different angle or evaluation modifiers:
      "postgresql performance optimization", "postgresql performance issues",
      "postgresql performance benchmarking" -> "postgresql performance"
      "ai coding tools pricing", "ai coding tools learning",
      "ai coding tools debugging" -> "ai coding tools"
    The angle (pricing, issues, optimization, challenges, best practices) is the author's
    chosen lens, not the stimulus. Strip it. It is captured in a separate field.

KEEP SEPARATE:
  - Labels about different subjects, even when they share words:
      "postgresql performance" and "python performance" are different stimuli.
      "database indexing" and "database migration" are different stimuli.
  - Genuinely distinct recurring debates or events:
      "microservices vs monolith" is its own stimulus, distinct from
      "microservices architecture" in general.

Rules:
  - Do NOT invent topics not represented in the label list.
  - Do NOT collapse topics that are genuinely distinct.
  - Canonical names describe what the post is ABOUT, never how the author felt about it.
  - Low-frequency variants you cannot place do not need to be enumerated -- a separate
    mapping pass handles every remaining label individually.

Return ONLY a JSON array of canonical topic strings, most frequent topics first.
No preamble, no explanation, no markdown.

Example output:
["postgresql performance", "ai coding tools", "microservices vs monolith", "api security vulnerabilities", "llm fine-tuning"]"""


def build_seed_user_message(domain: str, freq: dict, threshold: int) -> str:
    seed_labels = {l: f for l, f in freq.items() if f >= threshold}
    sorted_labels = sorted(seed_labels.items(), key=lambda x: -x[1])
    n = len(sorted_labels)
    lo, hi = max(10, n // 3), max(15, n // 2)
    lines = [f"Domain: {domain}", "",
             f"Topic labels (frequency >= {threshold}, sorted by frequency):", ""]
    for label, f in sorted_labels:
        lines.append(f"  {label} ({f})")
    lines.append("")
    lines.append(f"This list contains {n} labels. After collapsing synonyms and "
                 f"angle variants, expect a canonical vocabulary of roughly {lo}-{hi} "
                 f"topics. This is guidance, not a quota -- the data decides.")
    return "\n".join(lines)


def run_seed_pass(posts, cfg, client, logger, checkpoint_path):
    vocab = {}
    if os.path.exists(checkpoint_path):
        with open(checkpoint_path, encoding="utf-8") as f:
            vocab = json.load(f)
        print(f"  Seed checkpoint loaded: {len(vocab)} domain(s) already done.")

    domains = cfg["domains"]
    default_threshold = cfg["seed_freq_threshold"]
    domain_thresholds = cfg.get("domain_seed_thresholds", {})

    for domain in domains:
        if domain in vocab:
            print(f"  Seed [{domain}]: already done ({len(vocab[domain])} topics) -- skipping.")
            continue

        threshold = domain_thresholds.get(domain, default_threshold)
        freq = get_label_freq(posts, domain)
        seed_count = sum(1 for f in freq.values() if f >= threshold)
        total_labels = len(freq)

        if seed_count == 0:
            print(f"  Seed [{domain}]: no labels at freq>={threshold} -- skipping.")
            vocab[domain] = []
            continue

        print(f"  Seed [{domain}]: {seed_count} freq>={threshold} labels "
              f"(of {total_labels} total) ...")

        user_msg = build_seed_user_message(domain, freq, threshold)
        result = safe_call_with_retry(
            SEED_SYSTEM, user_msg, client, cfg,
            cfg["max_tokens_seed"], logger, tag=f"seed:{domain}",
        )

        if result is None or not isinstance(result, list):
            logger.error(f"SEED_FAILED domain={domain!r} result={result!r}")
            print(f"  Seed [{domain}]: FAILED -- skipping domain.")
            continue

        canonical = [str(t).strip().lower() for t in result if str(t).strip()]
        vocab[domain] = canonical
        print(f"  Seed [{domain}]: {len(canonical)} canonical topics identified "
              f"(threshold: freq>={threshold}).")
        logger.info(f"SEED_DONE domain={domain!r} canonical_count={len(canonical)} "
                    f"threshold={threshold}")

        with open(checkpoint_path, "w", encoding="utf-8") as f:
            json.dump(vocab, f, ensure_ascii=False, indent=2)

    return vocab

# ── Step 2: Mapping pass ──────────────────────────────────────────────────────

MAP_SYSTEM = """You are mapping raw topic labels to canonical topics for a behavioural research study. The topic field captures the STIMULUS of a post: the underlying subject that caused the person to write. The author's angle or evaluation is captured in a separate field.

You will receive:
  1. A domain name
  2. The canonical topic vocabulary for that domain. It has two sections:
     the established seed vocabulary, and topics invented in prior batches.
  3. A batch of raw labels to map

The decision rule: a raw label maps to a canonical topic only if posts carrying both
would be responding to the same underlying thing. Different opinions about the same
stimulus -> same topic. Same words about different stimuli -> different topics.

For each raw label, exactly one of three outcomes:
  a. MAP to an existing canonical topic when the label is the same stimulus
     phrased differently. This includes angle-modifier variants:
       "postgresql performance benchmarking" -> "postgresql performance"
       "ai coding tools pricing" -> "ai coding tools"
  b. INVENT a new canonical topic (2-5 words, lowercase, noun phrase) when the label
     is a genuine stimulus with no equivalent in the vocabulary. Before inventing,
     check the topics invented in prior batches -- if a semantic equivalent already
     exists there, use it instead of inventing a near-duplicate.
  c. SELF-MAP: if the label is already a clean canonical name for a genuinely
     distinct one-off topic (a specific technology, named event, or product),
     mapping it to itself or a lightly cleaned version of itself is correct.
     Do not force distinct one-offs into broader topics to avoid self-mapping.

Shared words are NOT shared stimulus. These mappings are WRONG -- never do this:
  "python performance optimization" -> "postgresql performance"     (different technology)
  "platform engineering best practices" -> "postgresql best practices"  (different subject)
  "ai in financial services" -> "ai in education"                   (different sector)
The subject of the label -- the technology, product, sector, or concept -- must match
the canonical topic's subject. A shared modifier word is never sufficient.

Output rules:
  - Return ONLY a JSON object mapping each raw label to its canonical topic.
  - The keys must be EXACT character-for-character copies of the raw labels as given.
    Do not renumber, reformat, retitle, or correct them.
  - Every label receives exactly one mapping. No label may be omitted.
  - No preamble, no explanation, no markdown.

Example output:
{
  "postgresql performance optimization": "postgresql performance",
  "monolith vs microservices debate": "microservices vs monolith",
  "vibe coding persistence": "vibe coding",
  "webflow cms features": "webflow cms",
  "scorm 1.2 limitations": "scorm 1.2 limitations"
}"""


def build_map_user_message(domain, seed_vocab, canonical_vocab, label_batch):
    seed_set = set(seed_vocab)
    invented = [t for t in canonical_vocab if t not in seed_set]
    vocab_lines = [f"  - {t}" for t in sorted(seed_vocab)]
    if invented:
        vocab_lines.append("  -- additionally invented in prior batches --")
        vocab_lines.extend(f"  - {t}" for t in sorted(invented))
    vocab_str  = "\n".join(vocab_lines)
    labels_str = "\n".join(f"  {lbl}" for lbl in label_batch)
    return (
        f"Domain: {domain}\n\n"
        f"Canonical topic vocabulary ({len(canonical_vocab)} topics):\n{vocab_str}\n\n"
        f"Raw labels to map ({len(label_batch)} labels, one per line):\n{labels_str}"
    )

# ── Gap 2: Output validation ──────────────────────────────────────────────────

def validate_batch_mappings(batch_mappings: dict, logger, tag: str) -> dict:
    """
    Validate every canonical topic returned in a batch mapping.
    Flags: empty string, longer than 80 chars, contains uppercase,
    contains suspicious characters. Logs warnings; does not reject mappings.
    Returns dict of {raw_label: issue} for any violations found.
    """
    issues = {}
    for raw, canonical in batch_mappings.items():
        if not canonical:
            logger.warning(f"VALIDATION empty canonical tag={tag} raw={raw!r}")
            issues[raw] = "empty"
        elif len(canonical) > 80:
            logger.warning(f"VALIDATION overlength tag={tag} raw={raw!r} "
                           f"canonical={canonical!r} len={len(canonical)}")
            issues[raw] = "overlength"
        elif canonical != canonical.lower():
            logger.warning(f"VALIDATION uppercase tag={tag} raw={raw!r} "
                           f"canonical={canonical!r}")
            issues[raw] = "uppercase"
        elif re.search(r'["\'\{\}\[\]]', canonical):
            logger.warning(f"VALIDATION bad_chars tag={tag} raw={raw!r} "
                           f"canonical={canonical!r}")
            issues[raw] = "bad_chars"
    return issues


# ── Gap 3: Domain quality stats ───────────────────────────────────────────────

class DomainStats:
    """Tracks mapping quality stats for one domain during the mapping pass."""
    def __init__(self, seed_vocab: list[str]):
        self.seed_set      = set(seed_vocab)
        self.seed_maps     = 0   # label mapped to an existing seed
        self.invented      = 0   # label caused a new canonical topic to be invented
        self.self_maps     = 0   # label mapped to itself (distinct one-off)
        self.fallbacks     = 0   # batch failed entirely, label self-mapped as fallback
        self.validation_issues = 0

    def record_batch(self, batch_mappings: dict, invented_in_batch: list,
                     is_fallback: bool = False, issues: dict = None):
        if is_fallback:
            self.fallbacks += len(batch_mappings)
            return
        for raw, canonical in batch_mappings.items():
            if canonical in self.seed_set:
                self.seed_maps += 1
            elif canonical == raw or canonical == raw.strip().lower():
                self.self_maps += 1
            elif canonical in invented_in_batch:
                self.invented += 1
            else:
                # mapped to a topic invented in a prior batch
                self.seed_maps += 1
        if issues:
            self.validation_issues += len(issues)

    def summary_line(self, total_labels: int) -> str:
        total = self.seed_maps + self.invented + self.self_maps + self.fallbacks
        return (
            f"    seed maps: {self.seed_maps:>5}  "
            f"invented: {self.invented:>5}  "
            f"self-maps: {self.self_maps:>5}  "
            f"fallbacks: {self.fallbacks:>3}  "
            f"validation issues: {self.validation_issues:>3}  "
            f"total: {total}/{total_labels}"
        )


# ── Core mapping logic (shared by full run and dry-run) ───────────────────────

def process_batch(batch, domain, seed_vocab, canonical_vocab, cluster_map,
                  client, cfg, logger, stats, dry_run=False, dryrun_lines=None):
    """
    Process one batch of labels. Updates cluster_map and canonical_vocab in place.
    Returns (batch_mappings, invented_in_batch, is_fallback).
    """
    tag = f"map:{domain}:{batch[0][:30]}"
    user_msg = build_map_user_message(domain, seed_vocab, canonical_vocab, batch)

    if dry_run and dryrun_lines is not None:
        dryrun_lines.append(f"\n{'─'*65}")
        dryrun_lines.append(f"DOMAIN: {domain}  |  BATCH: {batch[0][:40]} ... ({len(batch)} labels)")
        dryrun_lines.append(f"{'─'*65}")
        dryrun_lines.append("INPUT LABELS:")
        for lbl in batch:
            dryrun_lines.append(f"  {lbl}")

    result = safe_call_with_retry(
        MAP_SYSTEM, user_msg, client, cfg,
        cfg["max_tokens_map"], logger, tag=tag,
    )

    if result is None or not isinstance(result, dict):
        logger.error(f"MAP_BATCH_FAILED domain={domain!r} "
                     f"batch_start={batch[0]!r} result={str(result)[:120]}")
        fallback = {lbl: lbl for lbl in batch}
        cluster_map[domain].update(fallback)
        stats.record_batch(fallback, [], is_fallback=True)
        if dry_run and dryrun_lines is not None:
            dryrun_lines.append("  [BATCH FAILED -- all labels self-mapped as fallback]")
        return fallback, [], True

    batch_mappings = {}
    invented_in_batch = []
    for raw_label in batch:
        canonical = None
        for key in result:
            if key.strip().lower() == raw_label.strip().lower():
                canonical = str(result[key]).strip().lower()
                break
        if canonical is None:
            canonical = str(result.get(raw_label, raw_label)).strip().lower()
        batch_mappings[raw_label] = canonical
        if canonical not in canonical_vocab:
            canonical_vocab.append(canonical)
            invented_in_batch.append(canonical)

    issues = validate_batch_mappings(batch_mappings, logger, tag)
    stats.record_batch(batch_mappings, invented_in_batch, issues=issues)
    cluster_map[domain].update(batch_mappings)

    # Gap 4: live invention alert
    alert_thresh = cfg.get("invention_alert_threshold", 3)
    if len(invented_in_batch) > alert_thresh:
        print(f"  [ALERT] {domain}: batch invented {len(invented_in_batch)} new topics: "
              f"{invented_in_batch[:3]}{'...' if len(invented_in_batch) > 3 else ''}")
        logger.warning(f"INVENTION_ALERT domain={domain!r} "
                       f"count={len(invented_in_batch)} examples={invented_in_batch[:3]}")

    if invented_in_batch:
        logger.info(f"INVENTED domain={domain!r} count={len(invented_in_batch)} "
                    f"examples={invented_in_batch[:3]}")

    if dry_run and dryrun_lines is not None:
        dryrun_lines.append("MAPPINGS:")
        seed_set = set(seed_vocab)
        for raw, canon in batch_mappings.items():
            if canon in invented_in_batch:
                marker = " [NEW]"
            elif canon == raw or canon == raw.strip().lower():
                marker = " [SELF]"
            elif canon not in seed_set:
                marker = " [PRIOR-INVENTED]"
            else:
                marker = ""
            dryrun_lines.append(f"  {raw:<50} -> {canon}{marker}")
        if issues:
            dryrun_lines.append(f"  VALIDATION ISSUES: {issues}")

    return batch_mappings, invented_in_batch, False


def run_mapping_pass(posts, vocab, cfg, client, logger, checkpoint_path,
                     dry_run=False, dryrun_output_path=None):
    cluster_map = defaultdict(dict)
    if os.path.exists(checkpoint_path):
        with open(checkpoint_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    entry = json.loads(line)
                    cluster_map[entry["domain"]].update(entry["mappings"])
        mapped_total = sum(len(v) for v in cluster_map.values())
        print(f"  Map checkpoint loaded: {mapped_total} labels already mapped.")

    batch_size = cfg["mapping_batch_size"]
    all_domain_stats = {}
    dryrun_lines = [] if dry_run else None

    if dry_run:
        print("  DRY-RUN MODE -- one batch per domain, then stop.")
        print("  Results will be printed and written to file.\n")

    for domain in cfg["domains"]:
        if domain not in vocab or not vocab[domain]:
            print(f"  Map [{domain}]: no vocabulary -- skipping.")
            continue

        freq = get_label_freq(posts, domain)
        all_labels = sorted(freq.keys())
        already_mapped = set(cluster_map.get(domain, {}).keys())
        remaining = [l for l in all_labels if l not in already_mapped]

        if not remaining:
            print(f"  Map [{domain}]: all {len(all_labels)} labels already mapped -- skipping.")
            continue

        seed_vocab = list(vocab[domain])
        checkpoint_canonicals = set(seed_vocab)
        for mapped_canonical in cluster_map.get(domain, {}).values():
            checkpoint_canonicals.add(mapped_canonical)
        canonical_vocab = list(checkpoint_canonicals)

        stats = DomainStats(seed_vocab)
        all_domain_stats[domain] = stats

        if dry_run:
            # Run exactly one batch per domain
            test_batch = remaining[:batch_size]
            print(f"  Dry-run [{domain}]: testing {len(test_batch)} labels ...")
            process_batch(test_batch, domain, seed_vocab, canonical_vocab,
                          cluster_map, client, cfg, logger, stats,
                          dry_run=True, dryrun_lines=dryrun_lines)
            # Coverage projection from this sample
            n_seed = stats.seed_maps
            n_inv  = stats.invented
            n_self = stats.self_maps
            total_s = n_seed + n_inv + n_self
            if total_s > 0:
                proj_seed = n_seed / total_s * 100
                proj_inv  = n_inv  / total_s * 100
                proj_self = n_self / total_s * 100
                est_total_labels = len(all_labels)
                print(f"    Sample ({len(test_batch)} labels): "
                      f"seed maps {proj_seed:.0f}%  "
                      f"invented {proj_inv:.0f}%  "
                      f"self-maps {proj_self:.0f}%")
                print(f"    Projected over {est_total_labels} total labels: "
                      f"~{int(n_seed/total_s*est_total_labels)} seed maps, "
                      f"~{int(n_inv/total_s*est_total_labels)} invented, "
                      f"~{int(n_self/total_s*est_total_labels)} self-maps")
            continue

        # Full run
        print(f"  Map [{domain}]: {len(remaining)} labels remaining "
              f"({len(already_mapped)} already done). "
              f"Vocab size: {len(canonical_vocab)} topics ...")

        batches = [remaining[i:i + batch_size] for i in range(0, len(remaining), batch_size)]
        progress = tqdm(batches, desc=f"  {domain[:25]:<25}", unit="batch", ncols=80)

        for batch in progress:
            batch_mappings, invented_in_batch, is_fallback = process_batch(
                batch, domain, seed_vocab, canonical_vocab,
                cluster_map, client, cfg, logger, stats,
            )
            if not is_fallback:
                with open(checkpoint_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"domain": domain, "mappings": batch_mappings},
                                       ensure_ascii=False) + "\n")

        vocab[domain] = canonical_vocab
        print(f"  Map [{domain}]: done. "
              f"{len(cluster_map[domain])}/{len(all_labels)} labels mapped. "
              f"Vocab size: {len(canonical_vocab)} topics.")
        print(stats.summary_line(len(all_labels)))
        logger.info(f"MAP_DOMAIN_DONE domain={domain!r} "
                    f"mapped={len(cluster_map[domain])} vocab_size={len(canonical_vocab)}")

    # Write dry-run results file (Gap 5)
    if dry_run and dryrun_lines and dryrun_output_path:
        with open(dryrun_output_path, "w", encoding="utf-8") as f:
            f.write("DRY-RUN MAPPING RESULTS\n")
            f.write("=" * 65 + "\n")
            f.write("Review these mappings before running the full pass.\n\n")
            f.write("Checklist:\n")
            f.write("  1. Are seed maps correct? (no wrong-subject mappings)\n")
            f.write("  2. Are self-maps genuinely distinct one-offs?\n")
            f.write("  3. Are invented topics clean 2-5 word noun phrases?\n")
            f.write("  4. Is invention rate reasonable (< 30% of batch)?\n")
            f.write("  5. Are any validation issues logged above?\n")
            f.write("=" * 65 + "\n")
            f.write("\n".join(dryrun_lines))
        print(f"\n  Dry-run results written to: {dryrun_output_path}")
        print("\n  Review the file above before running --step 2 without --dry-run.")

    return dict(cluster_map), all_domain_stats


# ── New 1: Consistency check ──────────────────────────────────────────────────

def jaccard(a: str, b: str) -> float:
    ta = set(re.findall(r"[a-z0-9]+", a.lower()))
    tb = set(re.findall(r"[a-z0-9]+", b.lower()))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def run_consistency_check(map_path: str, vocab_path: str, threshold: float,
                          output_path: str):
    """
    Scan all invented topics (topics not in the original seed vocabulary) per domain.
    Flag pairs with Jaccard >= threshold as potential near-duplicates.
    """
    if not os.path.exists(map_path):
        print(f"  ERROR: {map_path} not found. Run step 2 first.")
        return
    if not os.path.exists(vocab_path):
        print(f"  ERROR: {vocab_path} not found.")
        return

    with open(map_path, encoding="utf-8") as f:
        cluster_map = json.load(f)
    with open(vocab_path, encoding="utf-8") as f:
        vocab = json.load(f)

    lines = ["CONSISTENCY CHECK -- NEAR-DUPLICATE INVENTED TOPICS",
             "=" * 65,
             f"Jaccard threshold: {threshold}",
             "These pairs were invented independently and may be duplicates.",
             "Review and consider merging in topic_cluster_map.json before",
             "running apply_topic_map.py.",
             "=" * 65, ""]

    total_flagged = 0

    for domain, domain_map in cluster_map.items():
        seed_set = set(vocab.get(domain, []))
        # Collect all canonical topics that appeared in this domain's mappings
        all_canonicals = set(domain_map.values())
        # Invented = in the final map but not in the seed vocabulary
        invented = sorted(all_canonicals - seed_set)

        if len(invented) < 2:
            continue

        pairs = []
        for i, a in enumerate(invented):
            for b in invented[i+1:]:
                j = jaccard(a, b)
                if j >= threshold:
                    pairs.append((j, a, b))

        if not pairs:
            continue

        pairs.sort(reverse=True)
        lines.append(f"\n{domain} -- {len(pairs)} potential duplicate pair(s):")
        for j, a, b in pairs:
            lines.append(f"  [{j:.2f}]  \"{a}\"  <->  \"{b}\"")
            total_flagged += 1

    lines.append(f"\nTotal flagged pairs: {total_flagged}")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"\n  Consistency check complete. {total_flagged} near-duplicate pair(s) flagged.")
    print(f"  Results written to: {output_path}")
    if total_flagged > 0:
        print("  Review and resolve before running apply_topic_map.py.")


# ── New 3: Review file ────────────────────────────────────────────────────────

def run_review_map(map_path: str, input_path: str, cfg: dict, output_path: str):
    """
    Generate a human-readable review file: every canonical topic with its
    frequency, the top-5 raw labels that mapped to it, and the top-5 invented
    topics per domain sorted by frequency.

    This is the precision audit artifact for Serina -- lets her spot-check
    whether a canonical topic accurately describes its assigned posts.
    """
    if not os.path.exists(map_path):
        print(f"  ERROR: {map_path} not found. Run step 2 first.")
        return

    with open(map_path, encoding="utf-8") as f:
        cluster_map = json.load(f)

    posts = load_corpus(input_path)
    lines = ["CANONICAL TOPIC MAPPING REVIEW",
             "=" * 72,
             "For each canonical topic: frequency and top-5 raw labels.",
             "Use this to spot-check precision before apply_topic_map.py.",
             "=" * 72]

    for domain in cfg["domains"]:
        domain_map = cluster_map.get(domain, {})
        if not domain_map:
            continue

        freq = get_label_freq(posts, domain)

        # Build canonical -> {raw_labels, post_count}
        canon_info: dict[str, dict] = defaultdict(lambda: {"labels": [], "posts": 0})
        for raw_label, canonical in domain_map.items():
            canon_info[canonical]["labels"].append(raw_label)
            canon_info[canonical]["posts"] += freq.get(raw_label, 0)

        sorted_canonicals = sorted(
            canon_info.items(), key=lambda x: -x[1]["posts"]
        )

        total_posts = sum(v["posts"] for v in canon_info.values())
        lines.append(f"\n{'='*72}")
        lines.append(f"DOMAIN: {domain}  "
                     f"({len(sorted_canonicals)} canonical topics, "
                     f"{total_posts:,} posts)")
        lines.append(f"{'='*72}")

        for canon, info in sorted_canonicals:
            n_posts = info["posts"]
            n_labels = len(info["labels"])
            pct = n_posts / total_posts * 100 if total_posts else 0
            lines.append(f"\n  {canon:<50} {n_posts:>5} posts ({pct:.1f}%)  "
                         f"[{n_labels} raw labels]")
            # Top 5 raw labels by frequency
            top_labels = sorted(info["labels"],
                                key=lambda l: freq.get(l, 0), reverse=True)[:5]
            for lbl in top_labels:
                f_count = freq.get(lbl, 0)
                marker = " [self-map]" if lbl == canon else ""
                lines.append(f"    <- {lbl} ({f_count}){marker}")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"\n  Review file written to: {output_path}")
    print("  Share with Serina for precision spot-check.")


# ── Summary ───────────────────────────────────────────────────────────────────

def print_summary(posts, vocab, cluster_map, cfg, all_domain_stats=None):
    print()
    print("=" * 72)
    print("NORMALISATION SUMMARY")
    print("=" * 72)
    print()
    for domain in cfg["domains"]:
        freq = get_label_freq(posts, domain)
        if not freq:
            continue
        total_posts  = sum(freq.values())
        total_labels = len(freq)
        domain_map   = cluster_map.get(domain, {})

        canonical_freq = defaultdict(int)
        for raw_label, canon in domain_map.items():
            canonical_freq[canon] += freq.get(raw_label, 0)

        covered_posts = sum(f for f in canonical_freq.values() if f >= 2)
        coverage_pct  = covered_posts / total_posts * 100 if total_posts else 0
        n_canonical   = len(canonical_freq)
        n_10plus      = sum(1 for f in canonical_freq.values() if f >= 10)
        n_30plus      = sum(1 for f in canonical_freq.values() if f >= 30)

        print(f"  {domain}")
        print(f"    Posts:            {total_posts:,}")
        print(f"    Raw labels:       {total_labels:,}")
        print(f"    Mapped labels:    {len(domain_map):,}")
        print(f"    Canonical topics: {n_canonical:,}  "
              f"(>= 10 posts: {n_10plus}  |  >= 30 posts: {n_30plus})")
        print(f"    Coverage (freq>=2 canonical): "
              f"{covered_posts:,} posts  ({coverage_pct:.1f}%)")
        if all_domain_stats and domain in all_domain_stats:
            print(all_domain_stats[domain].summary_line(total_labels))
        top10 = sorted(canonical_freq.items(), key=lambda x: -x[1])[:10]
        print(f"    Top canonical topics:")
        for canon, f in top10:
            print(f"      {canon:<45} {f:>5}")
        print()
    print("=" * 72)


# ── Main ──────────────────────────────────────────────────────────────────────

def run(cfg_path: str = "config_vocab.yaml", step: int = 0, dry_run: bool = False):
    cfg  = load_config(cfg_path)
    HERE = os.path.dirname(os.path.abspath(__file__))

    INPUT_PATH       = resolve(HERE, cfg["input_path"])
    VOCAB_OUTPUT     = resolve(HERE, cfg["vocab_output"])
    MAP_OUTPUT       = resolve(HERE, cfg["map_output"])
    CHECKPOINT_SEED  = resolve(HERE, cfg["checkpoint_seed"])
    CHECKPOINT_MAP   = resolve(HERE, cfg["checkpoint_map"])
    LOG_PATH         = resolve(HERE, cfg["log_path"])
    DRYRUN_OUTPUT    = resolve(HERE, cfg.get("dryrun_output", "mapping_dryrun_results.txt"))

    logger = setup_logger(LOG_PATH)

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        print("ERROR: OPENAI_API_KEY not set.")
        print("  PowerShell: $env:OPENAI_API_KEY=\"sk-proj-...\"")
        raise SystemExit(1)

    client = OpenAI(api_key=api_key)
    print(f"Loading {INPUT_PATH} ...")
    posts = load_corpus(INPUT_PATH)
    print(f"Loaded {len(posts):,} posts.\n")
    run_start = time.time()

    if step in (0, 1):
        print("=" * 72)
        print("STEP 1 -- SEED PASS")
        print("=" * 72)
        vocab = run_seed_pass(posts, cfg, client, logger, CHECKPOINT_SEED)
        with open(VOCAB_OUTPUT, "w", encoding="utf-8") as f:
            json.dump(vocab, f, ensure_ascii=False, indent=2)
        print(f"\n  Canonical vocabulary written to: {VOCAB_OUTPUT}")
        for domain, topics in vocab.items():
            print(f"    {domain:<35} {len(topics):>4} topics")
        print()

    if step in (0, 2):
        if step == 2:
            if not os.path.exists(VOCAB_OUTPUT):
                print(f"ERROR: {VOCAB_OUTPUT} not found. Run step 1 first.")
                raise SystemExit(1)
            with open(VOCAB_OUTPUT, encoding="utf-8") as f:
                vocab = json.load(f)
            print(f"Loaded canonical vocabulary from {VOCAB_OUTPUT}.")

        mode_label = "DRY-RUN" if dry_run else "MAPPING PASS"
        print("=" * 72)
        print(f"STEP 2 -- {mode_label}")
        print("=" * 72)
        cluster_map, all_domain_stats = run_mapping_pass(
            posts, vocab, cfg, client, logger, CHECKPOINT_MAP,
            dry_run=dry_run,
            dryrun_output_path=DRYRUN_OUTPUT if dry_run else None,
        )

        if not dry_run:
            with open(MAP_OUTPUT, "w", encoding="utf-8") as f:
                json.dump(cluster_map, f, ensure_ascii=False, indent=2)
            with open(VOCAB_OUTPUT, "w", encoding="utf-8") as f:
                json.dump(vocab, f, ensure_ascii=False, indent=2)
            print(f"\n  Cluster map written to: {MAP_OUTPUT}")
            print(f"  Updated vocabulary written to: {VOCAB_OUTPUT}")

            print_summary(posts, vocab, cluster_map, cfg, all_domain_stats)

    print(f"\nTotal elapsed: {time.time()-run_start:.0f}s")
    logger.info(f"RUN_END elapsed={time.time()-run_start:.0f}s "
                f"step={step} dry_run={dry_run}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config_vocab.yaml")
    parser.add_argument("--step", type=int, default=0,
                        help="1=seed only, 2=mapping only, 0=both (default)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run one batch per domain and stop. "
                             "Prints full input/output for review. "
                             "Use with --step 2.")
    parser.add_argument("--reset-domains", nargs="+", metavar="DOMAIN",
                        help="Remove these domains from the seed checkpoint before running.")
    parser.add_argument("--check-map", action="store_true",
                        help="Run consistency check on topic_cluster_map.json. "
                             "Flags near-duplicate invented topics. No API calls.")
    parser.add_argument("--review-map", action="store_true",
                        help="Generate mapping_review.txt for precision audit. "
                             "No API calls.")
    args = parser.parse_args()

    cfg = load_config(args.config)
    HERE = os.path.dirname(os.path.abspath(__file__))

    if args.reset_domains:
        ckpt_path = resolve(HERE, cfg["checkpoint_seed"])
        if os.path.exists(ckpt_path):
            with open(ckpt_path, encoding="utf-8") as f:
                ckpt = json.load(f)
            removed = [d for d in args.reset_domains if d in ckpt]
            for d in removed:
                del ckpt[d]
            with open(ckpt_path, "w", encoding="utf-8") as f:
                json.dump(ckpt, f, ensure_ascii=False, indent=2)
            print(f"  Reset checkpoint for: {removed}")
            print(f"  Kept in checkpoint:   {list(ckpt.keys())}")
        else:
            print(f"  No checkpoint found at {ckpt_path} -- nothing to reset.")

    if args.check_map:
        map_path    = resolve(HERE, cfg["map_output"])
        vocab_path  = resolve(HERE, cfg["vocab_output"])
        threshold   = cfg.get("consistency_jaccard_threshold", 0.6)
        output_path = resolve(HERE, "consistency_check_results.txt")
        run_consistency_check(map_path, vocab_path, threshold, output_path)

    elif args.review_map:
        map_path    = resolve(HERE, cfg["map_output"])
        input_path  = resolve(HERE, cfg["input_path"])
        output_path = resolve(HERE, cfg.get("review_output", "mapping_review.txt"))
        run_review_map(map_path, input_path, cfg, output_path)

    elif not args.check_map and not args.review_map:
        run(args.config, args.step, dry_run=args.dry_run)