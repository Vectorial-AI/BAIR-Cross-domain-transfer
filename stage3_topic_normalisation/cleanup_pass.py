"""
cleanup_pass.py
-----------------
Stages 1-3 of the topic vocabulary cleanup pass: candidate generation,
tiered LLM-checked routing, and an audited merge-proposal output.

Does NOT touch topic_cluster_map.json. Produces a proposed-merge file for
human review. Applying approved merges is a separate, deliberately small
script (apply_cleanup_merges.py) run only after review.

Why this shape (see prior diagnostics for full reasoning):
  - Greedy token-overlap clustering produces large groups (300+ labels)
    that chain together via single shared tokens and are NOT coherent
    single stimuli (verified by inspection of analyse_distinct_concepts.py
    output). These must never auto-merge regardless of group size.
  - Strong synonym pairs (Jaccard >= 0.5) are mostly correct but contain
    real false positives (e.g. "python performance optimization" matched
    to "postgresql performance optimization" on token overlap alone).
    No tier is exempt from review.
  - Simulated post-cleanup coverage numbers were unstable across runs
    (51% in one slice, 94% in another) because they were never actually
    audited. This script produces an audited number instead of a
    projected one.

Pipeline:
  STAGE 1: Candidate generation
    - Strong synonym candidates: singleton label vs existing canonical
      topic (within the same domain), Jaccard >= STRONG_THRESHOLD
    - Group candidates: singleton labels clustered against each other by
      shared meaningful tokens (greedy token-overlap, same domain only)

  STAGE 2: Tiered LLM-checked routing
    - Strong synonym pairs -> LLM yes/no: same stimulus?
    - Token-overlap groups <= MAX_GROUP_SIZE -> LLM yes/no: one coherent
      stimulus, or several jammed together?
    - Token-overlap groups > MAX_GROUP_SIZE -> re-split into smaller
      sub-groups using a stricter token-overlap threshold, then re-enter
      this stage recursively (capped at MAX_RESPLIT_DEPTH to avoid
      infinite recursion on a degenerate group)

  STAGE 3: Consolidated merge proposal
    - Every accepted merge written to proposed_merges.csv with full
      provenance: which stage/rule proposed it, the LLM's stated reasoning
    - Every rejected candidate also logged, for transparency

Usage:
    python cleanup_pass.py --domain "Software Engineering"
    python cleanup_pass.py --domain "Software Engineering" --dry-run
    python cleanup_pass.py --all-domains
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

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

csv.field_size_limit(10_000_000)

# ── Config ────────────────────────────────────────────────────────────────────

DEFAULTS = {
    "clean_input":       "tagged_posts_v2_clean.csv",
    "cluster_map":       "topic_cluster_map.json",
    "domains": [
        "Software Engineering", "AI", "Security", "EdTech", "Career",
        "Business & Strategy", "Data & Analytics",
        "Leadership & Management", "Open Source",
    ],
    "strong_threshold":  0.5,
    "weak_threshold":    0.3,
    "max_group_size":    9,      # groups above this size are re-split, never auto-passed
    "max_resplit_depth": 3,      # cap on recursive re-splitting of oversized groups
    "model":             "gpt-4.1-mini",
    "temperature":       0.0,
    "max_tokens_audit":  2000,
    "log_path":          "cleanup_pass.log",
    "batch_size":        20,     # LLM judgment calls per batch
}

STOPWORDS = {
    "a", "an", "the", "for", "of", "in", "on", "to", "and", "or", "with",
    "vs", "via", "using", "use", "uses", "new", "best", "top",
}

def load_config(path):
    cfg = DEFAULTS.copy()
    config_path = Path(os.path.dirname(os.path.abspath(__file__))) / path
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            overrides = yaml.safe_load(f) or {}
        cfg.update(overrides)
    return cfg

def resolve(here, p):
    return os.path.join(here, p)

def setup_logger(log_path):
    logger = logging.getLogger("cleanup_pass")
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

SHORT_MEANINGFUL_TOKENS = {"ai", "ml", "ux", "ui", "qa", "vc", "ar", "vr", "os", "ci", "cd"}

def tokenize(label):
    """Meaningful tokens only -- lowercase words, stopwords removed.
    Short domain-meaningful tokens (ai, ml, ux, etc.) are kept even though
    they're under the general length filter -- stripping them was found to
    silently change match results (e.g. 'ai engineering tools' vs
    'ai developer tools' loses its strongest shared signal if 'ai' is cut)."""
    words = re.findall(r"[a-z0-9]+", label.lower())
    return set(
        w for w in words
        if w not in STOPWORDS and (len(w) > 2 or w in SHORT_MEANINGFUL_TOKENS)
    )

def jaccard(set_a, set_b):
    if not set_a or not set_b:
        return 0.0
    inter = len(set_a & set_b)
    union = len(set_a | set_b)
    return inter / union if union else 0.0


# ── Stage 1: candidate generation ───────────────────────────────────────────

def generate_strong_synonym_candidates(singleton_labels, canonical_topics, threshold):
    """
    For each singleton label, find the best-matching EXISTING canonical topic
    by Jaccard token overlap. Returns list of (singleton, best_match, score).
    Only the single best match above threshold is kept per singleton --
    this is a candidate list for review, not an instruction to merge.

    Excludes trivial self-matches where the singleton label is itself
    identical to a canonical value already in the domain (J=1.0 against
    itself). This is not a merge candidate -- it's the same string appearing
    on both sides of the comparison, and was found to dominate candidate
    volume at scale (the large majority of a 975-candidate SW Eng sample
    were exactly this), drowning out genuine candidates.
    """
    canonical_tokens = {c: tokenize(c) for c in canonical_topics}
    candidates = []
    for label in singleton_labels:
        if label in canonical_topics:
            continue  # the label IS a canonical value elsewhere -- not a merge candidate
        label_tokens = tokenize(label)
        if not label_tokens:
            continue
        best_match, best_score = None, 0.0
        for canon, canon_tokens in canonical_tokens.items():
            if canon == label:
                continue  # redundant given the check above, kept for clarity/safety
            score = jaccard(label_tokens, canon_tokens)
            if score > best_score:
                best_match, best_score = canon, score
        if best_match and best_score >= threshold:
            candidates.append((label, best_match, round(best_score, 3)))
    return candidates


def generate_token_overlap_groups(labels, min_shared_tokens=1):
    """
    Greedy token-overlap clustering among a set of labels that have NOT
    been claimed by the strong/weak synonym pass. Returns list of groups
    (each a list of labels). This reproduces the existing project logic
    deliberately -- the fix is in how groups get TREATED downstream
    (size-gated + LLM-checked), not in how they get formed here.
    """
    remaining = list(labels)
    label_tokens = {l: tokenize(l) for l in remaining}
    groups = []
    used = set()

    for label in remaining:
        if label in used:
            continue
        this_group = [label]
        used.add(label)
        this_tokens = set(label_tokens[label])
        # Greedily absorb any unused label sharing >= min_shared_tokens tokens
        changed = True
        while changed:
            changed = False
            for other in remaining:
                if other in used:
                    continue
                shared = this_tokens & label_tokens[other]
                if len(shared) >= min_shared_tokens:
                    this_group.append(other)
                    used.add(other)
                    this_tokens |= label_tokens[other]
                    changed = True
        groups.append(this_group)

    return [g for g in groups if len(g) >= 2]


def resplit_oversized_group(group, depth, max_depth, min_shared_tokens):
    """
    Re-split an oversized group using a STRICTER token-overlap requirement.
    Recurses up to max_depth times. If a sub-group is still oversized at
    max_depth, it is returned as-is, flagged for manual review rather than
    silently auto-passed or silently dropped.
    """
    if depth >= max_depth:
        return [("MANUAL_REVIEW_REQUIRED", group)]

    stricter_min = min_shared_tokens + 1
    sub_groups = generate_token_overlap_groups(group, min_shared_tokens=stricter_min)

    # Labels not absorbed into any sub-group of size >= 2 are singletons now
    grouped_labels = set(l for g in sub_groups for l in g)
    leftovers = [l for l in group if l not in grouped_labels]

    results = []
    for sg in sub_groups:
        if len(sg) <= DEFAULTS["max_group_size"]:
            results.append(("OK", sg))
        else:
            results.extend(resplit_oversized_group(sg, depth + 1, max_depth, stricter_min))

    for l in leftovers:
        results.append(("SINGLETON_NO_GROUP", [l]))

    return results


# ── Stage 2: LLM-checked routing ────────────────────────────────────────────

SYNONYM_CHECK_SYSTEM = """You are auditing proposed topic merges for a behavioural research
study's topic normalisation pipeline. The topic field captures the STIMULUS of a post:
the underlying subject or event that caused the person to write, not the author's
angle, evaluation, or specific facet of it.

