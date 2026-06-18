"""
preprocess_posts_v2.py
----------------------
Final comprehensive preprocessing pipeline.
Applies ALL cleaning decisions in one pass.

Input:  posts_filtered.csv   (35,150 rows — room-filtered, date-filtered)
Output: posts_clean_v2.csv   (clean posts, ready for topic extraction)
        posts_rejected_v2.csv (every dropped row with drop_reason)

Filters applied in order:
  1.  Salvage [removed] Reddit bodies via title in raw_post_json
  2.  Drop known bot accounts (exact username match)
  3.  Drop automod/mod boilerplate by text pattern
  4.  Drop URL-only posts
  5.  Drop posts under 30 chars
  6.  Drop non-English posts (non-Latin script, >40% of words)
  7.  Drop exact duplicates within same profile_id
  8.  Drop narrow personal milestones (LinkedIn only)
  9.  Drop company/org author posts (authorType != Person)
  10. Drop cross-profile exact duplicates (keep first seen per text hash)
  11. Drop near-duplicates (same first-100-chars across different profiles)
  12. Drop link-title posts (URL + <40 chars non-URL text)
  13. Drop engagement-only posts (congratulations, well done, etc.)
  14. Drop corporate job postings (keep personal "I'm hiring")
  15. Drop blocked subreddits (Reddit only)

Author: Ananth / Vectorial AI — May 2026
"""

import csv
import json
import os
import re
import sys
import io
import hashlib
from collections import defaultdict

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

csv.field_size_limit(10_000_000)

HERE        = os.path.dirname(os.path.abspath(__file__))
INPUT_PATH  = os.path.join(HERE, "posts_filtered.csv")
OUTPUT_PATH = os.path.join(HERE, "posts_clean_v2.csv")
REJECT_PATH = os.path.join(HERE, "posts_rejected_v2.csv")

# ── Config ────────────────────────────────────────────────────────────────────

MIN_TEXT_LENGTH = 30

BOT_USERNAMES_EXACT = {
    "webhelperapp", "CSCQMods", "premod_suraweera", "AutomaticAd6646",
    "AutoModerator", "automoderator",
}

BLOCKED_SUBREDDITS = {
    # Vendor / product-controlled
    "OriginTrail", "appsmith", "falkordb", "DiscoLearning", "Infinilearn",
    "AIProductivityLab", "HeadlesseCommerce", "GraphRAG",
    # Personal profile feeds (u_ prefix)
    "u_prodigy_ai", "u_elearningmania", "u_magicedtech2", "u_Essay-Coach",
    "u_BattleFirstAid", "u_AI-Admissions", "u_pianofireman88",
    # Coupon / spam / certification
    "udemyfreebies", "udemyfreebiesdaily", "SideProject", "sideprojects",
    "vibecoding", "istqbastqbatsqa", "EssayCoachingHub", "AI_College_Admissions",
    # Firearms / gear (wrong domain in First Responders room)
    "NFA", "securityguards", "airsoft", "guns", "ar15", "CCW", "gundealsFU",
    "USMC", "greenberets", "gun", "Firearms", "CZFirearms", "SmithAndWesson",
    "Glocks", "1911", "ammo", "SpringfieldArmory", "PrimaryWeaponsSystems",
    "QualityTacticalGear", "liberalgunowners", "CAguns", "FN509", "austinguns",
    "blackpowder", "longrange", "BodyArmor", "TacticalMedicine", "MRE",
    "CompetitionShooting", "CBRNE", "NightVision", "gasmasks", "LessLethalOptions",
    "Shotguns", "nuclearweapons", "knivesandguns",
    # Off-domain: consumer only, no professional LinkedIn equivalent
    "AiPornhubvideo", "SuicideWatch", "LinkedInLunatics",
    "3Dprinting", "OculusQuest", "MetaQuestVR",
    "androidapps", "androidtablets", "iosapps", "drones",
    # NOTE: homelab, buildapc, pcmasterrace, homeassistant, homeautomation,
    # GameDevelopment, IndieDev, gamedev, hobbygamedev, raspberry_pi
    # are intentionally NOT blocked — professional opinions exist there
    # and is_personal flag in topic extraction handles show-and-tell posts
}

