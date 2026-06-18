"""
diagnose_outputs.py
-------------------
Exploratory scan of tagged_posts_v2.csv and paired_dataset_v2.csv.
Run: python diagnose_outputs.py
"""
import csv
import os
import sys
import io
from collections import Counter, defaultdict

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

csv.field_size_limit(10_000_000)

HERE        = os.path.dirname(os.path.abspath(__file__))
TAGGED      = os.path.join(HERE, "tagged_posts_v2.csv")
PAIRS       = os.path.join(HERE, "paired_dataset_v2.csv")

VALID_BROAD = {
    "AI", "Software Engineering", "Career", "EdTech", "Healthcare",
    "Leadership & Management", "Security", "Data & Analytics",
    "Open Source", "Business & Strategy", "Other"
}
VALID_SENTIMENT  = {"positive", "negative", "neutral", "mixed"}
VALID_STANCE     = {"assertive", "critical", "supportive", "questioning", "informational"}
VALID_POST_TYPES = {"opinion", "news_reaction", "question", "personal_milestone",
                    "promotional", "show_and_tell"}
TRANSFER_TYPES   = {"opinion", "news_reaction"}

# ── tagged_posts_v2.csv ───────────────────────────────────────────────────────
print("=" * 65)
print("TAGGED_POSTS_V2.CSV")
print("=" * 65)

total = 0
empty_topic = 0
unclassified = 0
transfer_eligible = 0

topic_broad_counts  = Counter()
sentiment_counts    = Counter()
stance_counts       = Counter()
post_type_counts    = Counter()
platform_counts     = Counter()
audience_counts     = Counter()
topic_counts        = Counter()
profile_id_missing  = 0
profile_id_present  = 0

# Track invalid values
invalid_broad     = Counter()
invalid_sentiment = Counter()
invalid_post_type = Counter()

with open(TAGGED, newline="", encoding="utf-8", errors="replace") as f:
    reader = csv.DictReader(f)
    fieldnames = reader.fieldnames
    print(f"\nColumns: {fieldnames}\n")
    for row in reader:
        total += 1
        tb = row.get("topic_broad", "").strip()
        t  = row.get("topic", "").strip()
        s  = row.get("sentiment", "").strip()
        st = row.get("stance", "").strip()
        pt = row.get("post_type", "").strip()
        pl = row.get("platform", "").strip().lower()
        pid = row.get("profile_id", "").strip()

        if not pid or pid == "unknown":
            profile_id_missing += 1
        else:
            profile_id_present += 1

        platform_counts[pl] += 1
        audience_counts[row.get("audience_room_name", "").strip()] += 1

        if not t:
            empty_topic += 1
        elif t == "UNCLASSIFIED":
            unclassified += 1
        else:
            topic_counts[t] += 1

        topic_broad_counts[tb] += 1
        if tb and tb not in VALID_BROAD:
            invalid_broad[tb] += 1

        sentiment_counts[s] += 1
        if s and s not in VALID_SENTIMENT:
            invalid_sentiment[s] += 1

        stance_counts[st] += 1
        post_type_counts[pt] += 1
        if pt and pt not in VALID_POST_TYPES:
            invalid_post_type[pt] += 1

        if pt in TRANSFER_TYPES:
            transfer_eligible += 1

print(f"Total rows          : {total:,}")
print(f"Transfer eligible   : {transfer_eligible:,}  (post_type in opinion/news_reaction)")
print(f"Empty topic         : {empty_topic:,}")
print(f"UNCLASSIFIED topic  : {unclassified:,}")
print(f"Unique topics       : {len(topic_counts):,}")
print(f"profile_id present  : {profile_id_present:,}")
print(f"profile_id missing  : {profile_id_missing:,}")

print(f"\n-- Platform distribution --")
for p, c in platform_counts.most_common():
    print(f"  {p:<20} {c:>6,}")

print(f"\n-- Audience rooms ({len(audience_counts)} total) --")
for a, c in audience_counts.most_common():
    print(f"  {c:>6,}  {a}")

print(f"\n-- topic_broad distribution --")
for b, c in topic_broad_counts.most_common():
    marker = " *** INVALID ***" if b and b not in VALID_BROAD else ""
    print(f"  {c:>6,}  '{b}'{marker}")

print(f"\n-- Invalid topic_broad values ({sum(invalid_broad.values())} posts) --")
for v, c in invalid_broad.most_common():
    print(f"  {c:>5}  '{v}'")

print(f"\n-- sentiment distribution --")
for s, c in sentiment_counts.most_common():
    marker = " *** INVALID ***" if s and s not in VALID_SENTIMENT else ""
    print(f"  {c:>6,}  '{s}'{marker}")

print(f"\n-- Invalid sentiment values (stance bleed) --")
for v, c in invalid_sentiment.most_common():
    print(f"  {c:>5}  '{v}'")

print(f"\n-- post_type distribution --")
for pt, c in post_type_counts.most_common():
    marker = " *** INVALID ***" if pt and pt not in VALID_POST_TYPES else ""
    print(f"  {c:>6,}  '{pt}'{marker}")

print(f"\n-- Top 30 topics by frequency --")
for t, c in topic_counts.most_common(30):
    print(f"  {c:>5}  {t}")

print(f"\n-- Topic cardinality distribution --")
freq_buckets = Counter()
for t, c in topic_counts.items():
    if c == 1:   freq_buckets["1 post"] += 1
    elif c <= 3: freq_buckets["2-3 posts"] += 1
    elif c <= 10: freq_buckets["4-10 posts"] += 1
    elif c <= 30: freq_buckets["11-30 posts"] += 1
    else:         freq_buckets["31+ posts"] += 1
for bucket in ["1 post", "2-3 posts", "4-10 posts", "11-30 posts", "31+ posts"]:
    print(f"  {freq_buckets[bucket]:>6,}  topics with {bucket}")

# ── paired_dataset_v2.csv ─────────────────────────────────────────────────────
print("\n" + "=" * 65)
print("PAIRED_DATASET_V2.CSV")
print("=" * 65)

if not os.path.exists(PAIRS):
    print("  File not found — skip")
else:
    pair_total = 0
    status_counts = Counter()
    group_counts  = Counter()
    audience_pair_counts = Counter()

    with open(PAIRS, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        pair_fields = reader.fieldnames
        print(f"\nColumns: {pair_fields}\n")
        for row in reader:
            pair_total += 1
            status_counts[row.get("status", row.get("valid_pair", ""))] += 1
            group_counts[row.get("group", row.get("topic_level2", ""))] += 1
            audience_pair_counts[row.get("audience", "")] += 1

    print(f"Total pair rows     : {pair_total:,}")
    print(f"\n-- Status distribution --")
    for s, c in status_counts.most_common():
        print(f"  {c:>5}  {s}")
    print(f"\n-- Audience coverage --")
    for a, c in audience_pair_counts.most_common():
        print(f"  {c:>5} cells  {a}")
    print(f"\n-- Unique groups in pairs: {len(group_counts):,} --")
    print(f"Top 20 groups by cell count:")
    for g, c in group_counts.most_common(20):
        print(f"  {c:>5}  {g}")