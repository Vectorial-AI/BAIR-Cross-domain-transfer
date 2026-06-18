"""
validate_merges.py
--------------------
Rigorous validation of proposed_merges.csv against known failure patterns,
before any human spends time reading hundreds of rows blind.

Does three things:
  1. Checks specific known-bad pairs (found during dry-run spot-checks)
     against their actual verdict -- did the LLM audit catch them?
  2. Surfaces a stratified sample: some approved, some rejected, across
     both synonym and group stages, so a human can judge quality without
     reading the entire file.
  3. Flags suspicious patterns automatically: rejected reasons that look
     thin/generic, approved pairs with low Jaccard scores (more likely
     to be coincidental), and any row with a missing or empty reason.

Does NOT modify proposed_merges.csv or topic_cluster_map.json. Read-only.

Usage:
    python validate_merges.py
    python validate_merges.py --domain "Software Engineering"
"""

import argparse
import csv
import os
import sys
import io
from pathlib import Path

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

csv.field_size_limit(10_000_000)

HERE = os.path.dirname(os.path.abspath(__file__))
MERGES_PATH = os.path.join(HERE, "proposed_merges.csv")

# Known-bad pairs identified during manual dry-run spot-checks, across domains.
# Format: (raw_label_substring, expected_verdict). Matched case-insensitively
# against the raw_label_or_group column. These are pairs a human reader
# already judged as clearly wrong (or, for the one marked YES, clearly right)
# before the live audit ran -- this checks whether Stage 2 agrees.
KNOWN_CASES = [
    ("python performance optimization", "REJECTED", "different technology than postgresql"),
    ("c++ project configuration issues", "REJECTED", "different technology than eslint"),
    ("auth service scaling", "REJECTED", "different service than notification"),
    ("api version updates", "REJECTED", "different scope than node.js version updates"),
    ("postgresql learning resources", "REJECTED", "different technology than python (EdTech domain, not this run)"),
    ("ab testing design patterns", "APPROVED", "genuine synonym of design patterns"),
    ("browser automation tools", "APPROVED", "genuine synonym of automation tools"),
    ("cli utility development", "APPROVED", "genuine synonym of cli tool development"),
]