AUTOMOD_TEXT_RE = re.compile(
    r'\bi am a bot\b'
    r'|\bthis is a bot\b'
    r'|\bautomoderator\b'
    r'|\bauto moderator\b'
    r"|^hey everyone[!.]?\s+i'?m u/"
    r'|^your (post|submission) has been (removed|locked)'
    r'|^this subreddit requires'
    r'|^please (read|review) the (rules|guidelines)'
    r'|^welcome to r/\w'
    r'|^this (post|thread) (is|has been) (now |)locked'
    r'|^submissions (to this subreddit )?must (be|include|have)'
    r'|^flair your post'
    r'|^moderators? of r/'
    r'|this thread is posted \*\*every',
    re.IGNORECASE
)

PERSONAL_MILESTONE_RE = re.compile(
    r"""
    (just |recently )?(completed?|finished?|earned?|passed?|obtained?)\s
    .{0,40}(course|certification|certificate|credential|badge|bootcamp|program)
    |i('?m| am| have|'?ve)\s+(just\s+)?(joined|starting|accepted|begun)\s.{0,30}(as|at)\s
    |(excited|thrilled|happy|delighted|honored)\s+to\s+(announce|share\s+that\s+i)
      .{0,60}(joined|accepted|starting|new role|new position)
    |\d+[\-\s]?year[s]?\s+(at|with|anniversary)
    |work(ing)?\s+anniversary
    |(i('?ve| have)|just)\s+(been\s+)?(promoted|received\s+a\s+promotion)
    """,
    re.IGNORECASE | re.VERBOSE
)

ENGAGEMENT_RE = re.compile(
    r'^(congratulations?|congrats|well done|great job|so proud|'
    r'happy (birthday|work anniversary)|welcome (to|aboard)|'
    r'thank you for|thanks for sharing|love this|great post|'
    r'well deserved|amazing work|fantastic)',
    re.IGNORECASE
)

CORPORATE_JOB_RE = re.compile(
    r"(we'?re? (hiring|looking for)|join our team|"
    r"open (position|role|requisition)|"
    r"job opening|now hiring|apply (now|today|here)|"
    r"we have an opening)",
    re.IGNORECASE
)

PERSONAL_JOB_RE = re.compile(
    r"\bi'?m (hiring|looking for)\b",
    re.IGNORECASE
)

LINK_TITLE_RE = re.compile(r'https?://\S+', re.IGNORECASE)
URL_ONLY_RE   = re.compile(r'^https?://\S+$')

# ── Helpers ───────────────────────────────────────────────────────────────────

def get_title_from_raw(raw_json_str):
    if not raw_json_str:
        return None
    try:
        raw = json.loads(raw_json_str)
        title = (raw.get("title") or
                 raw.get("post_details", {}).get("title") or None)
        if title and title.strip().lower() not in ("[removed]","[deleted]",""):
            return title.strip()
    except Exception:
        pass
    return None

def get_author_type(raw_json_str):
    if not raw_json_str:
        return ""
    try:
        raw = json.loads(raw_json_str)
        at = (raw.get("authorType") or raw.get("author_type") or
              raw.get("observationType") or "")
        if not at:
            author = raw.get("author", {})
            if isinstance(author, dict):
                at = author.get("type", "")
        return str(at).lower().strip()
    except Exception:
        return ""

def is_removed(text):
    t = text.strip().lower()
    return t in ("[removed]","[deleted]","removed","deleted",
                 "[ removed by moderator ]","[ removed by reddit ]",
                 "[ deleted by user ]","[ removed ]","[ deleted ]")

def is_non_english(text):
    words = text.split()
    if len(words) < 5:
        return False
    def is_latin_or_decorative(c):
        cp = ord(c)
        if cp <= 0x024F: return True
        if 0x1D400 <= cp <= 0x1D7FF: return True
        return False
    non_latin = sum(
        1 for w in words
        if any(ord(c) > 127 and not is_latin_or_decorative(c) for c in w)
    )
    return (non_latin / len(words)) > 0.4

def is_url_only(text):
    return bool(URL_ONLY_RE.match(text.strip()))

