"""
apply_topic_map.py
-------------------
Applies topic_cluster_map.json to tagged_posts_v2_clean.csv, rewriting the
topic field with the canonical normalised label.

Reads:
    tagged_posts_v2_clean.csv
    topic_cluster_map.json

Writes:
    tagged_posts_v2_normalised.csv

The original topic field is preserved as topic_raw. The normalised canonical
label is written to topic. All other fields are unchanged.

Unmapped labels (labels with no entry in the cluster map for their domain)
are kept as-is. A summary is printed showing coverage and unmapped rate.

Usage:
    python apply_topic_map.py
    python apply_topic_map.py --config config_vocab.yaml

Author: Ananth / Vectorial AI -- June 2026
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

DEFAULTS = {
    "input_path":    "tagged_posts_v2_clean.csv",
    "map_output":    "topic_cluster_map.json",
    "normalised_output": "tagged_posts_v2_normalised.csv",
}

def load_config(path: str) -> dict:
    cfg = DEFAULTS.copy()
    config_path = Path(os.path.dirname(os.path.abspath(__file__))) / path
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            overrides = yaml.safe_load(f) or {}
        cfg.update(overrides)
    return cfg

def resolve(here: str, p: str) -> str:
    return os.path.join(here, p)

def run(cfg_path: str = "config_vocab.yaml"):
    cfg  = load_config(cfg_path)
    HERE = os.path.dirname(os.path.abspath(__file__))

    INPUT_PATH  = resolve(HERE, cfg["input_path"])
    MAP_PATH    = resolve(HERE, cfg["map_output"])
    OUTPUT_PATH = resolve(HERE, cfg["normalised_output"])

    # Load cluster map
    print(f"Loading cluster map from {MAP_PATH} ...")
    with open(MAP_PATH, encoding="utf-8") as f:
        cluster_map: dict[str, dict[str, str]] = json.load(f)
    total_mappings = sum(len(v) for v in cluster_map.values())
    print(f"  {total_mappings:,} label mappings across {len(cluster_map)} domains.")

    # Load corpus
    print(f"Loading corpus from {INPUT_PATH} ...")
    posts = []
    with open(INPUT_PATH, newline="", encoding="utf-8", errors="replace") as f:
        reader    = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        for row in reader:
            posts.append(row)
    print(f"  {len(posts):,} posts loaded.")

    # Build output fieldnames: insert topic_raw after topic
    out_fields = []
    for fn in fieldnames:
        out_fields.append(fn)
        if fn == "topic":
            out_fields.append("topic_raw")
    if "topic_raw" not in out_fields:
        out_fields.append("topic_raw")

    # Apply map
    stats: dict[str, dict] = defaultdict(lambda: {
        "total": 0, "mapped": 0, "unchanged": 0, "unmapped": 0
    })

    for post in posts:
        domain     = post.get("topic_broad", "")
        raw_label  = (post.get("topic") or "").strip().lower()
        post["topic_raw"] = raw_label

        domain_map = cluster_map.get(domain, {})
        stats[domain]["total"] += 1

        if not raw_label or raw_label == "unclassified":
            stats[domain]["unchanged"] += 1
            continue

        if raw_label in domain_map:
            canonical = domain_map[raw_label]
            post["topic"] = canonical
            if canonical == raw_label:
                stats[domain]["unchanged"] += 1
            else:
                stats[domain]["mapped"] += 1
        else:
            # Not in map -- keep raw label, count as unmapped
            stats[domain]["unmapped"] += 1

    # Write output
    print(f"\nWriting normalised corpus to {OUTPUT_PATH} ...")
    with open(OUTPUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=out_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(posts)
    print(f"  {len(posts):,} rows written.")

    # Summary
    print()
    print("=" * 72)
    print("APPLICATION SUMMARY")
    print("=" * 72)
    print()
    print(f"  {'Domain':<35} {'Total':>7} {'Mapped':>8} {'Unchanged':>10} {'Unmapped':>9}")
    print(f"  {'-'*35} {'-'*7} {'-'*8} {'-'*10} {'-'*9}")

    grand_total = grand_mapped = grand_unmapped = 0
    for domain, s in sorted(stats.items()):
        if s["total"] == 0:
            continue
        mapped_pct   = s["mapped"]   / s["total"] * 100
        unmapped_pct = s["unmapped"] / s["total"] * 100
        print(f"  {domain:<35} {s['total']:>7,} "
              f"{s['mapped']:>7,} ({mapped_pct:>4.1f}%) "
              f"{s['unchanged']:>9,} "
              f"{s['unmapped']:>8,} ({unmapped_pct:>4.1f}%)")
        grand_total    += s["total"]
        grand_mapped   += s["mapped"]
        grand_unmapped += s["unmapped"]

    print(f"  {'TOTAL':<35} {grand_total:>7,} "
          f"{grand_mapped:>7,} ({grand_mapped/grand_total*100:>4.1f}%) "
          f"{'':>9} "
          f"{grand_unmapped:>8,} ({grand_unmapped/grand_total*100:>4.1f}%)")
    print()
    print("=" * 72)
    print()
    print("Next step:")
    print("  python build_pairs.py  -- rebuild matrix on normalised corpus.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config_vocab.yaml")
    args = parser.parse_args()
    run(args.config)
