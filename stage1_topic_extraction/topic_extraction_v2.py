"""
topic_extraction_v2.py
----------------------
Single-pass topic labelling per Serina's spec — with full opinion structure,
author diversity tracking, streaming, checkpointing, structured logging,
and config.yaml support.

Outputs:
    tagged_posts_v2.csv       all posts with full labels
    checkpoint_v2.jsonl       resumption state — delete to start fresh
    topic_extraction_v2.log   full execution record

    paired_dataset_v2.csv is produced by build_pairs.py (run separately)

Usage:
    python topic_extraction_v2.py
    python topic_extraction_v2.py --config config_fullrun.yaml

Author: Ananth / Vectorial AI — June 2026
"""

import argparse
import csv
import json
import logging
import os
import random
import sys
import io
import time
from collections import defaultdict
from pathlib import Path

import yaml
from openai import OpenAI
from tqdm import tqdm

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

csv.field_size_limit(10_000_000)

# ── Config ────────────────────────────────────────────────────────────────────

def load_config(path: str = "config.yaml") -> dict:
    config_path = Path(os.path.dirname(os.path.abspath(__file__))) / path
    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return cfg

def resolve(here: str, p: str) -> str:
    return os.path.join(here, p)

# ── Validation sets ───────────────────────────────────────────────────────────

VALID_SENTIMENTS  = {"positive", "negative", "neutral", "mixed"}
VALID_STANCES     = {"assertive", "critical", "supportive", "questioning", "informational"}
VALID_POST_TYPES  = {"opinion", "news_reaction", "question", "personal_milestone",
                     "promotional", "show_and_tell"}
VALID_TOPIC_BROAD = {"AI", "Software Engineering", "Career", "EdTech", "Healthcare",
                     "Leadership & Management", "Security", "Data & Analytics",
                     "Open Source", "Business & Strategy", "Other"}

# ── Prompt ────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are labelling social media posts for a cross-platform behavioural research study at frontier labs.

## The research task

We are studying how the same professional audience discusses the same subjects differently
across platforms — LinkedIn vs Reddit. To do this, we need to group posts by what triggered
them: the underlying topic or event that caused the person to write.

A post is made of two things fused together:
- The STIMULUS: what triggered the post — a news event, an ongoing debate, a recurring
  frustration, a technical question
- The OPINION: how this particular person responded — their framing, their stance,
  the specific angles they chose to address

Your job is to extract both. Two posts with completely different opinions but written in
response to the same underlying thing must get the same topic labels regardless of their
opposing views.

Example: An AI engineer on LinkedIn writing "This is a terrible decision by Anthropic" and
a Reddit engineer writing "Finally, someone is holding them accountable" are both responding
to the same stimulus — Anthropic's policy change. Same topic labels. But different sentiment
(negative vs positive) and different subtopics within their opinions.

## Why consistent topic labels matter

Posts sharing a topic label get grouped into a cell: (audience x topic). We count how many
LinkedIn posts and Reddit posts are in each cell. Inconsistent labels fragment cells and
destroy the comparison. Labels must be identical for the same underlying subject across
platforms, audiences, and batches.

## The three-level topic hierarchy

BROAD (topic_broad): The domain. Use ONLY these exact 11 values — no others:
  AI, Software Engineering, Career, EdTech, Healthcare,
  Leadership & Management, Security, Data & Analytics,
  Open Source, Business & Strategy, Other

  These are the ONLY valid values. Using any other value — "Marketing",
  "DevOps", "Finance", "Product", "Business", or anything else — is an error.
  If a post does not fit cleanly into one of the 11, use "Other".

MID (topic): The specific subject, 2-5 words, lowercase, consistent across posts.
  Abstract away from specific tool or product names to the underlying subject.
  Ask: what would someone search for to find posts like this?

  Good: "llm fine-tuning", "api security vulnerabilities", "engineering hiring market"
  Bad: "nestjs-xsecurity release", "click guardian app", "laravel package launch"

  If a post announces a specific tool -> label the category: "api authentication tooling"
  If a post reacts to a news event -> label the subject: "ai product pricing models"