def is_link_title(text):
    urls = LINK_TITLE_RE.findall(text)
    if not urls:
        return False
    text_without_urls = LINK_TITLE_RE.sub("", text).strip()
    return len(text_without_urls) < 40

def text_hash(text):
    return hashlib.md5(text.encode("utf-8", errors="replace")).hexdigest()

# ── Main ──────────────────────────────────────────────────────────────────────

def run():
    if not os.path.exists(INPUT_PATH):
        print(f"ERROR: {INPUT_PATH} not found.")
        raise SystemExit(1)

    print(f"Input : {INPUT_PATH}")

    kept_rows     = []
    rejected_rows = []
    fieldnames    = None

    # Dedup state
    within_profile_seen  = defaultdict(set)   # profile_id -> set of text hashes
    global_text_seen     = {}                 # text_hash -> True (cross-profile exact dupes)
    prefix_seen          = {}                 # first-100-chars -> True (near-dupes)

    counts = {k: 0 for k in [
        "total", "salvaged", "drop_bot", "drop_automod", "drop_url",
        "drop_short", "drop_nonenglish", "drop_within_dupe", "drop_milestone",
        "drop_company_author", "drop_cross_dupe", "drop_near_dupe",
        "drop_link_title", "drop_engagement", "drop_corp_job",
        "drop_subreddit", "kept",
    ]}

    def reject(row, reason):
        row["drop_reason"] = reason
        rejected_rows.append(row)

    with open(INPUT_PATH, newline="", encoding="utf-8", errors="replace") as f:
        reader    = csv.DictReader(f)
        fieldnames = reader.fieldnames

        for row in reader:
            counts["total"] += 1
            text      = (row.get("post_text") or "").strip()
            author    = (row.get("author_name") or "").strip()
            platform  = (row.get("platform") or "").strip().lower()
            profile   = (row.get("profile_id") or "").strip()
            raw_json  = (row.get("raw_post_json") or "").strip()
            subreddit = (row.get("subreddit") or "").strip()

            # 1. Salvage [removed]
            if is_removed(text):
                title = get_title_from_raw(raw_json)
                if title:
                    row["post_text"] = title
                    text = title
                    counts["salvaged"] += 1
                else:
                    reject(row, "removed_unrecoverable")
                    continue

            # 2. Bot accounts
            if author in BOT_USERNAMES_EXACT:
                counts["drop_bot"] += 1
                reject(row, f"bot_account:{author}")
                continue

            # 3. Automod content
            if AUTOMOD_TEXT_RE.search(text):
                counts["drop_automod"] += 1
                reject(row, "automod_content")
                continue

            # 4. URL only
            if is_url_only(text):
                counts["drop_url"] += 1
                reject(row, "url_only")
                continue

            # 5. Too short
            if len(text) < MIN_TEXT_LENGTH:
                counts["drop_short"] += 1
                reject(row, f"too_short:{len(text)}")
                continue

            # 6. Non-English
            if is_non_english(text):
                counts["drop_nonenglish"] += 1
                reject(row, "non_english")
                continue

            # 7. Within-profile exact duplicate
            h = text_hash(text)
            if h in within_profile_seen[profile]:
                counts["drop_within_dupe"] += 1
                reject(row, "within_profile_duplicate")
                continue
            within_profile_seen[profile].add(h)

            # 8. Personal milestones (LinkedIn only)
            if platform == "linkedin" and PERSONAL_MILESTONE_RE.search(text):
                counts["drop_milestone"] += 1
                reject(row, "personal_milestone")
                continue

            # 9. Company / org author (LinkedIn only)
            if platform == "linkedin":
                at = get_author_type(raw_json)
                if at and at not in ("person", "individual", ""):
                    counts["drop_company_author"] += 1
                    reject(row, f"company_author:{at}")
                    continue

            # 10. Cross-profile exact duplicate (keep first seen)
            if h in global_text_seen:
                counts["drop_cross_dupe"] += 1
                reject(row, "cross_profile_duplicate")
                continue
            global_text_seen[h] = True

            # 11. Near-duplicate (same first 100 chars across profiles)
            prefix = text[:100].lower().strip()
            if len(prefix) >= 50:
                if prefix in prefix_seen:
                    counts["drop_near_dupe"] += 1
                    reject(row, "near_duplicate")
                    continue
                prefix_seen[prefix] = True

            # 12. Link-title posts
            if is_link_title(text):
                counts["drop_link_title"] += 1
                reject(row, "link_title")
                continue

            # 13. Engagement-only
            if ENGAGEMENT_RE.match(text) and len(text) < 300:
                counts["drop_engagement"] += 1
                reject(row, "engagement_only")
                continue

            # 14. Corporate job postings (keep personal "I'm hiring")
            if CORPORATE_JOB_RE.search(text[:200]):
                if not PERSONAL_JOB_RE.search(text[:100]):
                    counts["drop_corp_job"] += 1
                    reject(row, "corporate_job_posting")
                    continue

            # 15. Blocked subreddits (Reddit only)
            if platform == "reddit" and subreddit in BLOCKED_SUBREDDITS:
                counts["drop_subreddit"] += 1
                reject(row, f"blocked_subreddit:{subreddit}")
                continue

            kept_rows.append(row)

            if counts["total"] % 5000 == 0:
                print(f"  ...{counts['total']:,} scanned  "
                      f"{len(kept_rows):,} kept so far")

    counts["kept"] = len(kept_rows)

    # ── Write outputs ─────────────────────────────────────────────────────────
    clean_fields   = [f for f in (fieldnames or []) if not f.startswith("_")]
    reject_fields  = clean_fields + ["drop_reason"]

    print(f"\nWriting {OUTPUT_PATH}...")
    with open(OUTPUT_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=clean_fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(kept_rows)

    print(f"Writing {REJECT_PATH}...")
    with open(REJECT_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=reject_fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rejected_rows)

    # ── Summary ───────────────────────────────────────────────────────────────
    total  = counts["total"]
    kept   = counts["kept"]
    dropped = total - kept

    print(f"\n{'='*60}")
    print(f"PREPROCESSING V2 COMPLETE")
    print(f"{'='*60}")
    print(f"  Input rows                   : {total:,}")
    print(f"  Salvaged [removed]           : {counts['salvaged']:,}")
    print(f"  {'─'*40}")
    print(f"  Dropped - bot account        : {counts['drop_bot']:,}")
    print(f"  Dropped - automod content    : {counts['drop_automod']:,}")
    print(f"  Dropped - URL only           : {counts['drop_url']:,}")
    print(f"  Dropped - too short          : {counts['drop_short']:,}")
    print(f"  Dropped - non-English        : {counts['drop_nonenglish']:,}")
    print(f"  Dropped - within-profile dup : {counts['drop_within_dupe']:,}")
    print(f"  Dropped - personal milestone : {counts['drop_milestone']:,}")
    print(f"  Dropped - company author     : {counts['drop_company_author']:,}")
    print(f"  Dropped - cross-profile dup  : {counts['drop_cross_dupe']:,}")
    print(f"  Dropped - near-duplicate     : {counts['drop_near_dupe']:,}")
    print(f"  Dropped - link-title         : {counts['drop_link_title']:,}")
    print(f"  Dropped - engagement only    : {counts['drop_engagement']:,}")
    print(f"  Dropped - corporate job post : {counts['drop_corp_job']:,}")
    print(f"  Dropped - blocked subreddit  : {counts['drop_subreddit']:,}")
    print(f"  {'─'*40}")
    print(f"  Total dropped                : {dropped:,}  ({dropped/total*100:.1f}%)")
    print(f"  Kept                         : {kept:,}  ({kept/total*100:.1f}%)")

    clean_mb  = os.path.getsize(OUTPUT_PATH)  / 1e6
    reject_mb = os.path.getsize(REJECT_PATH) / 1e6
    print(f"\n  Output (clean)    : {OUTPUT_PATH}  ({clean_mb:.1f} MB)")
    print(f"  Output (rejected) : {REJECT_PATH}  ({reject_mb:.1f} MB)")
    print(f"\nNext step: run topic_extraction_v1.py on posts_clean_v2.csv")


if __name__ == "__main__":
    run()
