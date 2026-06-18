"""
build_seeds_from_data.py
-------------------------
Derives canonical seed vocabulary directly from corpus data.
No LLM calls. Deterministic. Replaces the LLM seed pass entirely.

Method:
  For each domain, takes all freq>=2 labels and applies two-stage
  deduplication to produce the canonical seed vocabulary:

  Stage 1 -- Angle-modifier stripping
    Strips trailing angle modifiers (optimization, challenges, issues,
    best practices, etc.) from labels and groups variants together.
    The highest-frequency variant in each group becomes the canonical name.

  Stage 2 -- Word-order normalisation
    Groups labels with identical token sets regardless of word order.
    "microservices vs monolith" and "monolith vs microservices" are one topic.

  Stage 3 -- Jaccard deduplication
    Any remaining pair with Jaccard similarity >= JACCARD_THRESH is collapsed.
    The higher-frequency label wins.

Output: canonical_topics_v1.json  {domain -> [canonical_topic, ...]}

The result is a seed vocabulary that:
  - Is directly grounded in observed data (no LLM judgment)
  - Covers the full thematic breadth of each domain (all freq>=2 labels)
  - Has angle-modifier fragmentation removed
  - Is proportional in size to domain diversity

Downstream: build_topic_vocabulary.py --step 2 reads this file unchanged.

Usage:
    python build_seeds_from_data.py
    python build_seeds_from_data.py --config config_vocab.yaml

Author: Ananth / Vectorial AI -- June 2026
"""

import argparse
import csv
import json
import os
import re
import sys
import io
from collections import defaultdict
from pathlib import Path

import yaml

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

csv.field_size_limit(10_000_000)

# ── Defaults ──────────────────────────────────────────────────────────────────

DEFAULTS = {
    "input_path":          "tagged_posts_v2_clean.csv",
    "vocab_output":        "canonical_topics_v1.json",
    "seed_freq_threshold": 2,
    "jaccard_threshold":   0.75,   # collapse pairs above this similarity
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

# Trailing angle-modifier words to strip when normalising labels.
# These are evaluative lenses or process descriptors, not part of the stimulus.
ANGLE_MODIFIERS = {
    "optimization", "optimisation", "optimizations",
    "challenges", "challenge",
    "issues", "issue",
    "problems", "problem",
    "best", "practices", "practice",
    "strategies", "strategy",
    "improvements", "improvement",
    "benefits", "benefit",
    "techniques", "technique",
    "approaches", "approach",
    "trends", "trend",
    "risks", "risk",
    "concerns", "concern",
    "insights", "insight",
    "tips", "tip",
    "lessons", "lesson",
    "overview", "review",
    "analysis", "comparison",
    "guide", "tutorial",
    "basics", "fundamentals",
    "introduction", "intro",
    "discussion", "debate",
    "updates", "update",
    "news", "announcements", "announcement",
    "management", "solutions", "solution",
    "use", "cases", "case",
    "examples", "example",
    "resources", "resource",
    "tools",       # only as trailing modifier, not when it IS the subject
    "tooling",
    "methods", "methodology",
    "framework", "frameworks",
    "patterns", "pattern",
    "design",      # as trailing modifier
    "process", "processes",
    "workflow", "workflows",
    "implementation", "implementations",
    "adoption",    # as trailing modifier (not "ai adoption" which is a subject)
    "impact",      # as trailing modifier
    "evolution",
    "future",
    "history",
    "recap",
    "summary",
}

# Stop words: tokens too generic to anchor a thematic group
STOP_WORDS = {
    "and", "or", "in", "of", "the", "a", "an", "for", "to", "with",
    "on", "at", "by", "from", "as", "into", "about", "is", "are",
    "its", "their", "our", "this", "that", "vs", "via", "per",
}

# ── Config ────────────────────────────────────────────────────────────────────

def load_config(path: str) -> dict:
    cfg = DEFAULTS.copy()
    config_path = Path(os.path.dirname(os.path.abspath(__file__))) / path
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            overrides = yaml.safe_load(f) or {}
        cfg.update(overrides)
    else:
        print(f"  No config found at {config_path} -- using defaults.")
    return cfg

def resolve(here: str, p: str) -> str:
    return os.path.join(here, p)

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

# ── Deduplication helpers ─────────────────────────────────────────────────────

def tokenise(label: str) -> list[str]:
    return re.findall(r"[a-z0-9]+(?:\.[a-z0-9]+)*", label.lower())

def meaningful_tokens(label: str) -> frozenset:
    return frozenset(t for t in tokenise(label)
                     if t not in STOP_WORDS and len(t) > 1)

def jaccard(a: str, b: str) -> float:
    ta = meaningful_tokens(a)
    tb = meaningful_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)

