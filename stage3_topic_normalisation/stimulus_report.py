"""
stimulus_report.py
-------------------
Characterises the stimuli already captured in tagged_posts_v2_normalised.csv,
via the topic and topic_narrow fields, per Serina's request to see the actual
canonical topic list and per Anaant's framing that the database already
captures stimuli at two levels:

  - topic_narrow: named-event stimulus (specific datable occurrence)
  - topic: abstracted stimulus (recurring concern / subject category)

This script does NOT impose a definition. It quantifies what exists, lets
natural categories emerge from inspection of the actual label list, and
surfaces the ambiguous zone for manual review rather than auto-classifying it.

Outputs:
  stimulus_report.txt        -- full breakdown, human readable
  named_event_stimuli.csv    -- every post where topic_narrow != topic,
                                 for manual typology coding
  abstracted_stimuli.csv     -- the non-singleton canonical topics (topic field),
                                 sorted by frequency, for manual typology coding

Usage:
    python stimulus_report.py
    python stimulus_report.py --config config_vocab.yaml
"""

import argparse
import csv
import os
import sys
import io
from collections import defaultdict
from pathlib import Path

import yaml

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

csv.field_size_limit(10_000_000)

DEFAULTS = {
    "normalised_output": "tagged_posts_v2_normalised.csv",
    "domains": [
        "Software Engineering", "AI", "Security", "EdTech", "Career",
        "Business & Strategy", "Data & Analytics",
        "Leadership & Management", "Open Source",
    ],
    "transfer_types": ["opinion", "news_reaction"],
}

def load_config(path):
    cfg = DEFAULTS.copy()
    config_path = Path(os.path.dirname(os.path.abspath(__file__))) / path
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            overrides = yaml.safe_load(f) or {}
        cfg.update(overrides)
    return cfg

def resolve(here, p):
    return os.path.join(here, p)


