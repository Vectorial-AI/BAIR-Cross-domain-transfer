"""
explore_csv.py
--------------
Quick pre-flight audit of posts_clean_v2.csv before the full tagging run.

Answers:
  1. What are the column names? (confirms profile_id exists and its exact name)
  2. What platforms are present and in what volume?
  3. What audience rooms are present and in what volume?
  4. Per-room platform split — are both sides populated?
  5. Per-room unique author count — early signal on diversity risk

Run:
    python explore_csv.py
"""

import csv
import os
import sys
import io
from collections import defaultdict

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

csv.field_size_limit(10_000_000)

HERE      = os.path.dirname(os.path.abspath(__file__))
INPUT     = os.path.join(HERE, "posts_clean_v2.csv")

with open(INPUT, newline="", encoding="utf-8", errors="replace") as f:
    reader    = csv.DictReader(f)
    fieldnames = reader.fieldnames
    rows      = list(reader)

print(f"\n{'='*65}")
print(f"FILE: posts_clean_v2.csv   rows: {len(rows):,}")
print(f"{'='*65}")

# 1. Columns
print(f"\n── Columns ({len(fieldnames)}) ──────────────────────────────────────")
for i, col in enumerate(fieldnames, 1):
    print(f"  {i:>3}. {col}")

# 2. Platform counts
print(f"\n── Platform distribution ────────────────────────────────────")
plat_counts = defaultdict(int)
for r in rows:
    plat_counts[r.get("platform", "").strip().lower()] += 1
for plat, cnt in sorted(plat_counts.items(), key=lambda x: -x[1]):
    pct = cnt / len(rows) * 100
    print(f"  {plat:<20} {cnt:>7,}  ({pct:.1f}%)")

# 3. Identify the profile id column
candidate_cols = [c for c in fieldnames if "profile" in c.lower() or "user" in c.lower() or "author" in c.lower()]
print(f"\n── Profile/author candidate columns ─────────────────────────")
if candidate_cols:
    for col in candidate_cols:
        non_empty = sum(1 for r in rows if r.get(col, "").strip())
        print(f"  {col:<35} non-empty: {non_empty:,} / {len(rows):,}")
else:
    print("  !! No columns with 'profile', 'user', or 'author' in name found !!")
    print("  !! Author diversity tracking will be broken — fix profile_id column name !!")

# 4 & 5. Per-room platform split + unique author counts
print(f"\n── Per-room breakdown ───────────────────────────────────────")
print(f"  {'Room':<42} {'LI':>6} {'RD':>6} {'LI uniq':>8} {'RD uniq':>8}")

room_plat   = defaultdict(lambda: defaultdict(int))
room_authors = defaultdict(lambda: defaultdict(set))

for r in rows:
    room = r.get("audience_room_name", "").strip()
    plat = r.get("platform", "").strip().lower()
    pid  = r.get("profile_id") or r.get("profile_uuid") or r.get("user_id") or ""
    pid  = pid.strip()
    room_plat[room][plat] += 1
    if pid:
        room_authors[room][plat].add(pid)

for room in sorted(room_plat.keys()):
    li      = room_plat[room].get("linkedin", 0)
    rd      = room_plat[room].get("reddit", 0)
    li_uniq = len(room_authors[room].get("linkedin", set()))
    rd_uniq = len(room_authors[room].get("reddit", set()))
    flag = ""
    if li == 0:  flag = " !! NO LI"
    elif rd == 0: flag = " !! NO RD"
    print(f"  {room[:41]:<42} {li:>6,} {rd:>6,} {li_uniq:>8,} {rd_uniq:>8,}{flag}")

print(f"\n{'='*65}\n")