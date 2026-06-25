# Dataset Schema: final_dataset_v1_bilateral_only.csv

**BAIR × Vectorial AI - Cross-Platform Behavioral Transfer Dataset v1**

This file contains all posts where both LinkedIn and Reddit are represented
within the same audience room and canonical topic. Every row in this file
is part of a genuine bilateral cell. It is the primary input for transfer
function training and architecture exploration.

---

## Overview

| Property | Value |
|---|---|
| Total posts | 2,038 |
| Bilateral cells (audience × topic) | 197 |
| Cells with 10+ posts per side | 4 |
| Audience rooms | 2 (Backend Engineer, Instructional Designer) |
| Domains | 9 |
| Platforms | LinkedIn, Reddit |

---

## How to Use This File

Each row is one post. To reconstruct a bilateral cell for transfer function
training, filter by `audience_room_name` and `final_topic`, then split by
`platform`. The resulting LinkedIn and Reddit post sets are the two
distributions for Wasserstein Distance computation.

Use `audience_topic_platform_summary.csv` to see the full list of 197 cells
with per-platform counts before filtering into this file.

---

## Fields

| Field | Type | Never empty | Description |
|---|---|---|---|
| `post_id` | string | yes | Unique post identifier. LinkedIn: `urn:li:activity:...` format. Reddit: platform post ID. No duplicates in this file. |
| `audience_room_name` | string | yes | The professional community this post was collected under. Primary grouping key alongside `final_topic`. Example: `Backend Engineer (linkedin+reddit)`. |
| `platform` | string | yes | Source platform. Values: `linkedin` or `reddit`. |
| `domain` | string | yes | Broad subject domain. Values: `Software Engineering`, `AI`, `Security`, `EdTech`, `Career`, `Business & Strategy`, `Data & Analytics`, `Leadership & Management`, `Open Source`. |
| `post_text` | string | yes | Full post text, untruncated. Primary input for the transfer function. |
| `post_date` | string | no | Publication date, ISO 8601 where available. |
| `post_url` | string | no | Original URL of the post. |
| `post_type` | string | yes | Format and intent of the post. Values: `opinion`, `news_reaction`, `show_and_tell`, `question`, `promotional`, `personal_milestone`. |
| `sentiment` | string | yes | Sentiment toward the post subject. Values: `positive`, `negative`, `neutral`, `mixed`. Assigned by GPT-4.1-mini. |
| `stance` | string | yes | Author's evaluative stance. Values: `assertive`, `informational`, `supportive`, `critical`, `questioning`, `reflective`. |
| `original_topic` | string | no | Topic label from the prior LLM vocabulary pass, retained for provenance. Not used for grouping. |
| `topic_narrow` | string | no | More specific sub-topic from the original tagging pass, retained for provenance. |
| `final_topic` | string | yes | **Primary grouping key.** Canonical topic assigned by BERTopic semantic clustering (all-MiniLM-L6-v2 → UMAP → HDBSCAN) with GPT-4.1-mini naming, followed by validated manual consolidation of over-split clusters. One topic per post. |
| `source` | string | yes | Pipeline that produced the `final_topic`. Values: `new_pipeline` (BERTopic-assigned, majority of posts), `old_vocabulary_unchanged` (domains not yet re-clustered through BERTopic: Business & Strategy, Data & Analytics, Leadership & Management). |
| `bilateral_flag` | boolean | yes | `True` for all rows in this file by construction - every post here belongs to a cell where both LinkedIn and Reddit are present. |

---

## Transfer-Ready Cells (10+ posts per side)

| Topic | Audience | LinkedIn | Reddit | Total | Note |
|---|---|---|---|---|---|
| ruby on rails ecosystem | Backend Engineer | 21 | 96 | 117 | Primary training cell. Strong platform behavioral contrast confirmed. |
| elixir programming language | Backend Engineer | 17 | 20 | 37 | Reddit side is link-post titles, not authored discourse. Use for architecture exploration only until Reddit content improves. |
| open source software | Backend Engineer | 16 | 14 | 30 | LinkedIn: reflective and critical. Reddit: builders sharing projects. Confirmed behavioral inversion from the typical pattern. |
| python ecosystem | Backend Engineer | 10 | 15 | 25 | Real bilateral signal. Reddit has concentration on Pyrefly type-checker coverage. |

**Training-ready set excluding Elixir qualification: 3 cells, 172 posts.**

---

## What Is Not in This File

**Single-topic only.** Each post has exactly one `final_topic`. Multi-label assignment is not implemented.

**No aspect layer.** `final_topic` is the stimulus topic - what the post is about. The aspect layer (how the author frames it: outcome, technical, community, critical, etc.) is the next development milestone and is not a column in this file.

**No precision evaluation.** Topic assignment precision has not been formally evaluated against a human-labeled ground truth. Formal evaluation is pending per the methodology protocol.
