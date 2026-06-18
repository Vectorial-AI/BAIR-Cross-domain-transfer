# Database Schema - BAIR x Vectorial AI Cross-Platform Corpus
**Last updated:** June 19, 2026  
**Status:** Production corpus. Schema is stable. Two new fields (`stimulus`, `aspect_subtopics`) pending pipeline completion.

---

## Overview

The corpus is a flat CSV file: `tagged_posts_v2_normalised.csv`. Every row is one post. There are no foreign key relationships, no separate tables, no normalised relational structure. Each row is fully self-contained.

The file is the output of three sequential pipeline stages:

1. **Scraping** - raw posts collected from LinkedIn and Reddit, platform metadata attached
2. **LLM extraction** - `topic_extraction_v2.py` adds topic labels, sentiment, stance, post type, and opinion subtopics in a single pass
3. **Topic normalisation** - `apply_topic_map.py` replaces the free-form `topic` field with a canonical label from `topic_cluster_map.json`, preserving the original as `topic_raw`

A fourth downstream file, `paired_dataset_normalised.csv`, is produced by `build_pairs.py` and groups posts into bilateral cells (audience × topic) for the Wasserstein Distance computation. It derives entirely from the main CSV and is not a primary source.

---

## Full Field Reference

### Group 1 - Scraper-origin fields

Present from data collection. Never modified by any downstream pipeline step.

| Field | Type | Platform | Notes |
|---|---|---|---|
| `post_id` | string | both | Platform-native post identifier. Stable and unique per post. |
| `profile_id` | string | both | Author identifier from the platform. |
| `audience_room_id` | string | both | Internal identifier for the audience room this post was scraped from. |
| `audience_room_name` | string | both | Human-readable room label. See Audience Rooms section. |
| `platform` | string | both | Exactly "linkedin" or "reddit". |
| `post_text` | string | both | Full post body, untruncated. |
| `post_date` | string | both | Date the post was published on the platform. |
| `post_url` | string | both | Direct URL to the original post. |
| `num_likes` | integer | both | Like/upvote count at time of scrape. |
| `num_comments` | integer | both | Comment count at time of scrape. |
| `num_shares` | integer | LinkedIn | Share/repost count. Null for Reddit. |
| `subreddit` | string | Reddit | Subreddit name (without r/ prefix). Null for LinkedIn. |
| `author_name` | string | both | Display name of the post author. |
| `author_headline` | string | LinkedIn | LinkedIn headline. Null or flair equivalent for Reddit. |
| `author_profile_url` | string | both | URL to the author's profile. |
| `share_audience` | string | LinkedIn | Audience visibility setting ("anyone", "connections", etc.). Null for Reddit. |

---

### Group 2 - Quality control labels

Binary flags applied during post curation. Used to filter the corpus before analysis. Not applied by the LLM - these are human or rule-based annotations from the Vectorial AI data pipeline.

| Field | Meaning | Effect when true |
|---|---|---|
| `label_useful` | Post is substantive and on-domain | Inclusion signal |
| `label_personal` | Post is personal/off-topic (life events, non-professional) | Exclusion candidate |
| `label_off_domain` | Post falls outside the 11 topic domains | Exclusion candidate |
| `label_low_content` | Post has insufficient text for analysis (link-only, one-liner) | Exclusion candidate |
| `label_generic_advice` | Post is generic professional advice with no specific stimulus | Exclusion candidate |
| `label_repost` | Post is a repost or share of someone else's content | Exclusion candidate |
| `label_promo` | Post is promotional by content, regardless of `post_type` label | Exclusion candidate |

---

### Group 3 - LLM extraction fields

Added by `topic_extraction_v2.py`. Every post in the corpus has values for all of these fields. The LLM (gpt-4.1-mini) processes posts in randomised batches of 20, mixing platforms and audiences deliberately to prevent the model from inferring cross-platform alignment from context.

#### `topic_broad`
Fixed 11-item vocabulary. Domain classification of the post.