NARROW (topic_narrow): The most specific angle — a named event, specific debate, particular
  release — if one exists. If the post is a general opinion, repeat the mid-level topic.

  Named event: "anthropic claude code pro plan removal"
  Named event: "npm supply chain attack may 2024"
  General opinion: "llm fine-tuning" (same as topic)

## Opinion structure

opinion_subtopics: A list of 2-4 specific angles the opinion addresses WITHIN the topic.
  These are the sub-dimensions the person chose to talk about within their response.

  For a post about "llm fine-tuning":
    ["training cost", "evaluation quality", "lora vs full fine-tune"]
  For a post about "engineering hiring market":
    ["leetcode interview culture", "compensation expectations"]

  Keep each subtopic to 2-4 words, lowercase. These will be used to compare what aspects
  LinkedIn vs Reddit users focus on for the same topic — the core research signal.

  If the post has no real opinion (promotional, personal milestone), return an empty list.

## Sentiment and stance

sentiment: The overall emotional direction of the post toward its subject.
  Values: "positive", "negative", "neutral", "mixed"

  Positive = the author feels good about this — favorable, optimistic, approving
  Negative = the author feels bad about this — frustrated, disappointed, alarmed
  Neutral  = no clear emotional charge — factual, balanced, descriptive
  Mixed    = the author sees both upsides and downsides

  IMPORTANT: sentiment is an emotional charge, not a rhetorical posture.
  Do not use stance values as sentiment values. "questioning", "assertive",
  "supportive", "critical", "informational" are all invalid sentiment values.
  If a post asks a question, its sentiment is still "neutral" or "mixed"
  depending on emotional tone — not "questioning".

stance: The rhetorical posture of the post.
  Values: "assertive", "critical", "supportive", "questioning", "informational"

  Assertive     = stating a strong position
  Critical      = pushing back on something or someone
  Supportive    = endorsing or defending
  Questioning   = asking or expressing uncertainty
  Informational = sharing facts or news with minimal opinion

## Post type