For each pair, decide: would a post about LABEL and a post about CANONICAL_TOPIC be
responding to the same underlying thing, just described at a different level of
specificity or from a different angle? If yes, APPROVE the merge. Judge by meaning,
never by shared words alone.

CRITICAL -- a narrower label is STILL the same stimulus as its broader parent, and
should be APPROVED, when the narrower label is simply a specific facet, sub-case,
angle, or qualifier of the same underlying subject:
  "browser automation tools" -> "automation tools"            APPROVE (browser is a facet of automation tooling, same subject)
  "ab testing design patterns" -> "design patterns"            APPROVE (ab testing is a specific kind of design pattern usage)
  "aws lambda functions" -> "aws lambda usage"                 APPROVE (functions are a facet of general usage, same subject)
  "api development challenges" -> "api development"            APPROVE (challenges is an angle on the same subject)
This is the same principle already used upstream in this pipeline's own mapping pass:
"postgresql performance optimization" collapses into "postgresql performance" because
optimization is the author's angle, not a different subject. Apply that same standard
here. Do not reject a merge just because one label is more specific than the other --
that is normal and expected. Only reject when the SUBJECT itself differs.

REJECT only when the underlying subject -- the technology, product, service, sector,
or named thing -- genuinely differs, even if the labels share words:
  "python performance optimization" -> "postgresql performance optimization"   REJECT (different technology)
  "auth service scaling" -> "notification service scaling"                     REJECT (different service)
  "api version updates" -> "node.js version updates"                           REJECT (different scope: API contract vs platform version)
  "c++ project configuration issues" -> "eslint configuration issues"          REJECT (different technology)

