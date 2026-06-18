"""
filter_posts.py
---------------
Filters the raw 2.4GB posts.csv from Ashish down to:
  1. Only the 28 in-scope audience rooms
  2. Non-empty post_text
  3. LinkedIn posts with label_useful >= MIN_USEFUL_SCORE (Reddit: no label filter)
  4. Posts after DATE_CUTOFF (optional)

Input:  posts.csv  (same folder)
Output: posts_filtered.csv  (same folder)

Usage:
    python filter_posts.py

Author: Ananth / Vectorial AI — May 2026
"""

import csv
import os
import sys
import io
from datetime import datetime, timezone

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

csv.field_size_limit(10_000_000)

# ── Config ────────────────────────────────────────────────────────────────────
HERE       = os.path.dirname(os.path.abspath(__file__))
INPUT_PATH = os.path.join(HERE, "posts.csv")
OUTPUT_PATH= os.path.join(HERE, "posts_filtered.csv")

# Minimum Sapiens useful score for LinkedIn posts (Reddit has no labels — always kept)
MIN_USEFUL_SCORE = 0.5

# Only keep posts after this date. Set to None to keep all.
# 2 years back from now is a reasonable default — platform norms shift over time.
DATE_CUTOFF = datetime(2024, 1, 1, tzinfo=timezone.utc)

# ── The 28 in-scope room IDs ──────────────────────────────────────────────────
IN_SCOPE_ROOM_IDS = {
    "513b7ba2-e650-44bf-ab0a-f32c1db89e0e",  # AI Engineer merged
    "da5e58d1-70fa-42f8-b8d4-7307a41be679",  # Backend Engineer (linkedin+reddit)
    "31a3dd4c-98ff-4d5d-a26a-5ca631efa334",  # Engineering Manager
    "7c329506-1cb0-4ce6-b7c1-db7c0ec32038",  # Engineering Manager reddit
    "b16d6e7d-ff11-43cd-8cca-61bc6fc6053e",  # Product Manager
    "784977cd-1298-4063-88fe-8d0af52e2f6b",  # Product Manager Reddit
    "bd2e06ef-b1c8-4571-9077-629e22ab972a",  # CTOs
    "49c15c0e-2a2a-4058-80b7-d5e8c01d519f",  # CTO Reddit
    "6fd3a353-43ff-49ad-a669-4b6f5e9a7c18",  # Staff Engineer
    "1828f6b0-1a45-4d7d-98b4-8a58ba8e7f9d",  # VP Engineering
    "9dbf7905-f2d8-4ccc-8f87-e98701287ae1",  # Engineering Leadership Audience Room
    "cc0b0f6e-00f6-45b9-850a-1712e5b884f7",  # Fullstack Engineers
    "5ba1e8d2-98f2-4051-a664-250277ccdfcd",  # Frontend Engineer Reddit
    "9d9464a8-abc7-44ea-bf85-c43d699eceed",  # Full Stack Developer Reddit
    "be54487a-66ec-49fc-bdd8-91ecea633963",  # Devops/Infra Engineer
    "88fc6389-d354-4c56-97a0-de93b49d666e",  # QA Engineer Reddit
    "ffa83140-b830-469c-8a1e-78ec4958e828",  # Programming reddit
    "7a94cc8a-81dc-4eff-b5fe-ca4e5fd3257d",  # Product Leadership Audience Room
    "67464ce5-94eb-45ed-8734-2d779b6d37d2",  # Product Marketing Manager
    "56b94d07-134a-4096-9fbb-6a4ebead5527",  # [LinkedIn] OpenAI Community
    "f6f35805-1c62-4971-b991-9a17f7fac0ac",  # [Reddit] OpenAI Community
    "239bf92b-8e2d-4f5b-b90b-3ac8d3063187",  # Knowledge Graph Devs
    "a00c708a-1310-4fb8-9cc1-8c9370bd639b",  # Instructional Designer
    "1c82d6d5-b94b-436d-9c8e-ac18f2e3de57",  # First Responders
    "a18ef28e-a33a-4be7-af84-fd10f8de075b",  # Director / VP Product in Edtech Co
    "b90814ed-3aec-4dcd-a6d3-25c956add11f",  # Director/ VP engineering/ CTO in Edtech Co
    "2900f94b-2013-4d9f-bead-4a5cd3f01717",  # Engineering Org in Edtech Co
    "3bd77eca-d93a-434d-9a7a-751a03c52db4",  # Edtech Developers
}

