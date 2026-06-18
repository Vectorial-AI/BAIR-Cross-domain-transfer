"""
explore_topic_normalisation.py
--------------------------------
Exhaustive data-driven exploration of the topic field to predict
post-normalisation state. Covers every aspect of the task.

Sections:
  1. Full frequency distribution -- where do posts actually sit?
  2. Singleton anatomy -- what are the 2,958 singletons really?
     - Named product/tool/version (specific, unfixable)
     - Generic modifier of an existing freq>=2 label (fixable synonym)
     - Genuinely distinct concept (new canonical topic candidate)
     - Ambiguous/noise
  3. Synonym cluster simulation -- for each freq>=2 seed, how many
     singletons cluster around it via word overlap? What combined
     frequency results?
  4. Distinct concept candidates -- singletons that don't cluster
     to any seed but appear in the top-N by domain specificity.
     These become NEW canonical topics.
  5. Post-normalisation frequency distribution simulation --
     assuming identify-and-invent maps correctly, what does the
     resulting canonical vocabulary look like?
  6. Bilateral viability re-assessment with simulated vocabulary
  7. Representative sample of each case for human review

Run: python explore_topic_normalisation.py
"""

import csv
import json
import os
import sys
import io
import re
import math
from collections import Counter, defaultdict

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

csv.field_size_limit(10_000_000)

HERE  = os.path.dirname(os.path.abspath(__file__))
INPUT = os.path.join(HERE, "tagged_posts_v2_clean.csv")

TRANSFER_TYPES = {"opinion", "news_reaction"}

DOMAINS_FOCUS = ["Software Engineering", "AI", "Security", "EdTech"]

ALL_DOMAINS = [
    "Software Engineering", "AI", "Career", "EdTech",
    "Business & Strategy", "Security", "Data & Analytics",
    "Leadership & Management", "Open Source",
]

BROAD_FIX = {
    "software engineering": "Software Engineering",
    "ai": "AI", "edtech": "EdTech", "career": "Career",
    "business & strategy": "Business & Strategy",
    "security": "Security", "data & analytics": "Data & Analytics",
    "leadership & management": "Leadership & Management",
    "open source": "Open Source", "healthcare": "Healthcare", "other": "Other",
}

AUDIENCE_MAP = {
    "Backend Engineer (linkedin+reddit)":  "Backend Engineer",
    "CTOs":                                "CTO",
    "CTO Reddit":                          "CTO",
    "Engineering Manager":                 "Engineering Manager",
    "Engineering Manager reddit":          "Engineering Manager",
    "Fullstack Engineers":                 "Fullstack Engineer",
    "Full Stack Developer Reddit":         "Fullstack Engineer",
    "Instructional Designer":              "Instructional Designer",
    "[LinkedIn] OpenAI Community":         "OpenAI Community",
    "[Reddit] OpenAI Community":           "OpenAI Community",
}

BILATERAL = {"Backend Engineer", "Fullstack Engineer", "CTO", "Instructional Designer"}
WD_MIN = 30

# ── Helpers ───────────────────────────────────────────────────────────────────

# Named product/tool patterns -- these labels should stay specific
NAMED_RE = re.compile(
    r'\b(postgresql|postgres|mysql|sqlite|mongodb|redis|kafka|kubernetes|k8s|'
    r'docker|podman|react|vue|angular|svelte|next\.?js|nuxt|remix|astro|'
    r'node\.?js|deno|bun|python|rust|go|golang|typescript|javascript|java|'
    r'kotlin|swift|ruby|rails|django|fastapi|flask|spring|laravel|'
    r'aws|gcp|azure|terraform|ansible|pulumi|github|gitlab|bitbucket|'
    r'openai|anthropic|claude|gpt|llama|mistral|gemini|copilot|cursor|'
    r'graphql|grpc|rest|websocket|htmx|tailwind|shadcn|'
    r'linux|ubuntu|debian|alpine|windows|macos|'
    r'vercel|netlify|cloudflare|supabase|planetscale|neon|'
    r'elasticsearch|clickhouse|duckdb|snowflake|bigquery|databricks|'
    r'langchain|langgraph|llamaindex|autogen|crewai|'
    r'v\d+\.\d*|\d+\.\d+|release|launch|announcement|update|'
    r'cve-\d+|zero.?day)\b',
    re.IGNORECASE
)

# Version/release patterns
VERSION_RE = re.compile(r'\b\d+\.\d+|\bv\d+\b|release\s+\d+|version\s+\d+', re.IGNORECASE)

def word_set(s):
    return set(re.sub(r'[^a-z0-9]', ' ', s.lower()).split())