def strip_angle_modifiers(label: str) -> str:
    """
    Strip trailing angle-modifier tokens from a label.
    Only strips from the END of the label, left to right.
    Stops as soon as a non-modifier token is encountered.
    Preserves the subject.

    "postgresql performance optimization" -> "postgresql performance"
    "api design best practices" -> "api design"
    "ai coding tools" -> "ai coding tools"  (tools is the subject here,
                                              but this is handled by the
                                              context: only trailing)
    """
    tokens = tokenise(label)
    # Strip from right while the rightmost token is an angle modifier
    while tokens and tokens[-1] in ANGLE_MODIFIERS:
        tokens = tokens[:-1]
    return " ".join(tokens) if tokens else label

def normalise_token_order(label: str) -> frozenset:
    """Return frozenset of meaningful tokens for order-invariant comparison."""
    return meaningful_tokens(label)

# ── Core deduplication ────────────────────────────────────────────────────────

def deduplicate_labels(freq: dict[str, int],
                       jaccard_thresh: float) -> dict[str, str]:
    """
    Given {label: freq}, return {label: canonical_label} mapping.
    Canonical label is the highest-frequency representative of each group.

    Three passes:
      1. Angle-modifier stripping -- group by stripped root
      2. Word-order normalisation -- group by token set
      3. Jaccard similarity -- collapse near-identical pairs
    """
    # Working set: label -> current canonical (starts as identity)
    canonical: dict[str, str] = {l: l for l in freq}

    # ── Pass 1: Angle-modifier stripping ─────────────────────────────────
    # Group labels by their stripped root
    stripped_groups: dict[str, list[str]] = defaultdict(list)
    for label in freq:
        root = strip_angle_modifiers(label)
        if root:
            stripped_groups[root].append(label)

    for root, group in stripped_groups.items():
        if len(group) < 2:
            continue
        # Winner: highest frequency; tie-break: shortest label (cleanest name)
        winner = max(group, key=lambda l: (freq[l], -len(l)))
        for label in group:
            canonical[label] = canonical[winner]

    # ── Pass 2: Word-order normalisation ─────────────────────────────────
    # Group labels by their token set (order-invariant)
    token_set_groups: dict[frozenset, list[str]] = defaultdict(list)
    for label in freq:
        ts = normalise_token_order(label)
        if ts:
            token_set_groups[ts].append(label)

    for ts, group in token_set_groups.items():
        if len(group) < 2:
            continue
        winner = max(group, key=lambda l: (freq[l], -len(l)))
        winner_canon = canonical[winner]
        for label in group:
            canonical[label] = winner_canon

    # ── Pass 3: Jaccard similarity ────────────────────────────────────────
    # After passes 1+2, work on the remaining distinct canonical topics
    # Collect unique canonicals and their combined frequencies
    canon_freq: dict[str, int] = defaultdict(int)
    canon_members: dict[str, list[str]] = defaultdict(list)
    for label, canon in canonical.items():
        canon_freq[canon] += freq[label]
        canon_members[canon].append(label)

    # Sort by frequency descending so high-freq topics dominate
    canons = sorted(canon_freq.keys(), key=lambda c: -canon_freq[c])

    # Greedy collapse: for each pair, if Jaccard >= threshold, lower-freq
    # maps to higher-freq
    merged: dict[str, str] = {c: c for c in canons}

    for i, a in enumerate(canons):
        for b in canons[i+1:]:
            if merged[b] != b:
                continue   # already merged
            j = jaccard(a, b)
            if j >= jaccard_thresh:
                # a has higher freq (sorted), b merges into a
                merged[b] = merged[a]

    # Apply merged map back to original labels
    for label in canonical:
        canon = canonical[label]
        canonical[label] = merged.get(canon, canon)

    return canonical

# ── Build vocabulary ──────────────────────────────────────────────────────────