def run(cfg_path="config_vocab.yaml"):
    cfg  = load_config(cfg_path)
    HERE = os.path.dirname(os.path.abspath(__file__))
    NORM_PATH = resolve(HERE, cfg["normalised_output"])
    DOMAINS   = cfg["domains"]
    TRANSFER_TYPES = set(cfg["transfer_types"])

    OUT_REPORT  = resolve(HERE, "stimulus_report.txt")
    OUT_NAMED   = resolve(HERE, "named_event_stimuli.csv")
    OUT_ABSTRACT = resolve(HERE, "abstracted_stimuli.csv")

    print(f"Loading {NORM_PATH} ...")
    all_posts = []
    with open(NORM_PATH, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            all_posts.append(row)
    print(f"  {len(all_posts):,} total posts in file.")

    # SCOPE: restrict strictly to the nine normalised domains. Healthcare, Other,
    # and blank topic_broad were never processed by the normalisation pipeline
    # and are explicitly out of scope here.
    posts = [p for p in all_posts if p.get("topic_broad", "") in DOMAINS]
    excluded = len(all_posts) - len(posts)
    print(f"  {len(posts):,} posts within the nine normalised domains (in scope).")
    print(f"  {excluded:,} posts excluded (outside normalisation scope).\n")

    transfer_posts = [p for p in posts if p.get("post_type", "") in TRANSFER_TYPES]

    # ── 1. Named-event stimuli: topic_narrow != topic ──────────────────────────
    named_event_rows = []
    for p in transfer_posts:
        topic = (p.get("topic") or "").strip().lower()
        narrow = (p.get("topic_narrow") or "").strip().lower()
        if narrow and topic and narrow != topic:
            named_event_rows.append(p)

    named_event_freq = defaultdict(int)
    named_event_domain_freq = defaultdict(lambda: defaultdict(int))
    for p in named_event_rows:
        narrow = (p.get("topic_narrow") or "").strip().lower()
        domain = p.get("topic_broad", "")
        named_event_freq[narrow] += 1
        named_event_domain_freq[domain][narrow] += 1

    n_named_event_posts = len(named_event_rows)
    n_named_event_unique = len(named_event_freq)
    pct_transfer_with_named_event = n_named_event_posts / len(transfer_posts) * 100 if transfer_posts else 0

    # Named event frequency distribution
    named_event_singletons = sum(1 for f in named_event_freq.values() if f == 1)
    named_event_recurring = {k: v for k, v in named_event_freq.items() if v >= 2}

    # ── 2. Abstracted stimuli: non-singleton canonical topics (topic field) ────
    # IMPORTANT: counted per (domain, topic) pair, not by topic string alone.
    # The same topic string can be a distinct canonical topic in different
    # domains (e.g. "ai adoption challenges" in both AI and Career) -- collapsing
    # across domains would undercount the true canonical topic total and was a
    # real bug in the previous version of this script. This matches the
    # methodology in master_numbers.py, which sums per-domain unique counts.
    topic_freq_by_domain = defaultdict(lambda: defaultdict(int))
    topic_freq_by_domain_topic_key = defaultdict(int)  # key: (domain, topic)
    for p in posts:
        topic = (p.get("topic") or "").strip().lower()
        domain = p.get("topic_broad", "")
        if topic and topic not in ("unclassified", ""):
            topic_freq_by_domain[domain][topic] += 1
            topic_freq_by_domain_topic_key[(domain, topic)] += 1

    non_singleton_topics_keyed = {k: v for k, v in topic_freq_by_domain_topic_key.items() if v >= 2}
    singleton_topics_keyed = {k: v for k, v in topic_freq_by_domain_topic_key.items() if v == 1}

    # Flat overall freq dict (topic string -> total across all domains) is kept
    # ONLY for the "top 30 highest frequency" display, where cross-domain totals
    # are informative context, not a count of distinct canonical topics.
    topic_freq_overall_display = defaultdict(int)
    for (domain, topic), freq in topic_freq_by_domain_topic_key.items():
        topic_freq_overall_display[topic] += freq

    n_total_canonical = len(topic_freq_by_domain_topic_key)
    n_non_singleton = len(non_singleton_topics_keyed)
    n_singleton = len(singleton_topics_keyed)

    # Per domain non-singleton counts
    domain_non_singleton = {}
    for domain in DOMAINS:
        d_freq = topic_freq_by_domain.get(domain, {})
        d_non_sing = {k: v for k, v in d_freq.items() if v >= 2}
        domain_non_singleton[domain] = d_non_sing

    # ── 3. The overlap / ambiguous zone ─────────────────────────────────────────
    # Topics that appear BOTH as a non-singleton canonical topic AND have
    # named-event variants under them (i.e. topic_narrow diversity within one topic).
    # Keyed by (domain, topic) for the same cross-domain-collision reason as above.
    topic_to_narrow_variants = defaultdict(set)
    for p in transfer_posts:
        topic = (p.get("topic") or "").strip().lower()
        narrow = (p.get("topic_narrow") or "").strip().lower()
        domain = p.get("topic_broad", "")
        if topic and narrow and narrow != topic:
            topic_to_narrow_variants[(domain, topic)].add(narrow)

    ambiguous_topics = {
        k: variants for k, variants in topic_to_narrow_variants.items()
        if len(variants) >= 3  # topic has 3+ distinct named events under it -- candidate for being "too broad" / aspect-like
    }

    # ── Write report ──────────────────────────────────────────────────────────
    lines = []
    lines.append("=" * 90)
    lines.append("STIMULUS REPORT")
    lines.append("Characterising stimuli already captured via topic and topic_narrow")
    lines.append("tagged_posts_v2_normalised.csv")
    lines.append("=" * 90)
    lines.append("")
    lines.append("This report quantifies what exists. It does not impose a definition.")
    lines.append("Categories are for manual coding, not auto-classification.")
    lines.append("")
    lines.append(f"SCOPE: the nine normalised domains only ({len(posts):,} posts).")
    lines.append("Healthcare, Other, and posts with no topic_broad value are out of scope")
    lines.append("and excluded entirely from every count in this report.")
    lines.append("")

    lines.append("-" * 90)
    lines.append("1. NAMED-EVENT STIMULI (topic_narrow field)")
    lines.append("-" * 90)
    lines.append("")
    lines.append(f"  Transfer-eligible posts (opinion + news_reaction): {len(transfer_posts):,}")
    lines.append(f"  Posts with a named event (topic_narrow != topic):  {n_named_event_posts:,} ({pct_transfer_with_named_event:.1f}%)")
    lines.append(f"  Unique named events:                                {n_named_event_unique:,}")
    lines.append(f"  Named events appearing on exactly 1 post:           {named_event_singletons:,} ({named_event_singletons/n_named_event_unique*100:.1f}% of unique named events)" if n_named_event_unique else "  Named events appearing on exactly 1 post: 0")
    lines.append(f"  Named events appearing on 2+ posts (recurring):     {len(named_event_recurring):,}")
    lines.append("")
    lines.append("  Named events by domain (posts with a named event):")
    for domain in DOMAINS:
        d_named = named_event_domain_freq.get(domain, {})
        d_total_named_posts = sum(d_named.values())
        d_unique_named = len(d_named)
        lines.append(f"    {domain:<32} {d_total_named_posts:>5,} posts  |  {d_unique_named:>4,} unique named events")
    lines.append("")
    lines.append("  Top 20 most frequent named events (candidates for recurring/periodic events,")
    lines.append("  e.g. annual conferences, recurring product cycles -- vs one-off news):")
    for name, freq in sorted(named_event_freq.items(), key=lambda x: -x[1])[:20]:
        lines.append(f"    {name:<60} {freq:>4}")
    lines.append("")

    lines.append("-" * 90)
    lines.append("2. ABSTRACTED STIMULI (topic field, non-singleton canonical topics)")
    lines.append("-" * 90)
    lines.append("")
    lines.append(f"  Total canonical topics (topic field):        {n_total_canonical:,}")
    lines.append(f"  Non-singleton canonical topics (freq >= 2):  {n_non_singleton:,}")
    lines.append(f"  Singleton canonical topics (freq == 1):      {n_singleton:,}")
    lines.append("")
    lines.append("  Non-singleton canonical topics by domain:")
    for domain in DOMAINS:
        d_non_sing = domain_non_singleton.get(domain, {})
        d_total_posts_covered = sum(d_non_sing.values())
        lines.append(f"    {domain:<32} {len(d_non_sing):>4,} canonical topics  |  {d_total_posts_covered:>6,} posts covered")
    lines.append("")
    lines.append("  Top 30 highest-frequency abstracted stimuli (cross-domain totals shown for")
    lines.append("  context; a topic appearing in multiple domains is counted as a separate")
    lines.append("  canonical topic per domain in the totals above, but summed here for display):")
    for name, freq in sorted(topic_freq_overall_display.items(), key=lambda x: -x[1])[:30]:
        lines.append(f"    {name:<60} {freq:>4}")
    lines.append("")

    lines.append("-" * 90)
    lines.append("3. AMBIGUOUS ZONE")
    lines.append("-" * 90)
    lines.append("")
    lines.append("  Topics (from the topic field) that have 3+ distinct named events")
    lines.append("  (topic_narrow variants) underneath them. These are candidates for being")
    lines.append("  too broad / aspect-like rather than a single coherent stimulus --")
    lines.append("  e.g. 'ai in education' covering many distinct underlying events.")
    lines.append("  Manual review needed: is this one recurring stimulus, or several")
    lines.append("  distinct stimuli that happen to share a topic label?")
    lines.append("")
    lines.append(f"  {len(ambiguous_topics):,} topics flagged for review.")
    lines.append("")
    for (domain, topic), variants in sorted(ambiguous_topics.items(), key=lambda x: -len(x[1]))[:25]:
        lines.append(f"    {topic:<40} [{domain:<24}] {len(variants):>3} distinct named events underneath")
    lines.append("")

    lines.append("-" * 90)
    lines.append("4. SUMMARY FOR TYPOLOGY CODING")
    lines.append("-" * 90)
    lines.append("")
    lines.append(f"  named_event_stimuli.csv contains {n_named_event_posts:,} posts (one row per post)")
    lines.append(f"  with their topic_narrow value, for manual categorisation into types")
    lines.append(f"  (product release, security incident, policy change, conference/event,")
    lines.append(f"  M&A, other -- categories to be derived from reading the actual list,")
    lines.append(f"  not imposed in advance).")
    lines.append("")
    lines.append(f"  abstracted_stimuli.csv contains {n_non_singleton:,} canonical topic-domain pairs")
    lines.append(f"  (one row per topic per domain) with frequency, for manual categorisation")
    lines.append(f"  into types (recurring technical concern, market/career condition,")
    lines.append(f"  recurring debate, other).")
    lines.append("")
    lines.append("=" * 90)

    report_text = "\n".join(lines)
    print(report_text)

    with open(OUT_REPORT, "w", encoding="utf-8") as f:
        f.write(report_text)

    # ── Write named_event_stimuli.csv ────────────────────────────────────────
    with open(OUT_NAMED, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["topic_narrow", "topic", "topic_broad", "platform", "frequency_of_this_named_event", "typology_category"])
        seen_narrows = set()
        for p in sorted(named_event_rows, key=lambda x: -named_event_freq[(x.get("topic_narrow") or "").strip().lower()]):
            narrow = (p.get("topic_narrow") or "").strip().lower()
            key = (narrow, p.get("topic_broad", ""))
            if key in seen_narrows:
                continue
            seen_narrows.add(key)
            writer.writerow([
                narrow,
                (p.get("topic") or "").strip().lower(),
                p.get("topic_broad", ""),
                p.get("platform", ""),
                named_event_freq[narrow],
                ""  # blank column for manual typology coding
            ])

    # ── Write abstracted_stimuli.csv ─────────────────────────────────────────
    # One row per (domain, topic) pair -- the same topic string in two domains
    # is two separate rows, matching how it was counted above.
    with open(OUT_ABSTRACT, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["topic", "domain", "frequency", "typology_category"])
        for (domain, topic), freq in sorted(non_singleton_topics_keyed.items(), key=lambda x: -x[1]):
            writer.writerow([
                topic,
                domain,
                freq,
                ""  # blank column for manual typology coding
            ])

    print(f"\nWritten:")
    print(f"  {OUT_REPORT}")
    print(f"  {OUT_NAMED}")
    print(f"  {OUT_ABSTRACT}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config_vocab.yaml")
    args = parser.parse_args()
    run(args.config)