The test is always: does the SUBJECT match? Specificity, angle, and framing
differences are expected and should be approved. Subject mismatches, even with
heavy word overlap, should be rejected.

Return ONLY a JSON object mapping each pair's index (as a string) to an object with
"verdict" ("YES" or "NO") and "reason" (a brief explanation). No preamble, no markdown.

Example output:
{"0": {"verdict": "YES", "reason": "both describe the same underlying tooling concern"},
 "1": {"verdict": "NO", "reason": "different technologies sharing only a generic phrase"}}"""

GROUP_CHECK_SYSTEM = """You are auditing a proposed group of topic labels for a behavioural
research study's topic normalisation pipeline. These labels were grouped together because
they share some common words, but shared words do not always mean shared meaning.

For the group given, decide: do ALL of these labels concern the same underlying SUBJECT
(the technology, product, service, sector, or named thing), even if they describe
different facets, activities, or angles on that subject? If yes, APPROVE (verdict YES).

CRITICAL -- different facets or activities concerning the SAME subject are still one
coherent stimulus and should be APPROVED:
  "open source project feedback" + "open source project maintenance"   -> SAME subject
    (an open source project), different activities. APPROVE.
  "linux package conflicts" + "linux package managers"                 -> SAME subject
    (linux package management), different facets. APPROVE.
Do not reject a group just because labels describe different activities, angles, or
facets -- that is expected and is exactly what this consolidation step is for.

Only reject (verdict NO) when the labels concern genuinely DIFFERENT subjects that
happen to share a token, with no real shared underlying thing:
  "c10k problem" + "hibernate n+1 problem"   -> DIFFERENT subjects (different named
    technical problems in different domains), sharing only the word "problem". REJECT.
If rejecting, identify which specific labels are the outliers that don't share the
group's common subject -- not necessarily all of them.

Return ONLY a JSON object with "verdict" ("YES" or "NO"), "reason" (brief explanation),
and "outliers" (a list of the exact outlier label strings, empty list if verdict is YES).
No preamble, no markdown.

Example output:
{"verdict": "NO", "reason": "most labels concern home automation, but two concern unrelated home audio/NAS setup", "outliers": ["home audio setup", "home nas setup"]}"""


def call_llm(system_prompt, user_message, client, cfg, max_tokens, logger, tag=""):
    """Mirrors build_topic_vocabulary.py's call_llm exactly -- same client,
    same streaming pattern, same logging convention."""
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


def safe_json_parse(raw):
    """Identical to build_topic_vocabulary.py's safe_json_parse."""
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
    """Identical to build_topic_vocabulary.py's safe_call_with_retry."""
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