Valid values: `AI`, `Software Engineering`, `Career`, `EdTech`, `Healthcare`, `Leadership & Management`, `Security`, `Data & Analytics`, `Open Source`, `Business & Strategy`, `Other`

Nine of these eleven are active in the normalised analysis pipeline. `Healthcare` and `Other` are excluded from all WD computation and bilateral cell analysis. The `Other` category is overrepresented on Reddit (11.3% of Reddit posts vs 1.3% of LinkedIn posts), suggesting Reddit-native domains not covered by the fixed vocabulary - a known open issue.

#### `topic_raw`
Free-form, 2-5 words, lowercase. The original LLM-generated label before normalisation. Preserved as a record of what the model produced before vocabulary consolidation. Not used in downstream analysis - `topic` is used instead.

#### `topic`
The normalised canonical label. Before `apply_topic_map.py` runs, this field contains the same free-form label as `topic_raw`. After normalisation, it is replaced with the canonical value from `topic_cluster_map.json`. This is the primary grouping field for all analysis.

The normalisation pass collapsed 17,081 unique free-form labels to 5,408 canonical topics - a 68% reduction. Per-domain breakdown:

| Domain | Canonical topics |
|---|---|
| Software Engineering | 2,273 |
| Business & Strategy | 751 |
| Security | 574 |
| EdTech | 410 |
| Career | 440 |
| Data & Analytics | 263 |
| Open Source | 346 |
| Leadership & Management | 125 |
| AI | 226 |
| **Total (nine active domains)** | **5,408** |

#### `topic_narrow`
Free-form, lowercase. The most specific level of topic labelling - a named event, specific debate, or particular release if one exists. If the post is a general opinion with no datable event, this field repeats the `topic` value.

Examples:
- Named event: `anthropic claude code pro plan removal`
- Named event: `npm supply chain attack may 2024`
- General opinion: `llm fine-tuning` (same as `topic`)

Present as a distinct named event in approximately 58% of transfer-eligible posts. The field is never modified by `apply_topic_map.py` - it retains the original free-form extraction value throughout the pipeline.

**Note on lineage:** `topic_narrow` was produced in the same free-form extraction pass as `topic_raw`. It has not been normalised and carries the same vocabulary fragmentation risks. 11,148 unique named events exist across the corpus, of which 97.4% (10,857) appear only once. Only 291 recurring named events exist - these are the candidates for temporal cell analysis.

#### `opinion_subtopics`
JSON list. 2-4 free-form strings, each 2-4 words, lowercase. The evaluative or argumentative angles the author took within the topic. Intended to capture which sub-dimensions of a subject the author chose to foreground.

Example for a post about `llm fine-tuning`: `["training cost", "evaluation quality", "lora vs full fine-tune"]`

**Known limitation:** This field currently mixes two conceptually distinct things - re-descriptions of the stimulus itself, and genuine evaluative lenses applied to the stimulus. The `aspect_subtopics` field (see Pending Fields) is designed to replace it with a clean extraction of evaluative lenses only.

#### `sentiment`
Fixed vocabulary. The emotional valence of the post toward its subject.

| Value | Meaning |
|---|---|
| `positive` | Favourable, optimistic, approving |
| `negative` | Frustrated, disappointed, alarmed |
| `neutral` | Factual, balanced, descriptive - no clear emotional charge |
| `mixed` | Author sees both upsides and downsides |

Corpus distribution (transfer-eligible posts only):

| Sentiment | LinkedIn | Reddit |
|---|---|---|
| positive | 59.2% | 26.2% |
| neutral | 23.5% | 49.3% |
| negative | 11.1% | 16.6% |
| mixed | 6.2% | 8.0% |

The platform-level divergence here is one of the primary validated findings: LinkedIn skews heavily positive; Reddit defaults to neutral.

#### `stance`
Fixed vocabulary. The rhetorical posture of the author.

| Value | Meaning |
|---|---|
| `assertive` | Stating a strong position |
| `critical` | Pushing back on something or someone |
| `supportive` | Endorsing or defending |
| `questioning` | Asking or expressing uncertainty |
| `informational` | Sharing facts or news with minimal opinion |