# ── Filter ────────────────────────────────────────────────────────────────────

def parse_date(date_str):
    """Parse ISO date string. Returns None if unparseable."""
    if not date_str:
        return None
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def run():
    if not os.path.exists(INPUT_PATH):
        print(f"ERROR: {INPUT_PATH} not found.")
        raise SystemExit(1)

    print(f"Input : {INPUT_PATH}")
    print(f"Filter: {len(IN_SCOPE_ROOM_IDS)} in-scope room IDs")
    print(f"Filter: label_useful >= {MIN_USEFUL_SCORE} (LinkedIn only)")
    print(f"Filter: post_date >= {DATE_CUTOFF.date() if DATE_CUTOFF else 'no cutoff'}")
    print(f"Filter: non-empty post_text")
    print()

    counts = {
        "total":           0,
        "kept":            0,
        "skip_room":       0,
        "skip_text":       0,
        "skip_label":      0,
        "skip_date":       0,
    }
    room_kept = {}

    with open(INPUT_PATH,  newline="", encoding="utf-8", errors="replace") as fin, \
         open(OUTPUT_PATH, "w", newline="", encoding="utf-8") as fout:

        reader = csv.DictReader(fin)
        writer = None

        for row in reader:
            counts["total"] += 1

            # 1. Room filter
            if row.get("audience_room_id", "").strip() not in IN_SCOPE_ROOM_IDS:
                counts["skip_room"] += 1
                continue

            # 2. Empty text filter
            if not row.get("post_text", "").strip():
                counts["skip_text"] += 1
                continue

            # 3. LinkedIn useful label filter
            platform = row.get("platform", "").strip().lower()
            if platform == "linkedin":
                label_str = row.get("label_useful", "").strip()
                if label_str:
                    try:
                        if float(label_str) < MIN_USEFUL_SCORE:
                            counts["skip_label"] += 1
                            continue
                    except ValueError:
                        pass

            # 4. Date cutoff
            if DATE_CUTOFF:
                dt = parse_date(row.get("post_date", ""))
                if dt and dt < DATE_CUTOFF:
                    counts["skip_date"] += 1
                    continue

            # Write
            if writer is None:
                writer = csv.DictWriter(fout, fieldnames=reader.fieldnames,
                                        extrasaction="ignore")
                writer.writeheader()

            writer.writerow(row)
            counts["kept"] += 1
            room = row.get("audience_room_name", "")
            room_kept[room] = room_kept.get(room, 0) + 1

            if counts["total"] % 50_000 == 0:
                print(f"  ...{counts['total']:,} scanned  {counts['kept']:,} kept")

    # Summary
    kept_pct = counts["kept"] / max(counts["total"], 1) * 100
    print(f"\n-- Filter results -----------------------------------")
    print(f"  Total rows scanned  : {counts['total']:,}")
    print(f"  Kept                : {counts['kept']:,}  ({kept_pct:.1f}%)")
    print(f"  Skipped - wrong room: {counts['skip_room']:,}")
    print(f"  Skipped - empty text: {counts['skip_text']:,}")
    print(f"  Skipped - low useful: {counts['skip_label']:,}")
    print(f"  Skipped - too old   : {counts['skip_date']:,}")

    # Platform split of kept rows
    print(f"\n-- Platform split of kept rows ----------------------")
    li = sum(v for k, v in room_kept.items())  # placeholder — recount below
    print(f"  (see room breakdown below)")

    print(f"\n-- Posts kept per room ------------------------------")
    for room, cnt in sorted(room_kept.items(), key=lambda x: -x[1]):
        print(f"  {room[:52]:<54} {cnt:>7,}")

    out_size = os.path.getsize(OUTPUT_PATH) / 1e6
    print(f"\nOutput: {OUTPUT_PATH}  ({out_size:.1f} MB)")
    print(f"\nNext step: run topic_extraction.py")


if __name__ == "__main__":
    run()