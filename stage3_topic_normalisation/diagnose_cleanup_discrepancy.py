"""
diagnose_cleanup_discrepancy.py
---------------------------------
One-off diagnostic. Read-only, writes nothing. Resolves the discrepancy between:

  cleanup_pass.py's per-domain header numbers (non-singleton canonical topics
  + singleton labels, computed from before_freq built fresh from
  tagged_posts_v2_clean.csv)

  vs.

  master_numbers.py's verified totals (computed from the SAME clean.csv,
  cross-checked directly against topic_cluster_map.json with zero mismatch)

Both scripts read the same source files. If their numbers disagree, the cause
is a difference in COMPUTATION, not a difference in DATA. This script
reproduces both computations side by side, on identical loaded data, and
prints exactly where they diverge -- by domain, and down to the specific
labels if needed.

Usage:
    python diagnose_cleanup_discrepancy.py
    python diagnose_cleanup_discrepancy.py --domain "Software Engineering"
"""

import argparse
import csv
import json
import os
import sys
import io
from collections import defaultdict
from pathlib import Path

import yaml

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

csv.field_size_limit(10_000_000)

DOMAINS = [
    "Software Engineering", "AI", "Security", "EdTech", "Career",
    "Business & Strategy", "Data & Analytics",
    "Leadership & Management", "Open Source",
]

HERE = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(HERE, "tagged_posts_v2_clean.csv")
MAP_PATH = os.path.join(HERE, "topic_cluster_map.json")


def load_clean_csv():
    rows = []
    with open(CSV_PATH, newline="", encoding="utf-8", errors="replace") as f:
        for row in csv.DictReader(f):
            rows.append(row)
    return rows


def load_map():
    with open(MAP_PATH, encoding="utf-8") as f:
        return json.load(f)


def run(domain_filter=None):
    print(f"Loading {CSV_PATH} ...")
    all_rows = load_clean_csv()
    print(f"  {len(all_rows):,} total rows.\n")

    print(f"Loading {MAP_PATH} ...")
    cluster_map = load_map()
    print(f"  {len(cluster_map)} domain keys.\n")

    domains_to_check = [domain_filter] if domain_filter else DOMAINS

    for domain in domains_to_check:
        print("=" * 90)
        print(f"DOMAIN: {domain}")
        print("=" * 90)

        # ── Method A: cleanup_pass.py's exact computation ──────────────────
        # before_freq is built by SCANNING ALL ROWS regardless of domain match
        # at load time, then filtered by (domain, label) key at use time.
        before_freq_cleanup = defaultdict(int)
        for row in all_rows:
            d = row.get("topic_broad", "")
            t = (row.get("topic") or "").strip().lower()
            if t:
                before_freq_cleanup[(d, t)] += 1

        domain_map = cluster_map.get(domain, {})
        canonical_freq_A = defaultdict(int)
        for label, canon in domain_map.items():
            canonical_freq_A[canon] += before_freq_cleanup.get((domain, label), 0)
        non_singleton_A = {c for c, freq in canonical_freq_A.items() if freq >= 2}
        singleton_labels_A = [
            label for label, canon in domain_map.items()
            if label == canon and before_freq_cleanup.get((domain, label), 0) == 1
        ]
        total_A = len(non_singleton_A) + len(singleton_labels_A)

        print(f"\n  METHOD A (cleanup_pass.py's exact logic):")
        print(f"    Non-singleton canonical topics (freq>=2): {len(non_singleton_A):,}")
        print(f"    Singleton labels (self-mapped, freq==1):  {len(singleton_labels_A):,}")
        print(f"    Sum (A's implied total):                   {total_A:,}")

        # ── Method B: master_numbers.py's exact computation ────────────────
        # Restricts to IN-SCOPE rows (topic_broad in DOMAINS) at LOAD time,
        # BEFORE building any frequency dict -- not filtered later by key.
        before_rows_B = [r for r in all_rows if r.get("topic_broad", "") in DOMAINS]
        before_freq_B = defaultdict(int)
        for row in before_rows_B:
            d = row.get("topic_broad", "")
            t = (row.get("topic") or "").strip().lower()
            if t and t not in ("unclassified", ""):
                before_freq_B[(d, t)] += 1

        # master_numbers.py counts UNIQUE LABELS per domain directly from the
        # CSV's topic column for that domain -- not derived from the map at all
        # for the "before" count. But the figure we're cross-checking here
        # (total canonical AFTER) comes from a different part of that script:
        # after_labels_by_domain, built from tagged_posts_v2_NORMALISED.csv.
        # That is the actual ground truth for "how many canonical topics exist",
        # independent of any self-map/non-singleton split entirely.
        norm_path = os.path.join(HERE, "tagged_posts_v2_normalised.csv")
        after_unique_labels_B = set()
        if os.path.exists(norm_path):
            with open(norm_path, newline="", encoding="utf-8", errors="replace") as f:
                for row in csv.DictReader(f):
                    if row.get("topic_broad", "") != domain:
                        continue
                    t = (row.get("topic") or "").strip().lower()
                    if t and t not in ("unclassified", ""):
                        after_unique_labels_B.add(t)
            print(f"\n  METHOD B (master_numbers.py's ground truth -- unique topic")
            print(f"            values directly from tagged_posts_v2_normalised.csv):")
            print(f"    Total unique canonical labels: {len(after_unique_labels_B):,}")
        else:
            print(f"\n  METHOD B: {norm_path} not found on this machine, skipping direct check.")

        # ── Cross-check: does A's non-singleton set match map's own canonical values? ──
        map_canonical_values = set(domain_map.values())
        print(f"\n  CROSS-CHECK:")
        print(f"    Total distinct canonical VALUES in map (ground truth, this IS")
        print(f"    the number diagnose_topic_count.py verified earlier): {len(map_canonical_values):,}")

        a_minus_map = total_A - len(map_canonical_values)
        print(f"\n    Method A's implied total ({total_A:,}) minus map's actual distinct")
        print(f"    canonical values ({len(map_canonical_values):,}) = {a_minus_map:,}")

        if a_minus_map != 0:
            print(f"\n    DIVERGENCE FOUND. Investigating cause...")
            # Hypothesis: singleton_labels_A counts labels where before_freq==1,
            # but some of those labels might ALSO be a canonical value that
            # OTHER labels map to (i.e. they are simultaneously "singleton by
            # raw frequency" AND "a real multi-label canonical target") --
            # double counting them once in non_singleton_A's frequency sum
            # AND again as their own entry in singleton_labels_A.
            overlap = set(singleton_labels_A) & non_singleton_A
            print(f"    Labels that are BOTH in singleton_labels_A AND are themselves")
            print(f"    a non-singleton canonical value (the double-count suspect): {len(overlap):,}")
            if overlap:
                print(f"    First 10 examples:")
                for label in list(overlap)[:10]:
                    print(f"      {label!r}  (freq_as_canonical={canonical_freq_A[label]}, "
                          f"counted again as its own singleton entry)")
                print(f"\n    If this overlap count ~= the divergence ({a_minus_map:,}), this is the cause:")
                print(f"    these labels are counted TWICE by Method A's simple addition --")
                print(f"    once inside non_singleton_A's total, once again in singleton_labels_A's count.")

        print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--domain", default=None)
    args = parser.parse_args()
    run(args.domain)