def build_vocab_for_domain(posts: list[dict], domain: str,
                            freq_threshold: int,
                            jaccard_thresh: float) -> tuple[list[str], dict]:
    """
    Returns (canonical_topic_list, label_to_canonical_map) for a domain.
    canonical_topic_list: sorted by combined frequency descending.
    label_to_canonical_map: {raw_label: canonical_topic} for freq>=2 labels.
    """
    freq = get_label_freq(posts, domain)
    seed_labels = {l: f for l, f in freq.items() if f >= freq_threshold}

    if not seed_labels:
        return [], {}

    canonical_map = deduplicate_labels(seed_labels, jaccard_thresh)

    # Build canonical -> combined frequency
    canon_freq: dict[str, int] = defaultdict(int)
    for label, canon in canonical_map.items():
        canon_freq[canon] += seed_labels[label]

    # Sort by frequency
    sorted_canonicals = sorted(canon_freq.keys(), key=lambda c: -canon_freq[c])

    return sorted_canonicals, canonical_map

# ── Main ──────────────────────────────────────────────────────────────────────

def run(cfg_path: str = "config_vocab.yaml"):
    cfg  = load_config(cfg_path)
    HERE = os.path.dirname(os.path.abspath(__file__))

    INPUT_PATH    = resolve(HERE, cfg["input_path"])
    VOCAB_OUTPUT  = resolve(HERE, cfg["vocab_output"])
    freq_thresh   = cfg["seed_freq_threshold"]
    jaccard_thresh = cfg.get("jaccard_threshold", 0.75)
    domains       = cfg["domains"]

    print(f"Loading {INPUT_PATH} ...")
    posts = load_corpus(INPUT_PATH)
    print(f"Loaded {len(posts):,} posts.\n")

    print("=" * 72)
    print("BUILDING SEED VOCABULARY FROM DATA")
    print(f"freq threshold: >= {freq_thresh}  |  Jaccard collapse: >= {jaccard_thresh}")
    print("=" * 72)
    print()

    vocab = {}
    full_maps = {}  # for reporting

    for domain in domains:
        freq = get_label_freq(posts, domain)
        total_labels = len(freq)
        seed_count   = sum(1 for f in freq.values() if f >= freq_thresh)

        if seed_count == 0:
            print(f"  {domain}: no freq>={freq_thresh} labels -- skipping.")
            vocab[domain] = []
            continue

        canonicals, label_map = build_vocab_for_domain(
            posts, domain, freq_thresh, jaccard_thresh
        )
        vocab[domain] = canonicals
        full_maps[domain] = label_map

        # Compute stats
        before = seed_count
        after  = len(canonicals)
        collapsed = before - after
        reduction = collapsed / before * 100 if before else 0

        print(f"  {domain}")
        print(f"    freq>={freq_thresh} labels:  {before:>5}")
        print(f"    canonical topics:    {after:>5}  "
              f"({collapsed} collapsed, {reduction:.1f}% reduction)")

        # Show top-10 by combined frequency for spot-check
        canon_freq: dict[str, int] = defaultdict(int)
        for label, canon in label_map.items():
            freq_val = freq.get(label, 0)
            canon_freq[canon] += freq_val
        top10 = sorted(canon_freq.items(), key=lambda x: -x[1])[:10]
        print(f"    Top 10 canonical topics (by combined freq):")
        for canon, f in top10:
            # Show how many raw labels collapsed into this canonical
            members = [l for l, c in label_map.items() if c == canon]
            print(f"      {canon:<50} freq={f:>3}  "
                  f"({len(members)} raw label{'s' if len(members)!=1 else ''})")
        print()

    # Write output
    with open(VOCAB_OUTPUT, "w", encoding="utf-8") as f:
        json.dump(vocab, f, ensure_ascii=False, indent=2)
    print(f"Canonical vocabulary written to: {VOCAB_OUTPUT}")
    print()
    print("Summary:")
    for domain, canonicals in vocab.items():
        if canonicals:
            freq = get_label_freq(posts, domain)
            seed_count = sum(1 for f in freq.values() if f >= freq_thresh)
            print(f"  {domain:<35} {len(canonicals):>5} topics  "
                  f"(from {seed_count} freq>={freq_thresh} labels)")
    print()
    print("Next step:")
    print("  python build_topic_vocabulary.py --step 2")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config_vocab.yaml")
    args = parser.parse_args()
    run(args.config)