#### `post_type`
Fixed vocabulary. The genre of the post. Determines transfer eligibility.

| Value | Transfer eligible | Notes |
|---|---|---|
| `opinion` | Yes | Genuine take or reaction. Primary source for bilateral comparison. |
| `news_reaction` | Yes | Reaction to a named external event. Cross-platform by nature. Posts where the author works at the company being announced are classified as `promotional` instead. |
| `question` | No | Primarily seeking input. Structurally rare on LinkedIn (181 posts), very common on Reddit (2,956 posts). |
| `personal_milestone` | No | Career events - promotions, new jobs, anniversaries. |
| `promotional` | No | Webinars, product launches, event announcements, job postings. LinkedIn-native pattern. |
| `show_and_tell` | No | "I built X" posts sharing a personal project. No systematic LinkedIn equivalent. |

Transfer-eligible posts (opinion + news_reaction): **14,243 total** - 9,597 LinkedIn, 4,646 Reddit.

**Known issue:** Post-type labelling accuracy is imperfect. A sample of posts retrieved for prompt development showed several posts labeled `opinion` or `news_reaction` whose content was clearly promotional or show-and-tell. The `label_promo` quality control flag captures some of this but not all. The mislabelling rate has not been formally measured.

---

### Group 4 - Pending fields (not yet in the corpus)

These fields are the output of `stimulus_aspect_extraction_v1.py`, which is currently in active prompt development and has not completed a full run.

#### `stimulus` (pending)
String, 2-6 words, lowercase. The discrete event, named release, ongoing debate, or recurring professional concern that triggered the post. Derived fresh from post text - not copied from `topic_narrow`. Will be normalised in a downstream pass using the same identify-and-invent tooling built for `topic`.

Design constraint: a stimulus must be a thing that exists in the world independently. If a phrase describes a quality, consequence, or dimension of something rather than the thing itself, it is an aspect, not a stimulus. The upper bound: conditioning on the stimulus must leave room for the author to have chosen any aspect angle in response.

#### `aspect_subtopics` (pending)
JSON list, 2-4 items, each 2-4 words, lowercase. The evaluative and argumentative lenses the author applied to the stimulus. Strictly separated from the stimulus itself - no re-description of what triggered the post, only how the author chose to engage with it.

This replaces `opinion_subtopics` as the primary signal field for aspect-level distributional comparison between LinkedIn and Reddit.

---

## Corpus Counts

### Total

| | LinkedIn | Reddit | Total |
|---|---|---|---|
| All posts | 14,316 | 8,638 | 22,954 |
| Nine active domains | ~11,891 | ~8,638 | 20,529 |
| Healthcare / Other / blank (excluded) | 2,425 | - | 2,425 |

### By post type and platform

| Post type | LinkedIn | Reddit | Total |
|---|---|---|---|
| opinion | 5,926 | 3,253 | 9,179 |
| news_reaction | 3,671 | 1,393 | 5,064 |
| question | 181 | 2,956 | 3,137 |
| promotional | 2,220 | 113 | 2,333 |
| show_and_tell | 815 | 856 | 1,671 |
| personal_milestone | 742 | 26 | 768 |
| (blank) | 761 | 41 | 802 |

---

## Audience Rooms

24 rooms total. Rooms labeled "(linkedin+reddit)" are purpose-built bilateral rooms - both platforms were scraped for the same occupational audience. All other rooms are single-platform.

