"""
build_pairs.py
--------------
Builds the (audience x topic) paired dataset from tagged_posts_v2_normalised.csv.

Identical logic to the original build_pairs.py — only the input file changes.
Run this after topic normalisation to rebuild the bilateral matrix on
canonical topic labels rather than raw extraction labels.

Outputs:
    paired_dataset_normalised.csv     (audience x topic) matrix

Usage:
    python build_pairs.py
    python build_pairs.py --config config_pairs.yaml

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


def load_config(path: str = "config_pairs.yaml") -> dict:
    config_path = Path(os.path.dirname(os.path.abspath(__file__))) / path
    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return cfg

def resolve(here: str, p: str) -> str:
    return os.path.join(here, p)

def top_items(counter: dict, n: int) -> str:
    return " | ".join(
        k for k, _ in sorted(counter.items(), key=lambda x: -x[1])[:n]
    )

def normalise_audience(raw: str, audience_map: dict) -> str:
    return audience_map.get(raw.strip(), raw.strip())


def build_matrix(tagged: list[dict], cfg: dict) -> list[dict]:
    transfer_types    = set(cfg["transfer_types"])
    audience_map      = cfg.get("audience_map", {})
    groupby_field     = cfg.get("groupby_field", "topic")

    cells = defaultdict(lambda: {
        "linkedin":               0,
        "reddit":                 0,
        "linkedin_authors":       set(),
        "reddit_authors":         set(),
        "linkedin_author_counts": defaultdict(int),
        "reddit_author_counts":   defaultdict(int),
        "linkedin_top_narrow":    defaultdict(int),
        "reddit_top_narrow":      defaultdict(int),
        "linkedin_subtopics":     defaultdict(int),
        "reddit_subtopics":       defaultdict(int),
    })

    for post in tagged:
        if (post.get("topic") and
                post["topic"] != "UNCLASSIFIED" and
                post.get("post_type", "") in transfer_types):

            raw_room  = post.get("audience_room_name", "")
            audience  = normalise_audience(raw_room, audience_map)
            group_val = post.get(groupby_field, "").strip()
            if not group_val or group_val == "UNCLASSIFIED":
                continue
            key  = (audience, group_val)
            plat = post.get("platform", "").lower()
            pid  = post.get("profile_id", "unknown")

            if plat in ("linkedin", "reddit"):
                cells[key][plat] += 1
                cells[key][f"{plat}_authors"].add(pid)
                cells[key][f"{plat}_author_counts"][pid] += 1

                narrow    = post.get("topic_narrow", "").strip()
                topic_val = post.get("topic", "").strip()
                if narrow and narrow != topic_val:
                    cells[key][f"{plat}_top_narrow"][narrow] += 1

                try:
                    subtopics = json.loads(post.get("opinion_subtopics") or "[]")
                except (json.JSONDecodeError, TypeError):
                    subtopics = []
                for s in subtopics:
                    if s:
                        cells[key][f"{plat}_subtopics"][s] += 1

    pair_rows = []
    for (audience, group), c in cells.items():
        li = c["linkedin"]
        rd = c["reddit"]
        li_authors = len(c["linkedin_authors"])
        rd_authors = len(c["reddit_authors"])
        li_top_pct = max(c["linkedin_author_counts"].values(), default=0) / max(li, 1) * 100
        rd_top_pct = max(c["reddit_author_counts"].values(), default=0) / max(rd, 1) * 100

        pair_rows.append({
            "audience":          audience,
            "group":             group,
            "linkedin_posts":    li,
            "reddit_posts":      rd,
            "total_posts":       li + rd,
            "li_unique_authors": li_authors,
            "rd_unique_authors": rd_authors,
            "li_top_author_pct": round(li_top_pct, 1),
            "rd_top_author_pct": round(rd_top_pct, 1),
            "li_top_narrow":     top_items(c["linkedin_top_narrow"], 3),
            "rd_top_narrow":     top_items(c["reddit_top_narrow"],   3),
            "li_top_subtopics":  top_items(c["linkedin_subtopics"],  5),
            "rd_top_subtopics":  top_items(c["reddit_subtopics"],    5),
        })

    pair_rows.sort(key=lambda r: -r["total_posts"])
    return pair_rows


def run(cfg_path: str = "config_pairs.yaml"):
    cfg  = load_config(cfg_path)
    HERE = os.path.dirname(os.path.abspath(__file__))

    # ── Override input/output for normalised run ──────────────────────────────
    INPUT_TAGGED = resolve(HERE, cfg.get(
        "input_tagged_normalised",
        cfg.get("input_tagged", "tagged_posts_v2_normalised.csv")
    ))
    OUTPUT_PAIRS = resolve(HERE, cfg.get(
        "output_pairs_normalised",
        "paired_dataset_normalised.csv"
    ))
    transfer_types = set(cfg["transfer_types"])

    with open(INPUT_TAGGED, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        tagged = list(reader)

    print(f"Loaded {len(tagged):,} posts from {INPUT_TAGGED}")

    pair_rows = build_matrix(tagged, cfg)

    print(f"Writing {OUTPUT_PAIRS}...")
    with open(OUTPUT_PAIRS, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "audience", "group",
            "linkedin_posts", "reddit_posts", "total_posts",
            "li_unique_authors", "rd_unique_authors",
            "li_top_author_pct", "rd_top_author_pct",
            "li_top_narrow", "rd_top_narrow",
            "li_top_subtopics", "rd_top_subtopics",
        ])
        writer.writeheader()
        writer.writerows(pair_rows)

    # ── Summary ───────────────────────────────────────────────────────────────
    sentiment_li: dict[str, int] = defaultdict(int)
    sentiment_rd: dict[str, int] = defaultdict(int)
    for post in tagged:
        if post.get("post_type", "") in transfer_types:
            plat = post.get("platform", "").lower()
            s    = post.get("sentiment", "unknown")
            if plat == "linkedin":  sentiment_li[s] += 1
            elif plat == "reddit":  sentiment_rd[s] += 1

    type_counts: dict[str, int] = defaultdict(int)
    for post in tagged:
        type_counts[post.get("post_type", "unknown")] += 1

    print(f"\n{'='*72}")
    print("RESULTS SUMMARY")
    print(f"{'='*72}")
    print(f"\n  Posts loaded       : {len(tagged):,}")
    print(f"  Grouping field     : {cfg.get('groupby_field','topic')}")
    print(f"  Unique audiences   : {len(set(r['audience'] for r in pair_rows))}")
    print(f"  Unique topics      : {len(set(r['group'] for r in pair_rows))}")
    print(f"  Total cells        : {len(pair_rows)}")

    bilateral = [r for r in pair_rows if r["linkedin_posts"] > 0 and r["reddit_posts"] > 0]
    li_only   = [r for r in pair_rows if r["linkedin_posts"] > 0 and r["reddit_posts"] == 0]
    rd_only   = [r for r in pair_rows if r["reddit_posts"]  > 0 and r["linkedin_posts"] == 0]
    print(f"  Bilateral cells    : {len(bilateral)}  (posts on both LinkedIn and Reddit)")
    print(f"  LinkedIn-only      : {len(li_only)}")
    print(f"  Reddit-only        : {len(rd_only)}")

    # Taran's threshold: >= 10 posts on each side
    viable_10 = [r for r in bilateral
                 if r["linkedin_posts"] >= 10 and r["reddit_posts"] >= 10]
    viable_30 = [r for r in bilateral
                 if r["linkedin_posts"] >= 30 and r["reddit_posts"] >= 30]
    print(f"\n  Cells meeting Taran's threshold (>= 10 posts each side): {len(viable_10)}")
    print(f"  Cells meeting WD threshold      (>= 30 posts each side): {len(viable_30)}")

    print(f"\n  Post type breakdown:")
    for ptype, cnt in sorted(type_counts.items(), key=lambda x: -x[1]):
        pct    = cnt / max(len(tagged), 1) * 100
        marker = " <- transfer" if ptype in transfer_types else ""
        print(f"    {ptype:<22} {cnt:>7,}  ({pct:>5.1f}%){marker}")

    print(f"\n  Sentiment -- LinkedIn (transfer posts only):")
    li_total = sum(sentiment_li.values()) or 1
    for s, cnt in sorted(sentiment_li.items(), key=lambda x: -x[1]):
        print(f"    {s:<14} {cnt:>6,}  ({cnt/li_total*100:.1f}%)")

    print(f"\n  Sentiment -- Reddit (transfer posts only):")
    rd_total = sum(sentiment_rd.values()) or 1
    for s, cnt in sorted(sentiment_rd.items(), key=lambda x: -x[1]):
        print(f"    {s:<14} {cnt:>6,}  ({cnt/rd_total*100:.1f}%)")

    print(f"\n  Top 30 bilateral cells (by total posts):")
    print(f"  {'Audience':<28} {'Topic':<35} {'LI':>6} {'RD':>6} {'Total':>7}")
    for r in bilateral[:30]:
        print(f"  {r['audience'][:27]:<28} {r['group'][:34]:<35}"
              f" {r['linkedin_posts']:>6} {r['reddit_posts']:>6}"
              f" {r['total_posts']:>7}")

    # Top 20 topics per audience — Taran's second question
    print(f"\n  TOP 20 TOPICS PER AUDIENCE (posts on each side)")
    print(f"  {'='*72}")
    audiences = sorted(set(r["audience"] for r in pair_rows))
    for aud in audiences:
        aud_rows = [r for r in pair_rows if r["audience"] == aud]
        total_li = sum(r["linkedin_posts"] for r in aud_rows)
        total_rd = sum(r["reddit_posts"] for r in aud_rows)
        if total_li + total_rd == 0:
            continue
        print(f"\n  {aud}  (LI: {total_li:,} posts  RD: {total_rd:,} posts)")
        print(f"  {'Topic':<40} {'LI':>6} {'RD':>6} {'Total':>7}")
        top20 = sorted(aud_rows, key=lambda r: -r["total_posts"])[:20]
        aud_covered_li = sum(r["linkedin_posts"] for r in top20)
        aud_covered_rd = sum(r["reddit_posts"] for r in top20)
        for r in top20:
            print(f"    {r['group'][:39]:<40}"
                  f" {r['linkedin_posts']:>6} {r['reddit_posts']:>6}"
                  f" {r['total_posts']:>7}")
        li_cov_pct = aud_covered_li / total_li * 100 if total_li else 0
        rd_cov_pct = aud_covered_rd / total_rd * 100 if total_rd else 0
        print(f"  Top-20 covers: LI {aud_covered_li:,}/{total_li:,} ({li_cov_pct:.1f}%)"
              f"   RD {aud_covered_rd:,}/{total_rd:,} ({rd_cov_pct:.1f}%)")

    print(f"\n{'='*72}")
    print(f"Output: {OUTPUT_PAIRS}")
    print(f"{'='*72}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config_pairs.yaml")
    args = parser.parse_args()
    run(args.config)
