"""
analyse_distinct_concepts.py
-----------------------------
For each domain, takes the distinct concept singletons identified by
explore_topic_normalisation.py and analyses how they group thematically.

The goal: understand what an LLM identify-and-invent pass would actually
produce when it sees the full set of distinct concept singletons together,
not just individually.

The explore script showed these singletons by first-two-word prefix, which
undersells real groupings. This script uses shared meaningful token sets
to find groupings that cross prefix boundaries.

Three outputs per domain:
  1. Group size distribution -- how many groups of 3+, 5+, 10+ exist
  2. Cumulative coverage if groups of each size threshold were collapsed
  3. Realistic post-normalisation coverage estimate combining:
       - strong synonym collapses (Jaccard >= 0.5, from explore output)
       - weak synonym collapses  (Jaccard >= 0.3)
       - distinct concept group collapses (groups of 3+)
       - distinct concept group collapses (groups of 5+)

This gives the honest upper and lower bounds on what identify-and-invent
produces on the full corpus, not the Jaccard floor.

Paths follow the existing codebase convention.
"""

import csv
import json
import sys
import io
import os
import re
from collections import defaultdict
from pathlib import Path
from itertools import combinations

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

csv.field_size_limit(10_000_000)

# ── Config ─────────────────────────────────────────────────────────────────────

HERE   = Path(os.path.dirname(os.path.abspath(__file__)))
INPUT  = HERE / "tagged_posts_v2_clean.csv"

DOMAINS_OF_INTEREST = [
    "Software Engineering",
    "AI",
    "Security",
    "EdTech",
]

# Jaccard thresholds matching explore_topic_normalisation.py
STRONG_THRESH = 0.5
WEAK_THRESH   = 0.3

# Minimum group size to count as collapsible by LLM
GROUP_SIZE_THRESHOLDS = [3, 5, 10]

# Stop-words: tokens that are too generic to anchor a thematic group
STOP_WORDS = {
    "and", "or", "in", "of", "the", "a", "an", "for", "to", "with",
    "on", "at", "by", "from", "as", "into", "about", "is", "are",
    "its", "their", "our", "this", "that", "vs", "via", "per",
    "use", "using", "used", "uses", "based", "driven", "related",
    "best", "practices", "challenges", "strategies", "approaches",
    "trends", "issues", "impact", "overview", "discussion", "review",
    "analysis", "update", "updates", "adoption", "implementation",
    "management", "development", "tools", "tooling", "platform",
    "systems", "system", "process", "processes", "methods", "solutions",
    "solution", "features", "feature", "support", "integration",
    "benefits", "risks", "risk", "concerns", "insights",
}

# ── Helpers ────────────────────────────────────────────────────────────────────

def tokenise(label: str) -> set:
    """Split label into meaningful tokens, excluding stop-words."""
    tokens = re.findall(r"[a-z0-9]+(?:\.[a-z0-9]+)*", label.lower())
    return {t for t in tokens if t not in STOP_WORDS and len(t) > 2}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def load_corpus(path: Path):
    """Load tagged posts, return list of dicts."""
    posts = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            posts.append(row)
    return posts


def get_label_freq(posts, domain, field="topic"):
    """Return {label: freq} for a domain."""
    freq = defaultdict(int)
    for p in posts:
        if p.get("topic_broad") != domain:
            continue
        label = (p.get(field) or "").strip().lower()
        if label and label != "unclassified":
            freq[label] += 1
    return freq


