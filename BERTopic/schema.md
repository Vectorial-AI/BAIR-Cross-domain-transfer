# Dataset Schema

**BAIR × Vectorial AI - Cross-Platform Behavioral Transfer Dataset v1**

Two files are included. This document describes both.

---

## 1. final_dataset_v1_bilateral_only.csv

Every row is one social media post. Only posts belonging to a bilateral cell
are included - meaning every row's audience room and canonical topic has at
least one LinkedIn post and at least one Reddit post present. This is the
primary file for transfer function training and architecture exploration.

### Example record

```
post_id:            t3_1q7cull
audience_room_name: Backend Engineer (linkedin+reddit)
platform:           reddit
domain:             Software Engineering
post_text:          If you have ever needed a confirmation that struct pointers
                    are just fine... [full text]
post_date:          2026-01-08T14:14:01+00:00
post_url:           https://www.reddit.com/r/golang/comments/1q7cull/...
post_type:          opinion
sentiment:          positive
stance:             assertive
original_topic:     go pointers usage
topic_narrow:       struct pointer approach
final_topic:        golang programming language
source:             new_pipeline
bilateral_flag:     TRUE
```

### Field reference

| Field | Type | Always present | Description |
|---|---|---|---|
| `post_id` | string | yes | Unique post identifier. Reddit posts use the platform's native ID format (`t3_...`). LinkedIn posts use URN format (`urn:li:activity:...`). No duplicates in this file. |
| `audience_room_name` | string | yes | The professional community this post was collected under. The primary grouping key alongside `final_topic`. All transfer-ready cells are in `Backend Engineer (linkedin+reddit)`. |
| `platform` | string | yes | Source platform. Values: `linkedin` or `reddit`. |
| `domain` | string | yes | Broad subject domain. Values: `Software Engineering`, `AI`, `Security`, `EdTech`, `Career`, `Business & Strategy`, `Data & Analytics`, `Leadership & Management`, `Open Source`. |
| `post_text` | string | yes | Full text of the post, untruncated. Primary input for the transfer function. No row in this file has an empty `post_text`. |
| `post_date` | string | no | Publication timestamp in ISO 8601 format with timezone offset where available (e.g. `2026-01-08T14:14:01+00:00`). |
| `post_url` | string | no | Original URL of the post. |
| `post_type` | string | yes | Format and intent of the post. Values: `opinion`, `news_reaction`, `show_and_tell`, `question`, `promotional`, `personal_milestone`. |
| `sentiment` | string | yes | Sentiment of the post toward its subject. Values: `positive`, `negative`, `neutral`, `mixed`. Assigned by GPT-4.1-mini during original corpus tagging. 100% populated. |
| `stance` | string | yes | Author's evaluative stance. Values: `assertive`, `informational`, `supportive`, `critical`, `questioning`, `reflective`. 100% populated. |
| `original_topic` | string | no | Topic label from the prior LLM identify-and-invent vocabulary pass, retained for provenance. Not used for grouping. Example: `go pointers usage`. |
| `topic_narrow` | string | no | A more specific sub-topic from the original tagging pass, retained for provenance. Example: `struct pointer approach`. |
| `final_topic` | string | yes | **Primary grouping key.** Canonical topic assigned by BERTopic semantic clustering (all-MiniLM-L6-v2 → UMAP → HDBSCAN) with GPT-4.1-mini naming each cluster, followed by validated manual consolidation of over-split clusters. One topic per post. Example: `golang programming language`. |
| `source` | string | yes | Pipeline that produced the `final_topic`. Values: `new_pipeline` (BERTopic-assigned), `old_vocabulary_unchanged` (domains not yet re-clustered: Business & Strategy, Data & Analytics, Leadership & Management). |
| `bilateral_flag` | string | yes | `TRUE` for all rows in this file by construction - every post here belongs to a cell where both LinkedIn and Reddit are present. |

### How to reconstruct a bilateral cell

Filter by `audience_room_name` and `final_topic`. Split the result by
`platform`. The LinkedIn and Reddit post sets are the two distributions
for Wasserstein Distance computation or transfer function training.

### Transfer-ready cells (10+ posts per side)

| Topic | Audience | LinkedIn | Reddit | Total | Note |
|---|---|---|---|---|---|
| ruby on rails ecosystem | Backend Engineer | 21 | 96 | 117 | Primary training cell. Strong platform behavioral contrast confirmed. |
| elixir programming language | Backend Engineer | 17 | 20 | 37 | Reddit side is link-post titles, not authored discourse. Architecture exploration only until Reddit content improves. |
| open source software | Backend Engineer | 16 | 14 | 30 | LinkedIn: reflective and critical. Reddit: builders sharing projects. Confirmed behavioral inversion from the typical pattern. |
| python ecosystem | Backend Engineer | 10 | 15 | 25 | Real bilateral signal. Reddit has concentration on Pyrefly type-checker coverage. |

**Confirmed training-ready (excluding Elixir): 3 cells, 172 posts.**

### What is not in this file

**Single-topic only.** Each post has one `final_topic`. Multi-label
assignment is not implemented.

**No aspect layer.** `final_topic` is the stimulus topic - what the post
is about. The aspect layer (how the author frames it) is the next
development milestone.

**No precision evaluation.** Topic assignment precision has not been
formally evaluated against a human-labeled ground truth. Pending per
the methodology protocol.

---

## 2. audience_topic_platform_summary.csv

A grouped summary of all bilateral cells. One row per (domain ×
audience_room_name × final_topic) combination. Use this to see the
full distribution of bilateral coverage before filtering into the main
dataset file.

### Example records

```
domain             audience_room_name                  final_topic                linkedin  reddit  total  clears_10_per_side
Software Eng.      Backend Engineer (linkedin+reddit)  ruby on rails ecosystem    21        96      117    yes
Open Source        Backend Engineer (linkedin+reddit)  postgresql database system 78        4       82     no
Career             Instructional Designer              instructional design        2         74      76     no
```

### Field reference

| Field | Type | Description |
|---|---|---|
| `domain` | string | Broad subject domain. Same values as the main dataset. |
| `audience_room_name` | string | The professional community. Same values as the main dataset. |
| `final_topic` | string | Canonical topic. Same values as the main dataset. One row per unique (domain × audience × topic) combination. |
| `linkedin_posts` | integer | Number of LinkedIn posts in this cell. |
| `reddit_posts` | integer | Number of Reddit posts in this cell. |
| `total_posts` | integer | LinkedIn + Reddit combined. |
| `clears_10_per_side` | string | `yes` if both `linkedin_posts` >= 10 and `reddit_posts` >= 10, otherwise `no`. Cells marked `yes` have sufficient volume for transfer function training. |

### How to read this file

Rows are sorted by `total_posts` descending. The `clears_10_per_side`
flag identifies the 4 cells with sufficient bilateral volume for
training. All other rows represent real bilateral overlap at lower
volume - these are the targets for the scraping sprint. Cells where
one platform count is disproportionately low (e.g. LI=78, RD=4) have
bilateral presence but are structurally imbalanced and not suitable
for training without targeted collection on the weaker side.