| Room | LinkedIn | Reddit | Total | Bilateral |
|---|---|---|---|---|
| Backend Engineer (linkedin+reddit) | 3,126 | 1,670 | 4,796 | Yes |
| Director/VP Engineering/CTO in Edtech Co | 2,157 | 0 | 2,157 | No |
| Staff Engineer | 1,831 | 0 | 1,831 | No |
| Engineering Manager | 1,467 | 0 | 1,467 | No |
| First Responders | 0 | 1,368 | 1,368 | No |
| Fullstack Engineers | 1,213 | 0 | 1,213 | No |
| Instructional Designer | 239 | 901 | 1,140 | Yes |
| Engineering Org in Edtech Co | 1,005 | 0 | 1,005 | No |
| Knowledge Graph Devs | 0 | 973 | 973 | No |
| Full Stack Developer Reddit | 0 | 964 | 964 | No |
| Edtech Developers | 0 | 901 | 901 | No |
| CTOs | 882 | 0 | 882 | No |
| VP Engineering | 708 | 0 | 708 | No |
| Product Manager Reddit | 0 | 630 | 630 | No |
| [LinkedIn] OpenAI Community | 556 | 0 | 556 | No |
| Frontend Engineer Reddit | 0 | 551 | 551 | No |
| Product Leadership Audience Room | 539 | 0 | 539 | No |
| CTO Reddit | 0 | 402 | 402 | No |
| Engineering Leadership Audience Room | 375 | 0 | 375 | No |
| Devops/Infra Engineer | 218 | 0 | 218 | No |
| QA Engineer Reddit | 0 | 123 | 123 | No |
| Programming Reddit | 0 | 79 | 79 | No |
| Engineering Manager Reddit | 0 | 70 | 70 | No |
| [Reddit] OpenAI Community | 0 | 6 | 6 | No |

The `build_pairs.py` output reports 20 unique audiences rather than 24. The discrepancy is because several rooms (First Responders, Knowledge Graph Devs, Full Stack Developer Reddit, Edtech Developers, Product Manager Reddit, Frontend Engineer Reddit, CTO Reddit, QA Engineer Reddit, Programming Reddit, Engineering Manager Reddit, [Reddit] OpenAI Community) fall partly or entirely into the excluded Healthcare/Other domain and are filtered out of the nine-domain analysis scope.

---

## Topic Normalisation - Key Metrics

The `topic` field goes through two transformations before it reaches its final state in `tagged_posts_v2_normalised.csv`.

**Raw extraction (topic_extraction_v2.py):**
- 22,954 posts tagged
- 17,081 unique raw topic labels produced
- Average: 1.3 posts per label
- Singleton rate (labels appearing exactly once): 70.0% of posts

**After identify-and-invent normalisation (apply_topic_map.py):**
- 17,081 raw labels → 5,408 canonical labels (−68%)
- Singleton rate: 13.1% of posts (down from 70.0%)
- Coverage: 86.9% of posts carry a label appearing ≥ 2 times (up from 30.0%)
- Topics with ≥ 10 posts: 443 (up from 50)
- Topics with ≥ 30 posts: 88 (up from 4)
- Self-maps (raw label == canonical label, i.e. too specific to cluster): 5,137 of 17,081 (30.1%)

**Bilateral cell structure (build_pairs.py, current state):**
- 8,421 total cells (audience × topic combinations with ≥ 1 post)
- 324 bilateral cells (both LinkedIn and Reddit posts present)
- 5,307 LinkedIn-only cells
- 2,790 Reddit-only cells
- Cells meeting WD threshold (≥ 30 posts per side): **0**
- Cells meeting Taran's threshold (≥ 10 posts per side): **0**
- Gate to WD computation: targeted scraping currently underway

---

## Key Files

| File | Description | Status |
|---|---|---|
| `tagged_posts_v2_clean.csv` | Raw extraction output - free-form topic labels, all fields | Stable |
| `tagged_posts_v2_normalised.csv` | Primary analysis file - `topic` field replaced with canonical labels, `topic_raw` added | Stable |
| `topic_cluster_map.json` | Raw-label → canonical-label mapping, structured by domain | Stable (pre-cleanup-commit) |
| `paired_dataset_normalised.csv` | Bilateral cell structure derived from normalised CSV | Stable |
| `proposed_merges.csv` | Cleanup pass output - 545 proposed merges, not yet committed | Pending review |
| `named_event_stimuli.csv` | One row per unique named event from `topic_narrow`, with domain and frequency | Available |
| `abstracted_stimuli.csv` | One row per topic-domain pair - the abstracted stimulus layer | Available |
| `stimulus_report.txt` | Full characterisation of the stimulus distribution across the corpus | Available |