def classify_singletons(freq):
    """
    Reproduce the singleton classifier from explore_topic_normalisation.py.
    Returns dict with keys: fixable_synonym, weak_synonym, named_specific,
    distinct_concept, noise -- each a list of labels.
    """
    singletons   = [l for l, f in freq.items() if f == 1]
    freq2_labels = {l: f for l, f in freq.items() if f >= 2}

    # Named-specific heuristic: contains a version number, named product
    # with dot notation, or proper-noun-like capitalised token in original
    def is_named_specific(label):
        if re.search(r'\b\d+\.\d+\b', label):
            return True
        parts = label.split()
        # Has a technology name (contains dot or known tech prefixes)
        tech_prefixes = {
            "node.js", "next.js", "react", "angular", "vue", "django",
            "laravel", "spring", "kubernetes", "docker", "aws", "azure",
            "gcp", "postgresql", "mongodb", "redis", "kafka", "graphql",
            "grpc", "golang", "rust", "swift", "kotlin", "java", "python",
            "ruby", "php", "scala", "clojure", "haskell", "elm",
        }
        label_lower = label.lower()
        return any(tp in label_lower for tp in tech_prefixes) and len(parts) <= 4

    classified = {
        "fixable_synonym":  [],
        "weak_synonym":     [],
        "named_specific":   [],
        "distinct_concept": [],
        "noise":            [],
    }

    label_tokens = {l: tokenise(l) for l in singletons}
    seed_tokens  = {l: tokenise(l) for l in freq2_labels}

    for label in singletons:
        ltoks = label_tokens[label]

        # Named specific check first
        if is_named_specific(label):
            classified["named_specific"].append(label)
            continue

        # Find best Jaccard match against freq>=2 seeds
        best_j = 0.0
        for seed, stoks in seed_tokens.items():
            j = jaccard(ltoks, stoks)
            if j > best_j:
                best_j = j

        if best_j >= STRONG_THRESH:
            classified["fixable_synonym"].append(label)
        elif best_j >= WEAK_THRESH:
            classified["weak_synonym"].append(label)
        else:
            classified["distinct_concept"].append(label)

    return classified, freq2_labels


def group_distinct_concepts(distinct_labels: list) -> list:
    """
    Group distinct concept singletons by shared meaningful token overlap.
    Uses a single-pass greedy clustering: each label joins the first
    existing group where it shares >= 1 meaningful token with the group
    centroid (union of all tokens in the group). If no match, starts a
    new group.

    Returns list of groups, each a list of labels, sorted by size desc.
    """
    groups = []         # list of {"tokens": set, "labels": list}

    label_tokens = [(l, tokenise(l)) for l in distinct_labels]

    for label, ltoks in label_tokens:
        if not ltoks:
            continue
        best_group = None
        best_overlap = 0
        for g in groups:
            overlap = len(ltoks & g["tokens"])
            if overlap > best_overlap:
                best_overlap = overlap
                best_group = g

        if best_group is not None and best_overlap >= 1:
            best_group["labels"].append(label)
            best_group["tokens"] |= ltoks
        else:
            groups.append({"tokens": ltoks.copy(), "labels": [label]})

    # Sort by size desc
    groups.sort(key=lambda g: len(g["labels"]), reverse=True)
    return groups


def cumulative_coverage(groups, size_thresh, total_posts):
    """
    If all groups of size >= size_thresh were collapsed to one canonical
    topic each, how many posts would be covered (since each singleton = 1 post)?
    """
    collapsed_posts = sum(
        len(g["labels"])
        for g in groups
        if len(g["labels"]) >= size_thresh
    )
    return collapsed_posts, collapsed_posts / total_posts * 100 if total_posts else 0


def print_section(title):
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


# ── Main ───────────────────────────────────────────────────────────────────────

