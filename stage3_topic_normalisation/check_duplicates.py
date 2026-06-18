"""
check_duplicates.py
---------------------
One-off diagnostic. Read-only, writes nothing. Checks proposed_merges.csv for
exact duplicate rows (same domain+stage+raw_label_or_group+proposed_canonical),
reports counts per domain, and determines whether de-duplication would be a
clean, lossless operation (every duplicate is an EXACT repeat with identical
verdict/reason) or whether some "duplicates" actually disagree with each other
(same label pair appearing twice with DIFFERENT verdicts -- which would NOT be
safe to naively de-duplicate, since that would mean picking one arbitrarily).

Usage:
    python check_duplicates.py
"""

import csv
import os
import sys
import io
from collections import defaultdict

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

csv.field_size_limit(10_000_000)

HERE = os.path.dirname(os.path.abspath(__file__))
MERGES_PATH = os.path.join(HERE, "proposed_merges.csv")


def run():
    if not os.path.exists(MERGES_PATH):
        print(f"ERROR: {MERGES_PATH} not found.")
        sys.exit(1)

    rows = []
    with open(MERGES_PATH, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    print(f"Total rows in file: {len(rows):,}\n")

    # Per-domain row counts
    domain_counts = defaultdict(int)
    for r in rows:
        domain_counts[r.get("domain", "")] += 1
    print("Rows per domain:")
    for d, c in sorted(domain_counts.items(), key=lambda x: -x[1]):
        print(f"  {d:<30} {c:>6,}")
    print()

    # Group by (domain, stage, raw_label_or_group, proposed_canonical) -- the
    # identity key that should be unique. If the SAME identity key appears
    # more than once, check whether all occurrences agree exactly.
    key_groups = defaultdict(list)
    for i, r in enumerate(rows):
        key = (r.get("domain", ""), r.get("stage", ""),
               r.get("raw_label_or_group", ""), r.get("proposed_canonical", ""))
        key_groups[key].append((i, r))

    exact_duplicates = []      # identity key repeats, ALL fields identical
    conflicting_duplicates = []  # identity key repeats, but verdict/reason differ

    for key, occurrences in key_groups.items():
        if len(occurrences) <= 1:
            continue
        # Check if every occurrence is byte-for-byte identical across all columns
        full_rows = [tuple(r.items()) for i, r in occurrences]
        if len(set(full_rows)) == 1:
            exact_duplicates.append((key, occurrences))
        else:
            conflicting_duplicates.append((key, occurrences))

    total_exact_dup_rows = sum(len(occ) - 1 for key, occ in exact_duplicates)  # extra copies beyond the first
    total_conflict_groups = len(conflicting_duplicates)

    print("=" * 90)
    print("DUPLICATE ANALYSIS")
    print("=" * 90)
    print(f"\nIdentity keys with EXACT duplicates (same verdict, same reason, "
          f"safe to de-dupe): {len(exact_duplicates):,} keys, "
          f"{total_exact_dup_rows:,} redundant rows total")
    print(f"\nIdentity keys with CONFLICTING duplicates (same label pair, "
          f"DIFFERENT verdict or reason -- NOT safe to blindly de-dupe): "
          f"{total_conflict_groups:,}")

    if conflicting_duplicates:
        print(f"\n  CONFLICTS FOUND. Showing all {len(conflicting_duplicates)}:")
        for key, occurrences in conflicting_duplicates:
            domain, stage, raw, canon = key
            print(f"\n    [{domain} / {stage}] {raw[:60]} -> {canon[:40]}")
            for i, r in occurrences:
                print(f"      row {i}: verdict={r.get('verdict')}  reason={r.get('reason', '')[:80]}")

    # Specifically check Open Source, since that's the domain flagged as having
    # a duplicate-write warning fire during the run.
    os_rows = [(i, r) for i, r in enumerate(rows) if r.get("domain") == "Open Source"]
    os_keys = defaultdict(list)
    for i, r in os_rows:
        key = (r.get("stage", ""), r.get("raw_label_or_group", ""), r.get("proposed_canonical", ""))
        os_keys[key].append((i, r))
    os_dup_keys = {k: v for k, v in os_keys.items() if len(v) > 1}
    os_dup_exact = sum(1 for k, v in os_dup_keys.items()
                       if len(set(tuple(r.items()) for i, r in v)) == 1)
    os_dup_conflict = len(os_dup_keys) - os_dup_exact

    print(f"\n{'='*90}")
    print("OPEN SOURCE SPECIFIC CHECK (the domain that triggered the warning)")
    print(f"{'='*90}")
    print(f"  Total Open Source rows: {len(os_rows):,}")
    print(f"  Distinct identity keys with 2+ occurrences: {len(os_dup_keys):,}")
    print(f"  ...of which EXACT duplicates (safe): {os_dup_exact:,}")
    print(f"  ...of which CONFLICTING (unsafe to blindly dedupe): {os_dup_conflict:,}")

    print(f"\n{'='*90}")
    print("VERDICT")
    print(f"{'='*90}")
    if total_conflict_groups == 0:
        print("  SAFE TO DE-DUPLICATE. Every duplicate found is an exact repeat --")
        print("  same verdict, same reason, every time. Removing extra copies will")
        print("  not lose or alter any information. No re-run needed.")
    else:
        print(f"  NOT FULLY SAFE. {total_conflict_groups} identity key(s) have")
        print(f"  duplicate rows that DISAGREE with each other (different verdict or")
        print(f"  reason for the apparently same merge). De-duplicating naively would")
        print(f"  mean silently picking one verdict over another. Recommend re-running")
        print(f"  the affected domain(s) cleanly (delete file, re-run) rather than")
        print(f"  attempting to resolve disagreement automatically.")


if __name__ == "__main__":
    run()