post_type: What kind of post this is. Critical for filtering transfer pairs.

  "opinion"            — genuine opinion, reaction, or take on a subject.
                         BEST for transfer research. Has Reddit equivalents.
  "news_reaction"      — reacting to news from OUTSIDE your own organisation.
                         BEST for transfer research. Cross-platform by nature.
                         IMPORTANT: if the author works at the company being
                         announced ("we just launched", "our team shipped",
                         "I'm proud to share that my company..."), label it
                         "promotional" instead — news reactions come from
                         observers, not insiders.
  "question"           — primarily asking something, seeking input.
  "personal_milestone" — joining a company, promotion, certification, work anniversary.
                         This applies to the individual's own career events only.
                         Job postings on behalf of a company are "promotional" even
                         when written in first person ("We're hiring", "I'm looking
                         for a great engineer to join my team").
  "promotional"        — webinar invite, speaking announcement, product launch by an
                         insider, event promotion, job postings on behalf of a company.
                         LinkedIn-native, no Reddit equivalent.
  "show_and_tell"      — "I built X" posts sharing a personal project or release.

Only "opinion" and "news_reaction" will be used in the transfer dataset.
Label carefully — mislabelling a promotional post as news_reaction or opinion
inflates LinkedIn cells with non-transferable content.

IMPORTANT — valid post_type values: opinion, news_reaction, question,
personal_milestone, promotional, show_and_tell. These are the ONLY six.
Do not invent new values like "informational". If a post is purely sharing
information with no opinion, use "news_reaction" if it references a specific
external event, "show_and_tell" if sharing something the author made, or
"question" if seeking input. When in doubt, use "opinion".

## Return format

Return ONLY a valid JSON array — one object per post, same order as input.
No preamble, no markdown fences, no explanation.

Each object:
{
  "idx": <integer, the index from the input>,
  "topic_broad": "<one of the 11 fixed vocabulary terms>",
  "topic": "<mid-level subject, 2-5 words, lowercase>",
  "topic_narrow": "<specific event or angle, or repeat topic if general>",
  "opinion_subtopics": ["<2-4 words>", "<2-4 words>"],
  "sentiment": "<positive | negative | neutral | mixed>",
  "stance": "<assertive | critical | supportive | questioning | informational>",
  "post_type": "<opinion | news_reaction | question | personal_milestone | promotional | show_and_tell>"
}"""


# ── Logger ────────────────────────────────────────────────────────────────────

def setup_logger(log_path: str) -> logging.Logger:
    logger = logging.getLogger("topic_extraction")
    if logger.handlers:
        return logger   # already configured — don't add duplicate handlers
    logger.setLevel(logging.DEBUG)

    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)

    ch = logging.StreamHandler()
    ch.setLevel(logging.WARNING)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    fh.setFormatter(fmt)
    ch.setFormatter(fmt)

    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger


# ── Checkpoint ────────────────────────────────────────────────────────────────

def load_checkpoint(path: str) -> dict[int, dict]:
    done = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    post = json.loads(line)
                    done[int(post["_idx"])] = post
    return done

def save_to_checkpoint(path: str, posts: list[dict]):
    with open(path, "a", encoding="utf-8") as f:
        for post in posts:
            # keep _idx — required for resume; strip all other internal keys
            clean = {k: v for k, v in post.items()
                     if not k.startswith("_") or k == "_idx"}
            f.write(json.dumps(clean, ensure_ascii=False) + "\n")


# ── Validation ────────────────────────────────────────────────────────────────

def validate_and_log(post: dict, logger: logging.Logger):
    idx = post.get("_idx", "?")
    if post.get("topic_broad") not in VALID_TOPIC_BROAD:
        logger.warning(f"INVALID_BROAD idx={idx} value={post.get('topic_broad')!r}")
    if post.get("sentiment") not in VALID_SENTIMENTS:
        logger.warning(f"INVALID_SENTIMENT idx={idx} value={post.get('sentiment')!r}")
    if post.get("stance") not in VALID_STANCES:
        logger.warning(f"INVALID_STANCE idx={idx} value={post.get('stance')!r}")
    if post.get("post_type") not in VALID_POST_TYPES:
        logger.warning(f"INVALID_POST_TYPE idx={idx} value={post.get('post_type')!r}")


# ── Payload builder ───────────────────────────────────────────────────────────

def build_user_message(batch: list[dict]) -> str:
    lines = []
    for post in batch:
        idx      = post["_idx"]
        platform = post.get("platform", "")
        room     = post.get("audience_room_name", "")
        text     = (post.get("post_text") or "").strip()[:800]
        lines.append(f"[{idx}] platform={platform} | room={room}\n{text}")
    return "\n\n".join(lines)


# ── API call ──────────────────────────────────────────────────────────────────

def call_openai(
    batch: list[dict],
    client: OpenAI,
    cfg: dict,
    logger: logging.Logger,
    test_mode: bool,
) -> list[dict]:
    t0 = time.time()

    stream = client.chat.completions.create(
        model=cfg["model"],
        temperature=cfg["temperature"],
        stream=True,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": build_user_message(batch)},
        ],
        max_tokens=cfg["max_tokens"],
    )

    raw_chunks = []
    for chunk in stream:
        delta = chunk.choices[0].delta.content
        if delta:
            raw_chunks.append(delta)
            if test_mode:
                tqdm.write(delta, end="")

    if test_mode:
        tqdm.write("")

    elapsed = time.time() - t0
    raw = "".join(raw_chunks).strip()

    logger.debug(
        f"API_RESPONSE batch_start={batch[0]['_idx']} batch_end={batch[-1]['_idx']} "
        f"chars={len(raw)} elapsed={elapsed:.2f}s"
    )

    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    parsed = json.loads(raw)

    if isinstance(parsed, dict):
        for v in parsed.values():
            if isinstance(v, list):
                parsed = v
                break

    if not isinstance(parsed, list):
        logger.error(
            f"BAD_RESPONSE batch_start={batch[0]['_idx']} "
            f"expected=list got={type(parsed).__name__} raw={raw[:200]}"
        )
        return []

    clean = [item for item in parsed if isinstance(item, dict)]
    if len(clean) < len(parsed):
        logger.warning(
            f"DROPPED_ITEMS batch_start={batch[0]['_idx']} "
            f"count={len(parsed) - len(clean)}"
        )

    return clean


def safe_call(
    batch: list[dict],
    client: OpenAI,
    cfg: dict,
    logger: logging.Logger,
    test_mode: bool,
) -> list[dict]:
    for attempt in range(3):
        try:
            result = call_openai(batch, client, cfg, logger, test_mode)
            if result:
                return result
            logger.warning(
                f"EMPTY_RESULT batch_start={batch[0]['_idx']} attempt={attempt+1}"
            )
            time.sleep(2 ** (attempt + 1))
        except json.JSONDecodeError as e:
            logger.warning(
                f"JSON_ERROR batch_start={batch[0]['_idx']} attempt={attempt+1} error={e}"
            )
            time.sleep(2 ** (attempt + 1))
        except Exception as e:
            logger.warning(
                f"API_ERROR batch_start={batch[0]['_idx']} attempt={attempt+1} "
                f"type={type(e).__name__} error={e}"
            )
            time.sleep(2 ** (attempt + 1))

    logger.error(
        f"BATCH_FAILED batch_start={batch[0]['_idx']} all_attempts_exhausted=True"
    )
    return []


# ── Main ──────────────────────────────────────────────────────────────────────

def run(cfg_path: str = "config.yaml"):
    cfg  = load_config(cfg_path)
    HERE = os.path.dirname(os.path.abspath(__file__))

    INPUT_PATH        = resolve(HERE, cfg["input_path"])
    OUTPUT_TAGS       = resolve(HERE, cfg["output_tags"])
    CHECKPOINT_PATH   = resolve(HERE, cfg["checkpoint_path"])
    LOG_PATH          = resolve(HERE, cfg["log_path"])
    TEST_RESULTS_PATH = resolve(HERE, cfg.get("test_results", "test_results.txt"))

    TEST_MODE    = cfg["test_mode"]
    TEST_BATCHES = cfg["test_batches"]
    BATCH_SIZE   = cfg["batch_size"]
    SHUFFLE_SEED = cfg["shuffle_seed"]
    TRANSFER_TYPES = set(cfg["transfer_types"])

    logger = setup_logger(LOG_PATH)
    run_start = time.time()

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        print("ERROR: OPENAI_API_KEY not set.")
        print("  Windows:   set OPENAI_API_KEY=sk-proj-...")
        print("  Mac/Linux: export OPENAI_API_KEY=sk-proj-...")
        raise SystemExit(1)

    client = OpenAI(api_key=api_key)

    # Load posts
    with open(INPUT_PATH, newline="", encoding="utf-8", errors="replace") as f:
        reader     = csv.DictReader(f)
        fieldnames = reader.fieldnames
        all_posts  = list(reader)

    print(f"Loaded {len(all_posts):,} posts from {cfg['input_path']}")

    # Assign stable indices before shuffle
    for i, p in enumerate(all_posts):
        p["_idx"] = int(i)

    # Shuffle for cross-audience, cross-platform batch mixing
    random.seed(SHUFFLE_SEED)
    random.shuffle(all_posts)

    # Build batches
    batches = [
        all_posts[i:i + BATCH_SIZE]
        for i in range(0, len(all_posts), BATCH_SIZE)
    ]

    # Resume from checkpoint
    checkpoint = load_checkpoint(CHECKPOINT_PATH)
    if checkpoint:
        tqdm.write(f"  Resuming: {len(checkpoint):,} posts already tagged")
        logger.info(f"RESUME checkpoint_posts={len(checkpoint)}")

    tagged = list(checkpoint.values())

    def batch_done(batch):
        return all(p["_idx"] in checkpoint for p in batch)

    if TEST_MODE:
        batches_to_run = batches[:TEST_BATCHES]
        # in test mode don't skip — always run fresh
        tagged = []
        tqdm.write(f"TEST MODE — running {TEST_BATCHES} batch(es) of {BATCH_SIZE} posts\n")
        # open test results file — mirrors all batch output for post-run review
        test_file = open(TEST_RESULTS_PATH, "w", encoding="utf-8")
        test_file.write(f"TEST RUN — {TEST_BATCHES} batch(es) of {BATCH_SIZE} posts\n")
        test_file.write(f"Config: {cfg_path}  |  Model: {cfg['model']}  |  Seed: {SHUFFLE_SEED}\n")
        test_file.write(f"{'='*65}\n\n")
    else:
        test_file = None
        batches_to_run = [b for b in batches if not batch_done(b)]
        tqdm.write(
            f"FULL RUN — {len(batches_to_run):,} batches remaining "
            f"of {len(batches):,} total"
        )

    logger.info(
        f"RUN_START posts={len(all_posts)} batches_total={len(batches)} "
        f"batches_to_run={len(batches_to_run)} seed={SHUFFLE_SEED} "
        f"model={cfg['model']} test_mode={TEST_MODE}"
    )

    # In test mode, accumulate all output so it can be written to a file
    # — prevents 5-batch results being lost to terminal scroll buffer
    test_output_lines = []

    def tee(line: str = ""):
        """Print to terminal and accumulate for test output file."""
        print(line)
        test_output_lines.append(line)

    # Tag
    progress = tqdm(
        batches_to_run,
        desc="Tagging",
        unit="batch",
        ncols=90,
        bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]",
        disable=TEST_MODE,
    )

    for b_num, batch in enumerate(progress if not TEST_MODE else batches_to_run, 1):
        if not TEST_MODE:
            progress.set_postfix({
                "tagged": len(tagged),
                "batch":  f"{b_num}/{len(batches_to_run)}"
            })
        else:
            print(f"Batch {b_num}/{len(batches_to_run)}  ({len(batch)} posts)...",
                  end=" ", flush=True)

        logger.debug(f"BATCH_START batch={b_num} start_idx={batch[0]['_idx']}")

        classifications = safe_call(batch, client, cfg, logger, TEST_MODE)
        cls_by_idx = {
            c.get("idx", c.get("index", i)): c
            for i, c in enumerate(classifications)
        }

        batch_tagged = []
        for post in batch:
            cls = cls_by_idx.get(post["_idx"], {})
            post["topic_broad"]       = cls.get("topic_broad", "")
            post["topic"]             = cls.get("topic", "UNCLASSIFIED")
            post["topic_narrow"]      = cls.get("topic_narrow", "")
            post["opinion_subtopics"] = json.dumps(cls.get("opinion_subtopics", []))
            post["sentiment"]         = cls.get("sentiment", "")
            post["stance"]            = cls.get("stance", "")
            post["post_type"]         = cls.get("post_type", "")
            validate_and_log(post, logger)
            batch_tagged.append(post)
            tagged.append(post)

        if not TEST_MODE:
            save_to_checkpoint(CHECKPOINT_PATH, batch_tagged)

        logger.debug(
            f"BATCH_DONE batch={b_num} "
            f"tagged={len(batch_tagged)} "
            f"unclassified={sum(1 for p in batch_tagged if p['topic']=='UNCLASSIFIED')}"
        )

        if TEST_MODE:
            print("ok")
            lines = []
            lines.append(f"\n{'─'*65}")
            lines.append(f"BATCH {b_num} RESULTS")
            lines.append(f"{'─'*65}")
            for post in batch:
                lines.append(
                    f"\n  [{post['_idx']}] {post.get('platform',''):10} | "
                    f"{post.get('audience_room_name','')[:35]}"
                )
                lines.append(f"  TEXT      : {(post.get('post_text') or '')[:120]}")
                lines.append(f"  BROAD     : {post.get('topic_broad','')}")
                lines.append(f"  TOPIC     : {post.get('topic','')}")
                lines.append(f"  NARROW    : {post.get('topic_narrow','')}")
                lines.append(f"  SUBTOPICS : {post.get('opinion_subtopics','')}")
                lines.append(
                    f"  SENTIMENT : {post.get('sentiment','')}  "
                    f"STANCE: {post.get('stance','')}"
                )
                lines.append(f"  TYPE      : {post.get('post_type','')}")
            output = "\n".join(lines)
            print(output)
            if test_file:
                test_file.write(output + "\n")
                test_file.flush()

        time.sleep(0.5)

    if TEST_MODE:
        footer = "\n".join([
            f"\n{'='*65}",
            "Test complete. Review labels above.",
            "\nChecklist before full run:",
            "  1. topic_broad always from the fixed list? No invented domains?",
            "  2. topic abstract enough to group across platforms?",
            "  3. topic_narrow naming specific events where they exist?",
            "  4. opinion_subtopics capturing the right angles?",
            "  5. sentiment + stance feel correct? No stance values in sentiment?",
            "  6. post_type filtering correctly?",
            "     (insider announcements -> promotional, not news_reaction)",
            f"{'='*65}",
        ])
        print(footer)
        if test_file:
            test_file.write(footer + "\n")
            test_file.close()
            print(f"\nFull test output written to: {TEST_RESULTS_PATH}")
        logger.info("TEST_RUN_COMPLETE")
        return

    # ── Full run: write tagged posts ──────────────────────────────────────────

    new_fields = [
        "topic_broad", "topic", "topic_narrow",
        "opinion_subtopics", "sentiment", "stance", "post_type",
    ]
    # Exclude any original CSV columns that share a name with new LLM fields
    # (posts_clean_v2.csv has a 'post_type' column from Sapiens — we replace it)
    out_fields = [f for f in (fieldnames or [])
                  if not f.startswith("_") and f not in new_fields] + new_fields

    tqdm.write(f"\nWriting {OUTPUT_TAGS}...")
    with open(OUTPUT_TAGS, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=out_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(tagged)
    tqdm.write(f"  {len(tagged):,} rows written")

    # Post type breakdown
    type_counts: dict[str, int] = defaultdict(int)
    for post in tagged:
        type_counts[post.get("post_type", "unknown")] += 1

    elapsed = time.time() - run_start

    tqdm.write(f"\n{'='*65}")
    tqdm.write("TAGGING COMPLETE")
    tqdm.write(f"{'='*65}")
    tqdm.write(f"\n  Posts tagged    : {len(tagged):,}")
    tqdm.write(f"  Unclassified    : {sum(1 for p in tagged if p['topic'] == 'UNCLASSIFIED'):,}")
    tqdm.write(f"  Elapsed         : {elapsed:.0f}s")

    tqdm.write(f"\n  Post type breakdown:")
    transfer_types = set(cfg["transfer_types"])
    for ptype, cnt in sorted(type_counts.items(), key=lambda x: -x[1]):
        pct    = cnt / max(len(tagged), 1) * 100
        marker = " <- transfer" if ptype in transfer_types else ""
        tqdm.write(f"    {ptype:<22} {cnt:>7,}  ({pct:>5.1f}%){marker}")

    tqdm.write(f"\n{'='*65}")
    tqdm.write("Output files:")
    tqdm.write(f"  {OUTPUT_TAGS}")
    tqdm.write(f"  {CHECKPOINT_PATH}")
    tqdm.write(f"  {LOG_PATH}")
    tqdm.write(f"\nNext step:")
    tqdm.write(f"  python build_pairs.py --config config_pairs.yaml")
    tqdm.write(f"{'='*65}")

    logger.info(
        f"RUN_END tagged={len(tagged)} "
        f"unclassified={sum(1 for p in tagged if p['topic']=='UNCLASSIFIED')} "
        f"elapsed={elapsed:.0f}s"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config", default="config.yaml",
        help="Path to config YAML (default: config.yaml)"
    )
    args = parser.parse_args()
    run(args.config)