
"""
fix_csv_export.py
-----------------
Repairs tagged_posts_v2.csv by re-reading it correctly and writing a clean version.
The original file has raw_post_json content bleeding across columns due to missing quoting.

Run: python fix_csv_export.py
"""
import csv
import os
import sys
import io

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

csv.field_size_limit(10_000_000)

HERE    = os.path.dirname(os.path.abspath(__file__))
INPUT   = os.path.join(HERE, "tagged_posts_v2.csv")
OUTPUT  = os.path.join(HERE, "tagged_posts_v2_fixed.csv")

# These are the only columns we want — in order
KEEP_COLS = [
    "audience_room_id", "audience_room_name", "platform", "profile_id",
    "post_id", "post_text", "post_date", "post_url", "num_likes",
    "num_comments", "num_shares", "subreddit", "label_useful",
    "label_personal", "label_off_domain", "label_low_content",
    "label_generic_advice", "label_repost", "label_promo",
    "author_name", "author_headline", "author_profile_url", "share_audience",
    "topic_broad", "topic", "topic_narrow", "opinion_subtopics",
    "sentiment", "stance", "post_type"
    # raw_post_json deliberately excluded
]

with open(INPUT, newline="", encoding="utf-8", errors="replace") as f:
    reader = csv.DictReader(f)
    all_cols = reader.fieldnames
    print(f"Total columns in file: {len(all_cols)}")
    print(f"Keeping: {len(KEEP_COLS)} columns, dropping raw_post_json and phantom columns")

    rows_written = 0
    rows_skipped = 0

    with open(OUTPUT, "w", newline="", encoding="utf-8") as out:
        writer = csv.DictWriter(out, fieldnames=KEEP_COLS,
                                quoting=csv.QUOTE_ALL,
                                extrasaction="ignore")
        writer.writeheader()

        for row in reader:
            # Only keep rows where platform is a real platform value
            plat = row.get("platform", "").strip().lower()
            if plat not in ("linkedin", "reddit", "twitter", "x"):
                rows_skipped += 1
                continue
            # Only keep rows where audience_room_name looks like a real room
            aud = row.get("audience_room_name", "").strip()
            if len(aud) > 100 or aud.startswith("{") or aud.startswith('"'):
                rows_skipped += 1
                continue
            writer.writerow(row)
            rows_written += 1

print(f"\nRows written : {rows_written:,}")
print(f"Rows skipped : {rows_skipped:,}  (corrupted/JSON-bleed rows)")
print(f"\nOutput: tagged_posts_v2_fixed.csv")
print(f"Next step: run cleanup_tags.py on tagged_posts_v2_fixed.csv")