def jaccard(a, b):
    wa = word_set(a); wb = word_set(b)
    if not wa or not wb: return 0.0
    return len(wa & wb) / len(wa | wb)

def is_named_specific(label):
    return bool(NAMED_RE.search(label)) or bool(VERSION_RE.search(label))

def word_count(label):
    return len(label.strip().split())

def classify_label(label, freq2_labels, jaccard_threshold=0.5):
    """
    Returns (category, best_match, best_score):
    - named_specific: has named product/tool/version -- keep as-is
    - fixable_synonym: high Jaccard to a freq>=2 label -- collapse
    - distinct_concept: no named product, no good match -- new canonical topic candidate
    - noise: very long, ambiguous, or clearly extraction artifact
    """
    # Noise: very long labels
    if word_count(label) >= 7:
        return "noise", None, 0.0

    # Named specific
    if is_named_specific(label):
        # Even with named product, check if it is a near-synonym of freq>=2
        best_match = None; best_score = 0.0
        for fl in freq2_labels:
            if is_named_specific(fl):
                j = jaccard(label, fl)
                if j > best_score:
                    best_score = j; best_match = fl
        if best_score >= jaccard_threshold:
            return "fixable_synonym", best_match, best_score
        return "named_specific", best_match, best_score

    # Check Jaccard against all freq>=2 labels
    best_match = None; best_score = 0.0
    for fl in freq2_labels:
        j = jaccard(label, fl)
        if j > best_score:
            best_score = j; best_match = fl

    if best_score >= jaccard_threshold:
        return "fixable_synonym", best_match, best_score
    if best_score >= 0.3:
        return "weak_synonym", best_match, best_score
    return "distinct_concept", best_match, best_score


# ── Load ──────────────────────────────────────────────────────────────────────
print(f"Loading {INPUT} ...")

domain_li   = defaultdict(Counter)
domain_rd   = defaultdict(Counter)
cell_li     = defaultdict(lambda: defaultdict(Counter))
cell_rd     = defaultdict(lambda: defaultdict(Counter))

with open(INPUT, newline="", encoding="utf-8", errors="replace") as f:
    reader = csv.DictReader(f)
    for row in reader:
        if row.get("post_type", "").strip().lower() not in TRANSFER_TYPES:
            continue
        tb_raw = row.get("topic_broad", "").strip().lower()
        tb     = BROAD_FIX.get(tb_raw, tb_raw.title())
        topic  = row.get("topic", "").strip().lower()
        plat   = row.get("platform", "").strip().lower()
        aud    = AUDIENCE_MAP.get(row.get("audience_room_name", "").strip(), "")
        if not topic or tb not in ALL_DOMAINS:
            continue
        if plat == "linkedin":
            domain_li[tb][topic] += 1
            if aud in BILATERAL: cell_li[aud][tb][topic] += 1
        elif plat == "reddit":
            domain_rd[tb][topic] += 1
            if aud in BILATERAL: cell_rd[aud][tb][topic] += 1

print("Loaded.\n")
DIVIDER = "=" * 72

