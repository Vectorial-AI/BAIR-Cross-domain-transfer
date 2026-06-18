"""
check_anchor_pattern.py
-------------------------
Read-only. Scans every APPROVED synonym row in proposed_merges.csv for the
"anchor-topic over-broadening" signature confirmed in Data & Analytics:
a generic organizational/market/angle word merging into a narrower,
product- or platform-named canonical topic.

This is NOT a fully automatic classifier -- it's a targeted grep-with-context
that surfaces every candidate match across all domains for final human
adjudication, since the same signature was confirmed to produce both a real
false positive (D&A) and zero false positives (Open Source) under the
identical prompt. Word-pattern matching alone cannot resolve this; it can
only narrow down which rows need a human or second LLM look.

Signature checked: does the RAW LABEL contain one of a list of generic
organizational/market/angle words, while the PROPOSED CANONICAL does NOT
contain that same word (i.e. the canonical is narrower/more product-specific
than the raw label, which is the shape of the confirmed D&A failure)?

Usage:
    python check_anchor_pattern.py
    python check_anchor_pattern.py --domain "Data & Analytics"
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

# Generic organizational/market/angle words confirmed or strongly suspected to
# produce the anchor-topic failure when they appear in the RAW label but not
# the CANONICAL target -- i.e. the raw label is about a people/org/market lens
# on a subject, and the canonical target is a narrower product/platform name
# that does not actually cover that lens.
RISK_WORDS = [
    "leadership", "market", "solutions", "usability", "culture",
    "strategy", "governance", "policy", "ethics", "regulation",
    "adoption", "career", "hiring", "team", "organization",
    "management", "roi", "budget", "funding", "investment",
]


def load_merges(domain_filter=None):
    if not os.path.exists(MERGES_PATH):
        print(f"ERROR: {MERGES_PATH} not found.")
        sys.exit(1)
    rows = []
    with open(MERGES_PATH, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if domain_filter and row.get("domain") != domain_filter:
                continue
            rows.append(row)
    return rows


def check_signature(raw_label, canonical):
    """
    Returns the list of risk words present in raw_label but NOT in canonical.
    Empty list means no signature match.
    """
    raw_lower = raw_label.lower()
    canon_lower = canonical.lower()
    matches = []
    for word in RISK_WORDS:
        if word in raw_lower and word not in canon_lower:
            matches.append(word)
    return matches


def run(domain_filter=None):
    rows = load_merges(domain_filter)
    approved_synonym_rows = [
        r for r in rows
        if r["stage"] == "synonym" and r["verdict"] == "APPROVED"
    ]

    print(f"Scanning {len(approved_synonym_rows)} APPROVED synonym rows"
          f"{f' in {domain_filter}' if domain_filter else ' across all domains'} "
          f"for the anchor-topic signature.\n")

    flagged = []
    for r in approved_synonym_rows:
        matches = check_signature(r["raw_label_or_group"], r["proposed_canonical"])
        if matches:
            flagged.append((r, matches))

    print("=" * 100)
    print(f"FLAGGED: {len(flagged)} of {len(approved_synonym_rows)} approved synonym rows "
          f"match the anchor-topic risk signature")
    print("=" * 100)
    print()

    by_domain = {}
    for r, matches in flagged:
        by_domain.setdefault(r["domain"], []).append((r, matches))

    for domain, items in sorted(by_domain.items()):
        print(f"--- {domain} ({len(items)} flagged) ---")
        for r, matches in items:
            print(f"  {r['raw_label_or_group']:<42} -> {r['proposed_canonical']:<35} "
                  f"[risk words: {', '.join(matches)}]")
            print(f"    LLM reason: {r['reason']}")
        print()

    print("=" * 100)
    print("These rows are NOT automatically rejected. They are surfaced for a final")
    print("human read -- the same signature produced a confirmed real false positive")
    print("in Data & Analytics (digital analytics leadership/market/solutions/usability)")
    print("but ZERO false positives in Open Source under the identical prompt, so")
    print("word-pattern presence alone cannot decide this. Read each flagged row's")
    print("actual subject-match plausibility before deciding APPLY or HOLD.")
    print("=" * 100)

    # Write a decision-ready CSV: every flagged row, with a blank column for
    # the human's final call, so the adjudication itself is recorded.
    out_path = os.path.join(HERE, "anchor_pattern_review.csv")
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["domain", "raw_label_or_group", "proposed_canonical",
                          "score_or_na", "risk_words", "llm_reason", "human_decision"])
        for r, matches in flagged:
            writer.writerow([r["domain"], r["raw_label_or_group"], r["proposed_canonical"],
                              r["score_or_na"], "; ".join(matches), r["reason"], ""])
    print(f"\nWritten: {out_path}")
    print("Fill in the human_decision column (APPLY or HOLD) for each row before")
    print("any apply step runs.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--domain", default=None)
    args = parser.parse_args()
    run(args.domain)
