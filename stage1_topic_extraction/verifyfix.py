
"""
verify_fix.py
-------------
Quick sanity check on tagged_posts_v2_fixed.csv.
Run: python verify_fix.py
"""
import csv
import os
import sys
import io
from collections import Counter

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

csv.field_size_limit(10_000_000)

HERE  = os.path.dirname(os.path.abspath(__file__))
INPUT = os.path.join(HERE, "tagged_posts_v2_fixed.csv")

VALID_BROAD     = {"AI","Software Engineering","Career","EdTech","Healthcare",
                   "Leadership & Management","Security","Data & Analytics",
                   "Open Source","Business & Strategy","Other"}
VALID_SENTIMENT = {"positive","negative","neutral","mixed"}
VALID_POST_TYPE = {"opinion","news_reaction","question","personal_milestone",
                   "promotional","show_and_tell"}
TRANSFER_TYPES  = {"opinion","news_reaction"}

platform_ct   = Counter()
audience_ct   = Counter()
broad_ct      = Counter()
sentiment_ct  = Counter()
post_type_ct  = Counter()
topic_ct      = Counter()

total = 0
transfer = 0
empty_topic = 0
unclassified = 0

with open(INPUT, newline="", encoding="utf-8", errors="replace") as f:
    reader = csv.DictReader(f)
    print(f"Columns ({len(reader.fieldnames)}): {reader.fieldnames}\n")
    for row in reader:
        total += 1
        platform_ct[row.get("platform","").strip().lower()] += 1
        audience_ct[row.get("audience_room_name","").strip()] += 1
        broad_ct[row.get("topic_broad","").strip()] += 1
        sentiment_ct[row.get("sentiment","").strip()] += 1
        post_type_ct[row.get("post_type","").strip()] += 1
        t = row.get("topic","").strip()
        if not t:          empty_topic += 1
        elif t == "UNCLASSIFIED": unclassified += 1
        else:              topic_ct[t] += 1
        if row.get("post_type","").strip() in TRANSFER_TYPES:
            transfer += 1

print(f"Total rows        : {total:,}")
print(f"Transfer eligible : {transfer:,}")
print(f"Empty topic       : {empty_topic:,}")
print(f"UNCLASSIFIED      : {unclassified:,}")
print(f"Unique topics     : {len(topic_ct):,}")

print(f"\n-- Platform --")
for p,c in platform_ct.most_common():
    print(f"  {c:>6,}  {p}")

print(f"\n-- Audiences ({len(audience_ct)}) --")
for a,c in audience_ct.most_common(25):
    print(f"  {c:>6,}  {a}")

print(f"\n-- topic_broad --")
for b,c in broad_ct.most_common():
    flag = "" if (not b or b in VALID_BROAD) else "  *** INVALID ***"
    print(f"  {c:>6,}  '{b}'{flag}")

print(f"\n-- sentiment --")
for s,c in sentiment_ct.most_common():
    flag = "" if (not s or s in VALID_SENTIMENT) else "  *** INVALID ***"
    print(f"  {c:>6,}  '{s}'{flag}")

print(f"\n-- post_type --")
for pt,c in post_type_ct.most_common():
    flag = "" if (not pt or pt in VALID_POST_TYPE) else "  *** INVALID ***"
    print(f"  {c:>6,}  '{pt}'{flag}")

print(f"\n-- Top 20 topics --")
for t,c in topic_ct.most_common(20):
    print(f"  {c:>5}  {t}")

print(f"\n-- Topic cardinality --")
b = Counter()
for t,c in topic_ct.items():
    if c==1: b["1"]+= 1
    elif c<=3: b["2-3"]+=1
    elif c<=10: b["4-10"]+=1
    elif c<=30: b["11-30"]+=1
    else: b["31+"]+=1
for k in ["1","2-3","4-10","11-30","31+"]:
    print(f"  {b[k]:>6,}  topics with {k} post(s)")