def audit_synonym_candidates(candidates, client, cfg, logger, dry_run):
    """
    Sends strong-synonym candidate pairs to the LLM for a yes/no check.
    Returns (approved, rejected) -- each a list of (label, canonical, score, reason).
    """
    if dry_run:
        print(f"  [DRY RUN] {len(candidates)} synonym candidates generated, no LLM calls made.")
        pending = [(label, canon, score, "PENDING_LLM_REVIEW -- dry run, not yet audited")
                   for label, canon, score in candidates]
        return pending, []

    approved, rejected = [], []
    batch_size = cfg["batch_size"]
    for i in range(0, len(candidates), batch_size):
        batch = candidates[i:i + batch_size]
        pairs_block = "\n".join(
            f'{j}. LABEL: "{label}"  CANONICAL_TOPIC: "{canon}"'
            for j, (label, canon, score) in enumerate(batch)
        )
        result = safe_call_with_retry(
            SYNONYM_CHECK_SYSTEM, pairs_block, client, cfg,
            cfg["max_tokens_audit"], logger, tag=f"synonym_audit:{i}",
        )
        if result is None or not isinstance(result, dict):
            logger.error(f"SYNONYM_AUDIT_BATCH_FAILED batch_start={i} -- "
                         f"all candidates in this batch left UNAUDITED, not approved")
            for label, canon, score in batch:
                rejected.append((label, canon, score,
                                  "AUDIT_FAILED -- LLM call failed, treated as rejected "
                                  "out of caution, not silently approved"))
            continue

        for idx_str, verdict_obj in result.items():
            try:
                idx = int(idx_str)
            except (ValueError, TypeError):
                continue
            if idx < 0 or idx >= len(batch):
                continue
            if not isinstance(verdict_obj, dict):
                continue
            label, canon, score = batch[idx]
            verdict = str(verdict_obj.get("verdict", "")).strip().upper()
            reason = str(verdict_obj.get("reason", "")).strip()
            if verdict == "YES":
                approved.append((label, canon, score, reason))
            else:
                rejected.append((label, canon, score, reason))
        time.sleep(0.3)

    return approved, rejected


def audit_group_candidates(groups, client, cfg, logger, dry_run):
    """
    Sends token-overlap groups (already size-gated to <= max_group_size)
    to the LLM for a coherence check. Returns (approved, rejected).
    approved: list of (group_labels, reason)
    rejected: list of (group_labels, reason, outlier_labels)
    """
    if dry_run:
        print(f"  [DRY RUN] {len(groups)} group candidates generated, no LLM calls made.")
        pending = [(group, "PENDING_LLM_REVIEW -- dry run, not yet audited") for group in groups]
        return pending, []

    approved, rejected = [], []
    for gi, group in enumerate(groups):
        labels_block = "\n".join(f"  - {l}" for l in group)
        result = safe_call_with_retry(
            GROUP_CHECK_SYSTEM, labels_block, client, cfg,
            cfg["max_tokens_audit"], logger, tag=f"group_audit:{gi}",
        )
        if result is None or not isinstance(result, dict):
            logger.error(f"GROUP_AUDIT_FAILED group_index={gi} -- "
                         f"left UNAUDITED, treated as rejected out of caution")
            rejected.append((group, "AUDIT_FAILED -- LLM call failed, treated as "
                                     "rejected out of caution, not silently approved", []))
            continue

        verdict = str(result.get("verdict", "")).strip().upper()
        reason = str(result.get("reason", "")).strip()
        outliers = result.get("outliers", [])
        if not isinstance(outliers, list):
            outliers = []
        if verdict == "YES":
            approved.append((group, reason))
        else:
            rejected.append((group, reason, [str(o) for o in outliers]))
        time.sleep(0.3)

    return approved, rejected
    return approved, rejected


# ── Main per-domain pipeline ─────────────────────────────────────────────────

