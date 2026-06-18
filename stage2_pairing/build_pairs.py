"""
build_pairs.py
--------------
Builds the (audience x topic) paired dataset from tagged_posts_v2.csv.

Completely separate from the LLM tagging script — runs on already-tagged data.
Rerun this freely to change thresholds, groupings, or audience mappings
without touching the LLM or re-spending on the API.

Validity logic:
  - valid_pair = True if BOTH sides have >= min_pair_threshold posts
  - Author diversity is ADVISORY ONLY — reported in columns, never gates valid_pair
  - All thresholds are config-driven and documented as time-of-run snapshots
    Rerun after adding new data to get updated validity assessments.

Outputs:
    paired_dataset_v2.csv     (audience x topic) matrix — full research instrument

Usage:
    python build_pairs.py
    python build_pairs.py --config config_pairs.yaml

Author: Ananth / Vectorial AI — June 2026
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


# ── Config ────────────────────────────────────────────────────────────────────

def load_config(path: str = "config_pairs.yaml") -> dict:
    config_path = Path(os.path.dirname(os.path.abspath(__file__))) / path
    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return cfg

def resolve(here: str, p: str) -> str:
    return os.path.join(here, p)


# ── Helpers ───────────────────────────────────────────────────────────────────

def top_items(counter: dict, n: int) -> str:
    """Return top-n keys from a frequency counter as a pipe-separated string."""
    return " | ".join(
        k for k, _ in sorted(counter.items(), key=lambda x: -x[1])[:n]
    )

def normalise_audience(raw: str, audience_map: dict) -> str:
    """Map raw room name to canonical audience label."""
    return audience_map.get(raw.strip(), raw.strip())


# ── Matrix builder ────────────────────────────────────────────────────────────

def build_matrix(tagged: list[dict], cfg: dict) -> list[dict]:
    transfer_types        = set(cfg["transfer_types"])
    min_pair              = cfg["min_pair_threshold"]
    concentration_pct     = cfg["concentration_warning_pct"]
    audience_map          = cfg.get("audience_map", {})
    groupby_field         = cfg.get("groupby_field", "topic")

    cells = defaultdict(lambda: {
        # post counts
        "linkedin":                0,
        "reddit":                  0,
        # author diversity — advisory
        "linkedin_authors":        set(),
        "reddit_authors":          set(),
        "linkedin_author_counts":  defaultdict(int),
        "reddit_author_counts":    defaultdict(int),
        # topic_narrow frequency — temporal / event anchoring
        "linkedin_top_narrow":     defaultdict(int),
        "reddit_top_narrow":       defaultdict(int),
        # opinion_subtopics frequency — WD thematic loss signal
        "linkedin_subtopics":      defaultdict(int),
        "reddit_subtopics":        defaultdict(int),
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

                # topic_narrow — only count when it's a real named event
                narrow    = post.get("topic_narrow", "").strip()
                topic_val = post.get("topic", "").strip()
                if narrow and narrow != topic_val:
                    cells[key][f"{plat}_top_narrow"][narrow] += 1

                # opinion_subtopics
                try:
                    subtopics = json.loads(post.get("opinion_subtopics") or "[]")
                except (json.JSONDecodeError, TypeError):
                    subtopics = []
                for s in subtopics:
                    if s:
                        cells[key][f"{plat}_subtopics"][s] += 1

    pair_rows = []
    for (audience, group), c in cells.items():   # unpack key directly — fixes the bug
        li = c["linkedin"]
        rd = c["reddit"]
        li_authors = len(c["linkedin_authors"])
        rd_authors = len(c["reddit_authors"])

        li_top_pct = (
            max(c["linkedin_author_counts"].values(), default=0) / max(li, 1) * 100
        )
        rd_top_pct = (
            max(c["reddit_author_counts"].values(), default=0) / max(rd, 1) * 100
        )

        pair_rows.append({
            "audience":           audience,
            "group":              group,           # ← from cell key, not loop variable
            "linkedin_posts":     li,
            "reddit_posts":       rd,
            "total_posts":        li + rd,
            "li_unique_authors":  li_authors,
            "rd_unique_authors":  rd_authors,
            "li_top_author_pct":  round(li_top_pct, 1),
            "rd_top_author_pct":  round(rd_top_pct, 1),
            "li_top_narrow":      top_items(c["linkedin_top_narrow"], 3),
            "rd_top_narrow":      top_items(c["reddit_top_narrow"],   3),
            "li_top_subtopics":   top_items(c["linkedin_subtopics"],  5),
            "rd_top_subtopics":   top_items(c["reddit_subtopics"],    5),
        })

    pair_rows.sort(key=lambda r: -r["total_posts"])
    return pair_rows


# ── Main ──────────────────────────────────────────────────────────────────────

def run(cfg_path: str = "config_pairs.yaml"):
    cfg  = load_config(cfg_path)
    HERE = os.path.dirname(os.path.abspath(__file__))

    INPUT_TAGGED = resolve(HERE, cfg["input_tagged"])
    OUTPUT_PAIRS = resolve(HERE, cfg["output_pairs"])

    transfer_types = set(cfg["transfer_types"])

    # Load tagged posts
    with open(INPUT_TAGGED, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        tagged = list(reader)

    print(f"Loaded {len(tagged):,} tagged posts from {cfg['input_tagged']}")

    # Build matrix
    pair_rows = build_matrix(tagged, cfg)

    # Write paired dataset
    print(f"Writing {OUTPUT_PAIRS}...")
    with open(OUTPUT_PAIRS, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "audience", "group",
                "linkedin_posts", "reddit_posts", "total_posts",
                "li_unique_authors", "rd_unique_authors",
                "li_top_author_pct", "rd_top_author_pct",
                "li_top_narrow", "rd_top_narrow",
                "li_top_subtopics", "rd_top_subtopics",
            ],
        )
        writer.writeheader()
        writer.writerows(pair_rows)

    # ── Summary ───────────────────────────────────────────────────────────────

    # Sentiment breakdown per platform (transfer posts only)
    sentiment_li: dict[str, int] = defaultdict(int)
    sentiment_rd: dict[str, int] = defaultdict(int)
    for post in tagged:
        if post.get("post_type", "") in transfer_types:
            plat = post.get("platform", "").lower()
            s    = post.get("sentiment", "unknown")
            if plat == "linkedin":
                sentiment_li[s] += 1
            elif plat == "reddit":
                sentiment_rd[s] += 1

    # Post type breakdown
    type_counts: dict[str, int] = defaultdict(int)
    for post in tagged:
        type_counts[post.get("post_type", "unknown")] += 1

    print(f"\n{'='*65}")
    print("RESULTS SUMMARY")
    print(f"{'='*65}")
    print(f"\n  Posts loaded       : {len(tagged):,}")
    print(f"  Grouping field     : {cfg.get('groupby_field','topic')}")
    print(f"  Unique audiences   : {len(set(r['audience'] for r in pair_rows))}")
    print(f"  Unique groups      : {len(set(r['group'] for r in pair_rows))}")
    print(f"  Total cells        : {len(pair_rows)}")
    bilateral = [r for r in pair_rows if r["linkedin_posts"] > 0 and r["reddit_posts"] > 0]
    li_only   = [r for r in pair_rows if r["linkedin_posts"] > 0 and r["reddit_posts"] == 0]
    rd_only   = [r for r in pair_rows if r["reddit_posts"]  > 0 and r["linkedin_posts"] == 0]
    print(f"  Bilateral cells    : {len(bilateral)}  (posts on both sides)")
    print(f"  LinkedIn-only      : {len(li_only)}")
    print(f"  Reddit-only        : {len(rd_only)}")

    print(f"\n  Post type breakdown:")
    for ptype, cnt in sorted(type_counts.items(), key=lambda x: -x[1]):
        pct    = cnt / max(len(tagged), 1) * 100
        marker = " <- transfer" if ptype in transfer_types else ""
        print(f"    {ptype:<22} {cnt:>7,}  ({pct:>5.1f}%){marker}")

    print(f"\n  Sentiment — LinkedIn (transfer posts only):")
    li_total = sum(sentiment_li.values()) or 1
    for s, cnt in sorted(sentiment_li.items(), key=lambda x: -x[1]):
        print(f"    {s:<14} {cnt:>6,}  ({cnt/li_total*100:.1f}%)")

    print(f"\n  Sentiment — Reddit (transfer posts only):")
    rd_total = sum(sentiment_rd.values()) or 1
    for s, cnt in sorted(sentiment_rd.items(), key=lambda x: -x[1]):
        print(f"    {s:<14} {cnt:>6,}  ({cnt/rd_total*100:.1f}%)")

    print(f"\n  Top 30 bilateral cells (by total posts):")
    print(f"  {'Audience':<28} {'Group':<28} {'LI':>6} {'RD':>6} {'Total':>7}"
          f" {'LI au':>6} {'RD au':>6}")
    for r in bilateral[:30]:
        print(
            f"  {r['audience'][:27]:<28} {r['group'][:27]:<28}"
            f" {r['linkedin_posts']:>6} {r['reddit_posts']:>6}"
            f" {r['total_posts']:>7}"
            f" {r['li_unique_authors']:>6} {r['rd_unique_authors']:>6}"
        )

    print(f"\n  Audience coverage:")
    print(f"  {'Audience':<32} {'LI posts':>9} {'RD posts':>9} {'Bilateral cells':>16}")
    audience_li = defaultdict(int)
    audience_rd = defaultdict(int)
    for r in pair_rows:
        audience_li[r["audience"]] += r["linkedin_posts"]
        audience_rd[r["audience"]] += r["reddit_posts"]
    all_audiences = sorted(set(list(audience_li.keys()) + list(audience_rd.keys())))
    for aud in all_audiences:
        bil_cells = sum(1 for r in bilateral if r["audience"] == aud)
        flag = "" if (audience_li[aud] > 0 and audience_rd[aud] > 0) else " !! one-sided"
        print(f"  {aud[:31]:<32} {audience_li[aud]:>9,}"
              f" {audience_rd[aud]:>9,} {bil_cells:>16}{flag}")

    print(f"\n{'='*65}")
    print(f"Output: {OUTPUT_PAIRS}")
    print(f"{'='*65}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config", default="config_pairs.yaml",
        help="Path to config YAML (default: config_pairs.yaml)"
    )
    args = parser.parse_args()
    run(args.config)