def run():
    print(f"Loading {INPUT} ...")
    posts = load_corpus(INPUT)
    print(f"Loaded {len(posts):,} posts.\n")

    for domain in DOMAINS_OF_INTEREST:

        domain_posts = [p for p in posts if p.get("topic_broad") == domain]
        total_posts  = len(domain_posts)
        freq         = get_label_freq(posts, domain)
        unique       = len(freq)
        singletons_n = sum(1 for f in freq.values() if f == 1)

        print()
        print("#" * 72)
        print(f"# DOMAIN: {domain}")
        print(f"# {total_posts:,} posts  |  {unique:,} unique labels  |  "
              f"{singletons_n:,} singletons ({singletons_n/unique*100:.1f}%)")
        print("#" * 72)

        # ── 1. Classify singletons ─────────────────────────────────────────
        classified, freq2_labels = classify_singletons(freq)

        distinct = classified["distinct_concept"]
        fixable  = classified["fixable_synonym"]
        weak     = classified["weak_synonym"]
        named    = classified["named_specific"]

        print_section("1. SINGLETON CLASSIFICATION RECAP")
        print(f"  Fixable synonym  (Jaccard >= {STRONG_THRESH}): "
              f"{len(fixable):,} labels  ({len(fixable)/singletons_n*100:.1f}% of singletons)")
        print(f"  Weak synonym     (Jaccard >= {WEAK_THRESH}):  "
              f"{len(weak):,} labels  ({len(weak)/singletons_n*100:.1f}% of singletons)")
        print(f"  Named specific:  {len(named):,} labels  "
              f"({len(named)/singletons_n*100:.1f}% of singletons)")
        print(f"  Distinct concept:{len(distinct):,} labels  "
              f"({len(distinct)/singletons_n*100:.1f}% of singletons)")

        # ── 2. Group distinct concepts ─────────────────────────────────────
        print_section("2. DISTINCT CONCEPT THEMATIC GROUPINGS")
        print("  Greedy token-overlap clustering (>= 1 shared meaningful token)")
        print()

        groups = group_distinct_concepts(distinct)

        size_dist = defaultdict(int)
        for g in groups:
            size_dist[len(g["labels"])] += 1

        print(f"  Total groups formed: {len(groups)}")
        print()
        print(f"  {'Group size':<15} {'# groups':>10} {'# labels':>10} {'% of distinct':>15}")
        print(f"  {'-'*15} {'-'*10} {'-'*10} {'-'*15}")

        size_buckets = [1, 2, 3, 4, 5, 6, 9, 10, 19, 20]
        bucket_labels = ["1", "2", "3", "4", "5", "6-9", "10-19", "20+"]
        boundaries = [(1,1), (2,2), (3,3), (4,4), (5,5), (6,9), (10,19), (20,9999)]

        for (lo, hi), label in zip(boundaries, bucket_labels):
            grps_in = [g for g in groups if lo <= len(g["labels"]) <= hi]
            n_labels = sum(len(g["labels"]) for g in grps_in)
            pct = n_labels / len(distinct) * 100 if distinct else 0
            if grps_in:
                print(f"  {label:<15} {len(grps_in):>10,} {n_labels:>10,} {pct:>14.1f}%")

        # ── 3. Top groups ──────────────────────────────────────────────────
        print_section("3. TOP 30 THEMATIC GROUPS (by size)")
        print(f"  {'Group tokens (top 5)':<45} {'Size':>6}  {'Example labels'}")
        print(f"  {'-'*45} {'-'*6}  {'-'*40}")

        for g in groups[:30]:
            top_tokens = sorted(g["tokens"])[:5]
            token_str  = ", ".join(top_tokens)[:44]
            examples   = " | ".join(g["labels"][:3])[:60]
            print(f"  {token_str:<45} {len(g['labels']):>6}  {examples}")

        # ── 4. Cumulative coverage by group-size threshold ─────────────────
        print_section("4. CUMULATIVE COVERAGE IF DISTINCT GROUPS COLLAPSED")
        print("  (each singleton = 1 post; groups collapsed to 1 canonical topic)")
        print()

        already_covered = sum(f for f in freq2_labels.values())
        fixable_posts   = len(fixable)   # 1 post each
        weak_posts      = len(weak)      # 1 post each

        print(f"  Base (freq>=2 labels already):          "
              f"{already_covered:>5} posts  ({already_covered/total_posts*100:.1f}%)")
        print(f"  + strong synonym collapse:              "
              f"{fixable_posts:>5} posts  ({fixable_posts/total_posts*100:.1f}%)")
        print(f"  + weak synonym collapse:                "
              f"{weak_posts:>5} posts  ({weak_posts/total_posts*100:.1f}%)")
        print()

        cumulative_base = already_covered + fixable_posts + weak_posts

        for thresh in GROUP_SIZE_THRESHOLDS:
            collapsed, pct = cumulative_coverage(groups, thresh, total_posts)
            n_groups = sum(1 for g in groups if len(g["labels"]) >= thresh)
            total_covered = cumulative_base + collapsed
            total_pct     = total_covered / total_posts * 100
            print(f"  + distinct groups of size >= {thresh}:           "
                  f"{collapsed:>5} posts  ({collapsed/total_posts*100:.1f}%)  "
                  f"[{n_groups} groups]")
            print(f"    => Total coverage at this threshold:  "
                  f"{total_covered:>5} posts  ({total_pct:.1f}%)")
            print()

        # ── 5. Realistic LLM estimate vs Jaccard simulation ────────────────
        print_section("5. REALISTIC ESTIMATE: LLM vs JACCARD SIMULATION")

        # Jaccard simulation (from explore output): fixable + weak only
        jaccard_sim_coverage = (already_covered + fixable_posts + weak_posts) / total_posts * 100

        # Realistic LLM lower bound: + groups of 3+
        collapsed_3plus, _ = cumulative_coverage(groups, 3, total_posts)
        llm_lower = (cumulative_base + collapsed_3plus) / total_posts * 100

        # Realistic LLM upper bound: + groups of 2+
        collapsed_2plus, _ = cumulative_coverage(groups, 2, total_posts)
        llm_upper = (cumulative_base + collapsed_2plus) / total_posts * 100

        print(f"  Jaccard simulation coverage (floor):    {jaccard_sim_coverage:.1f}%")
        print(f"  LLM lower bound (groups >= 3):          {llm_lower:.1f}%")
        print(f"  LLM upper bound (groups >= 2):          {llm_upper:.1f}%")
        print()
        print(f"  Interpretation:")
        print(f"  The Jaccard simulation ({jaccard_sim_coverage:.1f}%) misses all the")
        print(f"  distinct concept singletons that share thematic tokens across")
        print(f"  prefix boundaries. The LLM will see these as a set and collapse")
        print(f"  them. Realistic post-normalisation coverage: "
              f"{llm_lower:.1f}% - {llm_upper:.1f}%")

        # ── 6. Groups that become viable (freq >= 10) post-LLM ─────────────
        print_section("6. GROUPS THAT REACH FREQ >= 10 AFTER COLLAPSE")
        print("  (groups large enough to be stable canonical topics)")
        print()

        viable_groups = [g for g in groups if len(g["labels"]) >= 10]
        if viable_groups:
            print(f"  {len(viable_groups)} group(s) reach freq >= 10:")
            print()
            for g in viable_groups:
                top_tokens = sorted(g["tokens"])[:6]
                print(f"  [{len(g['labels'])} labels] Tokens: {', '.join(top_tokens)}")
                for lbl in g["labels"][:8]:
                    print(f"    - {lbl}")
                if len(g["labels"]) > 8:
                    print(f"    ... and {len(g['labels'])-8} more")
                print()
        else:
            viable_5 = [g for g in groups if len(g["labels"]) >= 5]
            print(f"  No groups reach freq >= 10 from distinct concepts alone.")
            print(f"  {len(viable_5)} group(s) reach freq >= 5:")
            for g in viable_5[:10]:
                top_tokens = sorted(g["tokens"])[:5]
                print(f"  [{len(g['labels'])} labels] {', '.join(top_tokens)}")
                for lbl in g["labels"][:4]:
                    print(f"    - {lbl}")
                print()

    print()
    print("=" * 72)
    print("Done.")
    print("=" * 72)


if __name__ == "__main__":
    run()