def run_domain(domain, cfg, before_freq, cluster_map, client, logger, dry_run, sample_size=15):
    print(f"\n{'='*90}")
    print(f"DOMAIN: {domain}")
    print(f"{'='*90}")

    domain_map = cluster_map.get(domain, {})
    canonical_freq = defaultdict(int)
    for label, canon in domain_map.items():
        canonical_freq[canon] += before_freq.get((domain, label), 0)
    # Only non-singleton canonical topics are valid merge TARGETS. A singleton
    # label should never become the merge target for another singleton via
    # this strong-synonym pass -- that's what the token-overlap grouping
    # stage is for. Mixing the two stages was the bug caught in the smoke test.
    canonical_topics = {c for c, freq in canonical_freq.items() if freq >= 2}
    singleton_labels = [
        label for label, canon in domain_map.items()
        if label == canon and before_freq.get((domain, label), 0) == 1
    ]
    print(f"  Canonical topics: {len(canonical_topics):,}")
    print(f"  Singleton labels (candidates for cleanup): {len(singleton_labels):,}")

    # ── Stage 1a: strong synonym candidates ──────────────────────────────────
    strong_candidates = generate_strong_synonym_candidates(
        singleton_labels, canonical_topics, cfg["strong_threshold"]
    )
    print(f"\n  Stage 1a: {len(strong_candidates):,} strong synonym candidates "
          f"(Jaccard >= {cfg['strong_threshold']})")

    claimed = set(c[0] for c in strong_candidates)
    remaining_singletons = [l for l in singleton_labels if l not in claimed]

    # ── Stage 1b: token-overlap groups among remaining singletons ──────────
    raw_groups = generate_token_overlap_groups(remaining_singletons, min_shared_tokens=1)
    print(f"  Stage 1b: {len(raw_groups):,} token-overlap groups formed "
          f"from {len(remaining_singletons):,} remaining singletons")

    ok_groups, review_groups = [], []
    no_group_singletons = []
    for g in raw_groups:
        if len(g) <= cfg["max_group_size"]:
            ok_groups.append(g)
        else:
            split_results = resplit_oversized_group(g, depth=0, max_depth=cfg["max_resplit_depth"],
                                                      min_shared_tokens=1)
            for status, sub in split_results:
                if status == "OK":
                    ok_groups.append(sub)
                elif status == "MANUAL_REVIEW_REQUIRED":
                    review_groups.append(sub)
                elif status == "SINGLETON_NO_GROUP":
                    no_group_singletons.extend(sub)

    print(f"    -> {len(ok_groups):,} groups within size cap "
          f"(<= {cfg['max_group_size']} labels) after re-splitting")
    print(f"    -> {len(review_groups):,} groups still oversized after "
          f"{cfg['max_resplit_depth']} re-split attempts -- flagged for MANUAL review")
    print(f"    -> {len(no_group_singletons):,} labels had no valid grouping even after "
          f"re-splitting -- remain singletons, logged as NO_MATCH below")

    # ── Stage 2: LLM-checked routing ────────────────────────────────────────
    print(f"\n  Stage 2: auditing candidates via LLM "
          f"({'DRY RUN -- no calls made' if dry_run else 'live'})")

    syn_approved, syn_rejected = audit_synonym_candidates(
        strong_candidates, client, cfg, logger, dry_run
    )
    grp_approved, grp_rejected = audit_group_candidates(
        ok_groups, client, cfg, logger, dry_run
    )

    if not dry_run:
        print(f"    Synonym candidates: {len(syn_approved):,} approved, "
              f"{len(syn_rejected):,} rejected")
        print(f"    Group candidates: {len(grp_approved):,} approved, "
              f"{len(grp_rejected):,} rejected")
    else:
        sample_n = sample_size
        print(f"\n  Sample of synonym candidates (first {min(sample_n, len(syn_approved))} "
              f"of {len(syn_approved):,}, for quality spot-check):")
        for label, canon, score, _ in syn_approved[:sample_n]:
            print(f"    {label:<45} -> {canon:<40} [J={score}]")
        if len(syn_approved) > sample_n:
            print(f"    ... and {len(syn_approved) - sample_n:,} more (see proposed_merges.csv)")

        print(f"\n  Sample of group candidates (first {min(sample_n, len(grp_approved))} "
              f"of {len(grp_approved):,}, for quality spot-check):")
        for group, _ in grp_approved[:sample_n]:
            print(f"    [{len(group)}] {' | '.join(group)}")
        if len(grp_approved) > sample_n:
            print(f"    ... and {len(grp_approved) - sample_n:,} more (see proposed_merges.csv)")

    return {
        "domain": domain,
        "strong_candidates_total": len(strong_candidates),
        "syn_approved": syn_approved,
        "syn_rejected": syn_rejected,
        "grp_approved": grp_approved,
        "grp_rejected": grp_rejected,
        "manual_review_groups": review_groups,
        "no_group_singletons": no_group_singletons,
    }