for DOMAIN in DOMAINS_FOCUS:
    li = domain_li[DOMAIN]
    rd = domain_rd[DOMAIN]
    combined = Counter()
    for k, v in li.items(): combined[k] += v
    for k, v in rd.items(): combined[k] += v

    total      = sum(combined.values())
    n_unique   = len(combined)
    singletons = {l: c for l, c in combined.items() if c == 1}
    freq2plus  = {l: c for l, c in combined.items() if c >= 2}
    freq2_list = list(freq2plus.keys())

    print(f"\n{'#'*72}")
    print(f"# DOMAIN: {DOMAIN}")
    print(f"# {total:,} posts  |  {n_unique:,} unique labels  |  "
          f"{len(singletons):,} singletons ({len(singletons)/n_unique*100:.1f}%)")
    print(f"{'#'*72}")

    # ── Section 1: Frequency distribution ────────────────────────────────────
    print(f"\n{DIVIDER}")
    print(f"1. FREQUENCY DISTRIBUTION")
    print(DIVIDER)
    buckets = {
        "freq 1 (singletons)":    sum(1 for v in combined.values() if v == 1),
        "freq 2-3":               sum(1 for v in combined.values() if 2 <= v <= 3),
        "freq 4-9":               sum(1 for v in combined.values() if 4 <= v <= 9),
        "freq 10-29":             sum(1 for v in combined.values() if 10 <= v <= 29),
        "freq 30-99":             sum(1 for v in combined.values() if 30 <= v <= 99),
        "freq 100+":              sum(1 for v in combined.values() if v >= 100),
    }
    posts_in_bucket = {
        "freq 1 (singletons)":    sum(v for v in combined.values() if v == 1),
        "freq 2-3":               sum(v for v in combined.values() if 2 <= v <= 3),
        "freq 4-9":               sum(v for v in combined.values() if 4 <= v <= 9),
        "freq 10-29":             sum(v for v in combined.values() if 10 <= v <= 29),
        "freq 30-99":             sum(v for v in combined.values() if 30 <= v <= 99),
        "freq 100+":              sum(v for v in combined.values() if v >= 100),
    }
    print(f"\n  {'Bucket':<25} {'Labels':>8} {'Posts':>8} {'% posts':>9}")
    print(f"  {'-'*25} {'-'*8} {'-'*8} {'-'*9}")
    for bucket, n_labels in buckets.items():
        n_posts = posts_in_bucket[bucket]
        print(f"  {bucket:<25} {n_labels:>8,} {n_posts:>8,} {n_posts/total*100:>8.1f}%")

    # ── Section 2: Singleton anatomy ─────────────────────────────────────────
    print(f"\n{DIVIDER}")
    print(f"2. SINGLETON ANATOMY")
    print(DIVIDER)

    cats = defaultdict(list)
    cat_with_match = defaultdict(list)

    for label in singletons:
        cat, match, score = classify_label(label, freq2_list)
        cats[cat].append(label)
        if match:
            cat_with_match[cat].append((label, match, score))

    for cat in ["fixable_synonym", "weak_synonym", "named_specific",
                "distinct_concept", "noise"]:
        labels = cats[cat]
        pct_sing = len(labels)/len(singletons)*100
        pct_all  = len(labels)/total*100
        print(f"\n  {cat.upper()}: {len(labels):,} labels "
              f"({pct_sing:.1f}% of singletons, {pct_all:.1f}% of all posts)")

        # Show best examples
        examples = sorted(
            cat_with_match[cat], key=lambda x: -x[2]
        )[:8] if cat_with_match[cat] else [(l, None, 0) for l in labels[:8]]

        for label, match, score in examples:
            if match:
                print(f"    {label:<45} -> {match} ({freq2plus.get(match,0)}) "
                      f"[{score:.2f}]")
            else:
                print(f"    {label}")

    # ── Section 3: Synonym cluster simulation ─────────────────────────────────
    print(f"\n{DIVIDER}")
    print(f"3. SYNONYM CLUSTER SIMULATION")
    print(f"   For each freq>=2 seed, how many singletons cluster around it?")
    print(f"   Uses Jaccard >= 0.5 threshold (conservative)")
    print(DIVIDER)

    # Build clusters
    seed_clusters = defaultdict(list)  # seed -> [singleton labels]
    unclustered   = []

    for label in singletons:
        cat, match, score = classify_label(label, freq2_list, jaccard_threshold=0.5)
        if cat == "fixable_synonym" and match:
            seed_clusters[match].append((label, score))
        else:
            unclustered.append(label)

    # Combined frequency after clustering
    seed_combined = Counter(freq2plus)  # start with freq>=2 counts
    for seed, members in seed_clusters.items():
        seed_combined[seed] += len(members)

    # Print top clusters
    top_clusters = sorted(
        [(seed, combined_freq, seed_clusters.get(seed, []))
         for seed, combined_freq in seed_combined.items()
         if seed_clusters.get(seed)],
        key=lambda x: -x[1]
    )[:20]

    print(f"\n  Top 20 clusters (seed + absorbed singletons):")
    print(f"  {'Seed':<40} {'Base':>5} {'Absorbed':>9} {'Total':>7}")
    print(f"  {'-'*40} {'-'*5} {'-'*9} {'-'*7}")
    for seed, total_freq, members in top_clusters:
        base = freq2plus.get(seed, 0)
        absorbed = len(members)
        print(f"  {seed:<40} {base:>5} {absorbed:>9} {total_freq:>7}")
        # Show absorbed members
        for member, score in sorted(members, key=lambda x: -x[1])[:3]:
            print(f"    + {member} [{score:.2f}]")

    # ── Section 4: Distinct concept candidates ────────────────────────────────
    print(f"\n{DIVIDER}")
    print(f"4. DISTINCT CONCEPT CANDIDATES")
    print(f"   Singletons that don't map to any freq>=2 seed.")
    print(f"   These BECOME new canonical topics if freq warrants it.")
    print(f"   (Currently freq=1 but post-sprint volume may increase them)")
    print(DIVIDER)

    distinct = [(l, classify_label(l, freq2_list, 0.5)) for l in unclustered
                if classify_label(l, freq2_list, 0.5)[0] in
                ("distinct_concept", "weak_synonym", "named_specific")]

    # Group by broad theme (first 2 words)
    theme_groups = defaultdict(list)
    for label, (cat, match, score) in distinct:
        theme = " ".join(label.split()[:2])
        theme_groups[theme].append((label, cat, score))

    print(f"\n  {len(distinct):,} distinct concept singletons")
    print(f"  Top theme groups (first 2 words of label):")
    top_themes = sorted(theme_groups.items(), key=lambda x: -len(x[1]))[:20]
    print(f"\n  {'Theme prefix':<30} {'Count':>6}  Examples")
    print(f"  {'-'*30} {'-'*6}  {'-'*40}")
    for theme, members in top_themes:
        examples = ", ".join(l for l, _, _ in members[:3])
        print(f"  {theme:<30} {len(members):>6}  {examples}")

    # ── Section 5: Simulated post-normalisation vocabulary ────────────────────
    print(f"\n{DIVIDER}")
    print(f"5. SIMULATED POST-NORMALISATION VOCABULARY")
    print(f"   Assumes: fixable_synonym singletons -> best seed (Jaccard >= 0.5)")
    print(f"            weak_synonym singletons    -> best seed (Jaccard >= 0.3)")
    print(f"            distinct_concept           -> new canonical topic")
    print(f"            named_specific             -> kept as-is (specific stimulus)")
    print(f"            noise                      -> dropped")
    print(DIVIDER)

    simulated = Counter(freq2plus)
    n_collapsed_strong = 0
    n_collapsed_weak   = 0
    n_new_topics       = 0
    n_kept_specific    = 0
    n_dropped_noise    = 0

    for label in singletons:
        cat, match, score = classify_label(label, freq2_list, 0.3)
        if cat == "fixable_synonym" and score >= 0.5:
            simulated[match] += 1
            n_collapsed_strong += 1
        elif cat in ("fixable_synonym", "weak_synonym") and score >= 0.3:
            simulated[match] += 1
            n_collapsed_weak += 1
        elif cat == "distinct_concept":
            simulated[label] += 1  # becomes its own canonical topic
            n_new_topics += 1
        elif cat == "named_specific":
            simulated[label] += 1  # keep
            n_kept_specific += 1
        else:
            n_dropped_noise += 1   # noise dropped

    total_simulated = sum(simulated.values())
    n_vocab         = len(simulated)
    n_sing_sim      = sum(1 for v in simulated.values() if v == 1)
    n_10plus        = sum(1 for v in simulated.values() if v >= 10)
    n_30plus        = sum(1 for v in simulated.values() if v >= 30)

    print(f"\n  Disposition of singletons:")
    print(f"    Strong synonym collapse (Jaccard>=0.5): {n_collapsed_strong:,}")
    print(f"    Weak synonym collapse   (Jaccard>=0.3): {n_collapsed_weak:,}")
    print(f"    Kept as new canonical topic:            {n_new_topics:,}")
    print(f"    Kept as named specific:                 {n_kept_specific:,}")
    print(f"    Dropped (noise):                        {n_dropped_noise:,}")

    print(f"\n  Simulated vocabulary stats:")
    print(f"    Unique labels:          {n_vocab:,} (was {n_unique:,})")
    print(f"    Remaining singletons:   {n_sing_sim:,} ({n_sing_sim/n_vocab*100:.1f}%)")
    print(f"    Labels with freq>=10:   {n_10plus:,}")
    print(f"    Labels with freq>=30:   {n_30plus:,}")
    print(f"    Posts covered (non-singleton vocab): "
          f"{sum(v for v in simulated.values() if v>=2):,} "
          f"({sum(v for v in simulated.values() if v>=2)/total*100:.1f}%)")

    # Coverage at different thresholds
    for threshold in [2, 5, 10, 30]:
        covered = sum(v for v in simulated.values() if v >= threshold)
        n_labels_t = sum(1 for v in simulated.values() if v >= threshold)
        print(f"    Coverage at freq>={threshold:<3}: "
              f"{covered:,} posts ({covered/total*100:.1f}%) "
              f"from {n_labels_t:,} labels")

    print(f"\n  Top-20 canonical topics after normalisation:")
    print(f"  {'Label':<45} {'Simulated freq':>15} {'Base freq':>11}")
    print(f"  {'-'*45} {'-'*15} {'-'*11}")
    for label, freq in simulated.most_common(20):
        base = freq2plus.get(label, 1)  # 1 if it was a singleton that became new
        print(f"  {label:<45} {freq:>15,} {base:>11,}")

    # ── Section 6: Bilateral viability re-assessment ─────────────────────────
    print(f"\n{DIVIDER}")
    print(f"6. BILATERAL VIABILITY RE-ASSESSMENT (>= {WD_MIN} per side)")
    print(f"   Applies simulated normalisation to bilateral cells.")
    print(DIVIDER)

    for aud in sorted(BILATERAL):
        li_cell = cell_li[aud][DOMAIN]
        rd_cell = cell_rd[aud][DOMAIN]
        if not li_cell or not rd_cell:
            continue
        li_total = sum(li_cell.values())
        rd_total = sum(rd_cell.values())
        if li_total < 30 or rd_total < 10:
            continue

        # Apply simulation to cell
        li_sim = Counter()
        rd_sim = Counter()

        def apply_sim(source, target):
            for label, count in source.items():
                if label in freq2plus:
                    target[label] += count
                else:
                    cat, match, score = classify_label(label, freq2_list, 0.3)
                    if cat == "fixable_synonym" and score >= 0.5 and match:
                        target[match] += count
                    elif cat in ("fixable_synonym","weak_synonym") and score >= 0.3 and match:
                        target[match] += count
                    elif cat in ("distinct_concept","named_specific"):
                        target[label] += count
                    # noise dropped

        apply_sim(li_cell, li_sim)
        apply_sim(rd_cell, rd_sim)

        all_topics   = set(li_sim.keys()) | set(rd_sim.keys())
        viable       = [(t, li_sim[t], rd_sim[t]) for t in all_topics
                        if li_sim[t] >= WD_MIN and rd_sim[t] >= WD_MIN]
        near_viable  = [(t, li_sim[t], rd_sim[t]) for t in all_topics
                        if (li_sim[t] >= 10 or rd_sim[t] >= 10)
                        and not (li_sim[t] >= WD_MIN and rd_sim[t] >= WD_MIN)]

        if li_total < 50 and rd_total < 20:
            continue

        print(f"\n  {aud} x {DOMAIN}  (LI:{li_total}  RD:{rd_total})")
        print(f"  Viable (>={WD_MIN} both): {len(viable)}")
        if viable:
            for t, lc, rc in sorted(viable, key=lambda x:-(x[1]+x[2])):
                print(f"    {t:<50} LI:{lc:>4}  RD:{rc:>4}")
        print(f"  Near-viable (10+ one side): {len(near_viable)}")
        for t, lc, rc in sorted(near_viable, key=lambda x:-(x[1]+x[2]))[:6]:
            print(f"    {t:<50} LI:{lc:>4}  RD:{rc:>4}")

    # ── Section 7: Representative samples for human review ───────────────────
    print(f"\n{DIVIDER}")
    print(f"7. REPRESENTATIVE SAMPLES FOR HUMAN REVIEW")
    print(DIVIDER)

    print(f"\n  A. STRONG SYNONYMS that will collapse (Jaccard >= 0.5):")
    shown = 0
    for label in singletons:
        cat, match, score = classify_label(label, freq2_list, 0.5)
        if cat == "fixable_synonym" and score >= 0.5:
            print(f"    {label:<45} -> {match} [J={score:.2f}]")
            shown += 1
            if shown >= 15: break

    print(f"\n  B. WEAK SYNONYMS -- model should get but heuristic uncertain (0.3-0.5):")
    shown = 0
    for label in singletons:
        cat, match, score = classify_label(label, freq2_list, 0.3)
        if cat in ("fixable_synonym","weak_synonym") and 0.3 <= score < 0.5:
            print(f"    {label:<45} -> {match} [J={score:.2f}]")
            shown += 1
            if shown >= 15: break

    print(f"\n  C. DISTINCT CONCEPTS -- should become new canonical topics:")
    shown = 0
    for label in singletons:
        cat, match, score = classify_label(label, freq2_list, 0.3)
        if cat == "distinct_concept":
            print(f"    {label}")
            shown += 1
            if shown >= 20: break

    print(f"\n  D. NAMED SPECIFICS -- correct, keep as-is:")
    shown = 0
    for label in singletons:
        cat, match, score = classify_label(label, freq2_list, 0.3)
        if cat == "named_specific":
            print(f"    {label}")
            shown += 1
            if shown >= 15: break

    print(f"\n  E. OVER-SPECIFIC/NOISE -- should be dropped or folded:")
    shown = 0
    for label in singletons:
        cat, match, score = classify_label(label, freq2_list, 0.3)
        if cat == "noise":
            print(f"    {label}")
            shown += 1
            if shown >= 10: break

print(f"\n{DIVIDER}")
print("Done.")
