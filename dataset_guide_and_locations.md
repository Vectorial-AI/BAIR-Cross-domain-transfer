# BAIR x Vectorial AI - Cross-Platform Behavioral Corpus Locations

Raw data files are on the shared drive. Links to each file are in the sections below.

---

## Repository structure

```
/
├── README.md
├── docs/
│   ├── database_schema.md
│   └── canonical_topics_and_cleanup_pass.md
└── data/
    ├── corpus/
    │   ├── tagged_posts_v2_clean.csv
    │   ├── tagged_posts_v2_normalised.csv
    │   └── paired_dataset_normalised.csv
    ├── vocabulary/
    │   ├── topic_cluster_map.json
    │   └── updated_canonical_vocabulary
    ├── cleanup/
    │   ├── proposed_merges.csv
    │   ├── proposed_merges_reviewed.csv
    │   └── review_summary.txt
    └── stimulus/
        ├── stimulus_report.txt
        ├── named_event_stimuli.csv
        └── abstracted_stimuli.csv
```

---

## Documentation

Both documents below are the authoritative references for the corpus and pipeline. Read these before working with any of the data files.

**`docs/database_schema.md`**
Complete field reference for `tagged_posts_v2_normalised.csv`. Covers all 30 fields across four groups: scraper-origin fields, quality control labels, LLM extraction fields, and pending fields. Includes corpus counts, audience room breakdown, topic normalisation key metrics, and the full key files reference.

**`docs/canonical_topics_and_cleanup_pass.md`**
Full account of the topic vocabulary construction pipeline. Covers the identify-and-invent normalisation pass, the cleanup pass architecture and results, known-good and known-bad merge examples, current blocking items, and evaluation design. This is the reference document for understanding the state of `topic_cluster_map.json` and `proposed_merges.csv`.

---

## Data files

All data files live on the shared drive. Download them into the corresponding `data/` subdirectory to match the structure above.

### `data/corpus/`

**`tagged_posts_v2_clean.csv`** - [drive link]
Raw extraction output. 22,954 posts with all fields populated. The `topic` field in this file contains the original free-form LLM label before normalisation. This is the pre-normalisation source of truth.

**`tagged_posts_v2_normalised.csv`** - [drive link]
Primary analysis file. Identical to the clean CSV except the `topic` field has been replaced with the canonical label from `topic_cluster_map.json`. The original free-form label is preserved as `topic_raw`. All downstream analysis runs from this file.

**`paired_dataset_normalised.csv`** - [drive link]
Bilateral cell structure. One row per (audience, topic) cell, with LinkedIn and Reddit post counts. Derived from `tagged_posts_v2_normalised.csv` by `build_pairs.py`. 8,421 total cells, 324 bilateral. Currently zero cells meet the 30-posts-per-side threshold for Wasserstein Distance computation - targeted scraping is underway to address this.

### `data/vocabulary/`

**`topic_cluster_map.json`** - [drive link]
The full raw-label to canonical-label mapping. Structure: `{domain: {raw_label: canonical_label}}`. 17,081 entries across nine active domains. This is the artifact that `apply_topic_map.py` reads to produce `tagged_posts_v2_normalised.csv`. Current state: pre-cleanup-commit. The 545 proposed merges in `data/cleanup/proposed_merges.csv` have not yet been applied.

**`updated_canonical_vocabulary`** - [drive link]
Human-readable distillation of the canonical vocabulary. One entry per canonical topic per domain. Use this for browsing the vocabulary or checking label coverage. `topic_cluster_map.json` is the machine-readable ground truth.

### `data/cleanup/`

These three files represent the full state of the cleanup pass. See `docs/canonical_topics_and_cleanup_pass.md` for complete context before working with them.