def write_proposed_merges(results, out_path):
    file_exists = os.path.exists(out_path)
    existing_domains = set()
    if file_exists:
        with open(out_path, newline="", encoding="utf-8", errors="replace") as f:
            for row in csv.DictReader(f):
                existing_domains.add(row.get("domain", ""))

    # Append mode: running cleanup_pass.py per-domain in a loop must not
    # silently destroy prior domains' results. Real bug found: the original
    # "w" mode meant only the LAST domain run in a multi-domain loop survived
    # in the file, even though every domain's terminal output was correct.
    mode = "a" if file_exists else "w"
    with open(out_path, mode, newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["domain", "stage", "raw_label_or_group", "proposed_canonical",
                              "score_or_na", "verdict", "reason"])
        for r in results:
            domain = r["domain"]
            if domain in existing_domains:
                print(f"  WARNING: '{domain}' already has rows in {out_path}. "
                      f"Appending again will create duplicate rows for this domain. "
                      f"Delete the file first if you want a clean re-run of this domain.")
            for label, canon, score, reason in r["syn_approved"]:
                verdict = "PENDING_REVIEW" if "PENDING_LLM_REVIEW" in reason else "APPROVED"
                writer.writerow([domain, "synonym", label, canon, score, verdict, reason])
            for label, canon, score, reason in r["syn_rejected"]:
                writer.writerow([domain, "synonym", label, canon, score, "REJECTED", reason])
            for group, reason in r["grp_approved"]:
                survivor = max(group, key=len)  # longest label as placeholder survivor name;
                                                  # human should confirm/rename on review
                verdict = "PENDING_REVIEW" if "PENDING_LLM_REVIEW" in reason else "APPROVED"
                writer.writerow([domain, "group", " | ".join(group), survivor, "n/a",
                                  verdict, reason])
            for group, reason, outliers in r["grp_rejected"]:
                writer.writerow([domain, "group", " | ".join(group), "n/a", "n/a",
                                  "REJECTED", f"{reason} -- outliers: {'; '.join(outliers)}"])
            for group in r["manual_review_groups"]:
                writer.writerow([domain, "oversized_group", " | ".join(group[:10]) +
                                  (f" ... and {len(group)-10} more" if len(group) > 10 else ""),
                                  "n/a", "n/a", "MANUAL_REVIEW_REQUIRED",
                                  f"group of {len(group)} labels, exceeded size cap after re-splitting"])
            for label in r["no_group_singletons"]:
                writer.writerow([domain, "no_grouping_found", label, "n/a", "n/a",
                                  "NO_MATCH", "considered for grouping, no valid match found "
                                  "even after stricter re-split -- remains a singleton"])


def run(cfg_path="config_cleanup.yaml", domain_filter=None, dry_run=False, sample_size=15):
    cfg = load_config(cfg_path)
    HERE = os.path.dirname(os.path.abspath(__file__))

    logger = setup_logger(resolve(HERE, cfg["log_path"]))

    client = None
    if not dry_run:
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            print("ERROR: OPENAI_API_KEY not set.")
            print("  PowerShell: $env:OPENAI_API_KEY=\"sk-proj-...\"")
            raise SystemExit(1)
        client = OpenAI(api_key=api_key)

    print(f"Loading {cfg['clean_input']} ...")
    before_freq = defaultdict(int)
    with open(resolve(HERE, cfg["clean_input"]), newline="", encoding="utf-8", errors="replace") as f:
        for row in csv.DictReader(f):
            d = row.get("topic_broad", "")
            t = (row.get("topic") or "").strip().lower()
            if t:
                before_freq[(d, t)] += 1

    print(f"Loading {cfg['cluster_map']} ...")
    with open(resolve(HERE, cfg["cluster_map"]), encoding="utf-8") as f:
        cluster_map = json.load(f)

    domains_to_run = [domain_filter] if domain_filter else cfg["domains"]

    all_results = []
    for domain in domains_to_run:
        if domain not in cfg["domains"]:
            print(f"WARNING: '{domain}' not in configured domains, skipping.")
            continue
        result = run_domain(domain, cfg, before_freq, cluster_map, client, logger, dry_run, sample_size)
        all_results.append(result)

    out_path = resolve(HERE, "proposed_merges.csv")
    write_proposed_merges(all_results, out_path)
    print(f"\n{'='*90}")
    print(f"Written: {out_path}")
    print(f"Review every row before running apply_cleanup_merges.py")
    print(f"{'='*90}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config_cleanup.yaml")
    parser.add_argument("--domain", default=None, help="Run a single domain only")
    parser.add_argument("--all-domains", action="store_true")
    parser.add_argument("--dry-run", action="store_true",
                         help="Run Stage 1 candidate generation only, skip LLM calls")
    parser.add_argument("--sample-size", type=int, default=15,
                         help="How many candidates to print per domain for spot-checking (dry-run only)")
    args = parser.parse_args()
    run(args.config, domain_filter=args.domain, dry_run=args.dry_run, sample_size=args.sample_size)