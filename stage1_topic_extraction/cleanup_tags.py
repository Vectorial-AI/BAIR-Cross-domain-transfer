"""
cleanup_tags.py
---------------
Post-processing pass over tagged_posts_v2_fixed.csv.
Corrects known label errors without re-running the LLM.

Fixes:
  1. Invalid topic_broad  → canonical 11-item vocabulary
  2. Stance bleed in sentiment field:
       - Sets sentiment to correct valence
       - Moves the stance value to the stance field (if stance is currently empty)
  3. Invalid post_type    → correct post_type

Writes:
  tagged_posts_v2_clean.csv
  cleanup_report.txt

Run: python cleanup_tags.py
"""
import csv
import os
import sys
import io
from collections import defaultdict, Counter

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

csv.field_size_limit(10_000_000)

HERE   = os.path.dirname(os.path.abspath(__file__))
INPUT  = os.path.join(HERE, "tagged_posts_v2_fixed.csv")
OUTPUT = os.path.join(HERE, "tagged_posts_v2_clean.csv")
REPORT = os.path.join(HERE, "cleanup_report.txt")

# ── Vocabularies ──────────────────────────────────────────────────────────────

VALID_BROAD = {
    "AI", "Software Engineering", "Career", "EdTech", "Healthcare",
    "Leadership & Management", "Security", "Data & Analytics",
    "Open Source", "Business & Strategy", "Other"
}

VALID_SENTIMENT = {"positive", "negative", "neutral", "mixed"}

VALID_STANCE = {
    "assertive", "critical", "supportive", "questioning", "informational"
}

VALID_POST_TYPE = {
    "opinion", "news_reaction", "question",
    "personal_milestone", "promotional", "show_and_tell"
}

# ── Correction maps ───────────────────────────────────────────────────────────

BROAD_MAP = {
    "Hardware":                         "Other",
    "Product & Strategy":               "Business & Strategy",
    "Product":                          "Business & Strategy",
    "Cloud & Infrastructure":           "Software Engineering",
    "Design & Management":              "Leadership & Management",
    "Promotional":                      "Other",
    "Customer Support":                 "Other",
    "Product Manager":                  "Business & Strategy",
    "Product Leadership":               "Business & Strategy",
    "Product Leadership Audience Room": "Business & Strategy",
    "Engineering":                      "Software Engineering",
    "DevOps":                           "Software Engineering",
    "Product Management":               "Business & Strategy",
}

# Stance values that bled into the sentiment field.
# Maps invalid_sentiment_value -> (correct_sentiment, correct_stance)
STANCE_BLEED_MAP = {
    "questioning":   ("neutral",  "questioning"),
    "critical":      ("negative", "critical"),
    "assertive":     ("neutral",  "assertive"),
    "supportive":    ("positive", "supportive"),
    "informational": ("neutral",  "informational"),
}

POST_TYPE_MAP = {
    "informational": "show_and_tell",
}

# ── Load ──────────────────────────────────────────────────────────────────────

with open(INPUT, newline="", encoding="utf-8", errors="replace") as f:
    reader     = csv.DictReader(f)
    fieldnames = reader.fieldnames
    rows       = list(reader)

print(f"Loaded {len(rows):,} rows from {os.path.basename(INPUT)}")

# ── Apply corrections ─────────────────────────────────────────────────────────

corrections = defaultdict(list)  # field -> [(original, corrected, post_id)]
total         = len(rows)
corrected_rows = 0

for row in rows:
    changed = False
    pid = row.get("post_id", row.get("profile_id", ""))

    # 1. topic_broad
    tb = row.get("topic_broad", "").strip()
    if tb and tb not in VALID_BROAD:
        canonical = BROAD_MAP.get(tb, "Other")
        corrections["topic_broad"].append((tb, canonical, pid))
        row["topic_broad"] = canonical
        changed = True

    # 2. Stance bleed in sentiment field
    s = row.get("sentiment", "").strip()
    if s and s not in VALID_SENTIMENT:
        fix = STANCE_BLEED_MAP.get(s)
        if fix:
            correct_sentiment, correct_stance = fix
            corrections["sentiment"].append((s, correct_sentiment, pid))
            row["sentiment"] = correct_sentiment

            # Move the stance value to the stance field only if it's currently empty
            current_stance = row.get("stance", "").strip()
            if not current_stance:
                corrections["stance_recovered"].append(
                    ("(empty)", correct_stance, pid)
                )
                row["stance"] = correct_stance
            else:
                corrections["stance_already_set"].append(
                    (correct_stance, current_stance, pid)
                )
            changed = True
        else:
            # Unknown invalid value — set sentiment to neutral, leave stance alone
            corrections["sentiment_unknown"].append((s, "neutral", pid))
            row["sentiment"] = "neutral"
            changed = True

    # 3. post_type
    pt = row.get("post_type", "").strip()
    if pt and pt not in VALID_POST_TYPE:
        canonical = POST_TYPE_MAP.get(pt, "show_and_tell")
        corrections["post_type"].append((pt, canonical, pid))
        row["post_type"] = canonical
        changed = True

    if changed:
        corrected_rows += 1

# ── Write output ──────────────────────────────────────────────────────────────

with open(OUTPUT, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames,
                            quoting=csv.QUOTE_ALL, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)

print(f"Written: {os.path.basename(OUTPUT)}  ({total:,} rows)")

# ── Write report ──────────────────────────────────────────────────────────────

lines = [
    "cleanup_tags.py report",
    f"Input  : {os.path.basename(INPUT)}",
    f"Output : {os.path.basename(OUTPUT)}",
    "",
    f"Total rows     : {total:,}",
    f"Rows corrected : {corrected_rows:,}",
    "",
]

FIELD_LABELS = {
    "topic_broad":        "topic_broad corrections",
    "sentiment":          "sentiment corrections (stance bleed fixed)",
    "stance_recovered":   "stance field populated from bleed (was empty)",
    "stance_already_set": "stance bleed detected but stance already populated — sentiment fixed only",
    "sentiment_unknown":  "sentiment unknown invalid values → neutral",
    "post_type":          "post_type corrections",
}

for field, items in corrections.items():
    label = FIELD_LABELS.get(field, field)
    counts = Counter((orig, corr) for orig, corr, _ in items)
    lines.append(f"── {label} ({len(items):,}) ──")
    for (orig, corr), cnt in sorted(counts.items(), key=lambda x: -x[1]):
        lines.append(f"  {cnt:>5}  '{orig}'  →  '{corr}'")
    lines.append("")

report_text = "\n".join(lines)
with open(REPORT, "w", encoding="utf-8") as f:
    f.write(report_text)

print(report_text)
print(f"Written: {os.path.basename(REPORT)}")
print(f"\nNext step: python cluster_topics.py")