**`proposed_merges.csv`** - [drive link]
545 proposed synonym and group merges across all nine domains, produced by `cleanup_pass.py`. Columns: domain, raw label, proposed canonical target, Jaccard score, stage (synonym or group), LLM verdict (APPROVED or REJECTED), LLM reasoning. None of these merges have been applied to `topic_cluster_map.json`. Do not treat approved rows as committed.

**`proposed_merges_reviewed.csv`** - [drive link]
Same 545 rows with an additional independent review column. 389 rows were reviewed (the Software Engineering domain in full). Columns added: manual verdict, manual reasoning. 95 disagreements with the LLM audit are present in this file - 59 where the independent review rejected an LLM-approved merge, and 36 where the independent review approved an LLM-rejected merge. See `review_summary.txt` for the full disagreement list.

**`review_summary.txt`** - [drive link]
All 95 disagreements between the LLM audit and the independent review, with both sets of reasoning presented side by side. Reference this before making any adjudication decision on the 59 disputed anchor-topic cases.

### `data/stimulus/`

These three files are outputs of `stimulus_report.py`, which characterises the existing stimulus distribution in the corpus using the `topic_narrow` and `topic` fields. They are read-only reference artifacts - they describe what stimuli already exist in the data and have not been used to drive any pipeline decisions yet.

**`stimulus_report.txt`** - [drive link]
Full characterisation of the stimulus distribution. Key figures: 11,148 unique named events across the corpus (97.4% singletons), 291 recurring named events, 2,647 non-singleton topic-domain pairs at the abstracted layer, 1,082 ambiguous-zone pairs with three or more distinct named events underneath them.

**`named_event_stimuli.csv`** - [drive link]
One row per unique named event drawn from `topic_narrow`. Columns include domain, the named event string, and frequency. The 291 recurring named events are the candidates for temporal cell analysis.

**`abstracted_stimuli.csv`** - [drive link]
One row per topic-domain pair - the abstracted stimulus layer. Includes the domain column. Use this to browse what recurring subjects the corpus covers at the topic level, as distinct from the named-event level in `named_event_stimuli.csv`.

---

## Current pipeline state

The pipeline has three active threads. This section summarises their state as of the repository creation date.

**Topic vocabulary (canonical + cleanup).** The canonical vocabulary of 5,408 topics is complete and in production. The cleanup pass has been run across all nine domains, producing 545 proposed merges. Four blocking items remain before the merges can be committed: validation of the Data and Analytics and Open Source domains, row-by-row adjudication of 59 disputed Software Engineering merges, one unresolved merge pair, and the build of the apply script. See `docs/canonical_topics_and_cleanup_pass.md` Section 4.

**Stimulus and aspect extraction.** `stimulus_aspect_extraction_v1.py` is built and in active prompt development. It adds two new fields to the corpus: `stimulus` (event or concern that triggered the post, derived from post text) and `aspect_subtopics` (evaluative lenses the author applied, cleanly separated from the stimulus). Test runs have been completed. Full run pending prompt finalisation and a downstream normalisation pass design decision.

**Bilateral cell coverage.** Zero cells currently meet the 30-posts-per-side threshold for Wasserstein Distance computation. Targeted scraping is underway. This is the gate for conditional WD analysis.

---

## Open questions for the group

Two methodological questions are currently blocking pipeline decisions. Both require input from the BAIR team before implementation can proceed.

**Precision evaluation of the canonical topics.** Coverage has been measured at 86.9%. Precision - whether posts grouped under the same canonical topic are genuinely about the same subject - has not been measured. A protocol is needed: sample size, sampling strategy, human judgment task design, inter-annotator agreement handling. Reference: `docs/canonical_topics_and_cleanup_pass.md` Section 5; meeting notes Section 6.1.

**Multi-topic classification.** The current schema assigns one topic per post. Multi-topic assignment has been agreed as valuable but not implemented. Two design questions must be resolved before implementation: how WD handles posts contributing to multiple cells, and whether multi-topic assignment happens in a single labelling pass or a second pass over already-labelled posts. Reference: meeting notes Section 6.2.