def load_merges(domain_filter=None):
    if not os.path.exists(MERGES_PATH):
        print(f"ERROR: {MERGES_PATH} not found. Run cleanup_pass.py first.")
        sys.exit(1)
    rows = []
    with open(MERGES_PATH, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if domain_filter and row.get("domain") != domain_filter:
                continue
            rows.append(row)
    return rows


def check_known_cases(rows):
    print("=" * 90)
    print("1. KNOWN-CASE CHECK")
    print("   Pairs already judged by a human during dry-run review -- does the live")
    print("   audit agree?")
    print("=" * 90)
    for substring, expected, note in KNOWN_CASES:
        matches = [r for r in rows if substring.lower() in r["raw_label_or_group"].lower()]
        if not matches:
            print(f"\n  '{substring}' -- NOT FOUND in this run's candidates "
                  f"(may be in a different domain, or didn't survive Stage 1)")
            continue
        for m in matches:
            actual = m["verdict"]
            agree = "MATCH" if actual.startswith(expected) or expected in actual else "MISMATCH"
            flag = "" if agree == "MATCH" else "  <-- CHECK THIS"
            print(f"\n  '{substring}'")
            print(f"    Expected: {expected}  ({note})")
            print(f"    Actual:   {actual}{flag}")
            print(f"    Proposed canonical: {m['proposed_canonical']}")
            print(f"    LLM reason: {m['reason']}")


def stratified_sample(rows, n_per_bucket=10):
    print(f"\n{'='*90}")
    print(f"2. STRATIFIED SAMPLE (up to {n_per_bucket} per bucket, for manual eyeball)")
    print(f"{'='*90}")

    buckets = {
        ("synonym", "APPROVED"): [],
        ("synonym", "REJECTED"): [],
        ("group", "APPROVED"): [],
        ("group", "REJECTED"): [],
    }
    for r in rows:
        key = (r["stage"], r["verdict"])
        if key in buckets:
            buckets[key].append(r)

    for (stage, verdict), items in buckets.items():
        print(f"\n  --- {stage.upper()} / {verdict} ({len(items)} total, showing up to {n_per_bucket}) ---")
        for r in items[:n_per_bucket]:
            if stage == "synonym":
                print(f"    {r['raw_label_or_group']:<42} -> {r['proposed_canonical']:<35} "
                      f"[J={r['score_or_na']}]  {r['reason']}")
            else:
                label_preview = r['raw_label_or_group'][:70]
                print(f"    [{label_preview}]")
                print(f"      -> {r['reason']}")


def flag_suspicious(rows):
    print(f"\n{'='*90}")
    print("3. AUTOMATED SUSPICION FLAGS")
    print(f"{'='*90}")

    empty_reason = [r for r in rows if not r.get("reason", "").strip()
                     and r["verdict"] in ("APPROVED", "REJECTED")]
    print(f"\n  Rows with empty/missing reason despite a final verdict: {len(empty_reason)}")
    for r in empty_reason[:10]:
        print(f"    [{r['stage']}] {r['raw_label_or_group'][:60]} -> verdict={r['verdict']}")

    audit_failed = [r for r in rows if "AUDIT_FAILED" in r.get("reason", "")]
    print(f"\n  Rows where the LLM call itself failed (treated as rejected, "
          f"not silently approved): {len(audit_failed)}")
    for r in audit_failed[:10]:
        print(f"    [{r['stage']}] {r['raw_label_or_group'][:60]}")

    low_score_approved = []
    for r in rows:
        if r["stage"] == "synonym" and r["verdict"] == "APPROVED":
            try:
                score = float(r["score_or_na"])
                if score < 0.55:
                    low_score_approved.append((r, score))
            except (ValueError, TypeError):
                pass
    print(f"\n  Synonym approvals at the low end of Jaccard (< 0.55) -- worth a second look, "
          f"since these were the weakest token-overlap signal to begin with: {len(low_score_approved)}")
    for r, score in low_score_approved[:10]:
        print(f"    {r['raw_label_or_group']:<42} -> {r['proposed_canonical']:<35} [J={score}]  {r['reason']}")

    no_match_rows = [r for r in rows if r["verdict"] == "NO_MATCH"]
    manual_review_rows = [r for r in rows if r["verdict"] == "MANUAL_REVIEW_REQUIRED"]
    print(f"\n  NO_MATCH (singletons considered, no valid grouping found): {len(no_match_rows)}")
    print(f"  MANUAL_REVIEW_REQUIRED (oversized groups never resolved): {len(manual_review_rows)}")


def summary_counts(rows):
    print(f"\n{'='*90}")
    print("SUMMARY COUNTS (from the file directly, cross-check against terminal output)")
    print(f"{'='*90}")
    from collections import Counter
    counts = Counter((r["stage"], r["verdict"]) for r in rows)
    for (stage, verdict), c in sorted(counts.items()):
        print(f"  {stage:<10} {verdict:<25} {c:>5}")
    print(f"\n  TOTAL ROWS: {len(rows)}")


def run(domain_filter=None):
    rows = load_merges(domain_filter)
    if domain_filter:
        print(f"Loaded {len(rows)} rows for domain: {domain_filter}\n")
    else:
        print(f"Loaded {len(rows)} rows across all domains in the file.\n")

    summary_counts(rows)
    check_known_cases(rows)
    stratified_sample(rows)
    flag_suspicious(rows)

    print(f"\n{'='*90}")
    print("Validation complete. This does not replace human review of the full CSV --")
    print("it surfaces what's most likely to need attention first.")
    print(f"{'='*90}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--domain", default=None)
    args = parser.parse_args()
    run(args.domain)
