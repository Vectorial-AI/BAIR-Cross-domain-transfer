# BAIR audience-posts pipeline

Reproducible, staged pipeline for filtering, cleaning, LLM topic tagging, audience×topic pairing, and topic normalisation on the BAIR audience-posts export.

Each stage lives in its own folder with a **stage README** (`stageN_.../README.md`). **Run every script from that stage’s directory.** Paths in configs are relative to the stage folder.

This repo ships **scripts, configs, and audit artifacts** from a completed run. Large CSV inputs/outputs are **not** included. Place them locally per the data handoff table below.

---

## Quick start

```powershell
cd REPO_cursor_version
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:OPENAI_API_KEY = "your-key-here"   # required for stages 1 and 3 only
```

Then follow [Pipeline run order](#pipeline-run-order). See [Data handoff between stages](#data-handoff-between-stages) for which files to copy where.

---

## Repository layout

| Folder | Purpose | Stage README |
|--------|---------|--------------|
| `stage0_preprocessing/` | Filter raw export → clean posts | [README](stage0_preprocessing/README.md) |
| `stage1_topic_extraction/` | LLM tagging, CSV repair, label cleanup, QC | [README](stage1_topic_extraction/README.md) |
| `stage2_pairing/` | Audience×topic matrix on **raw** topic labels | [README](stage2_pairing/README.md) |
| `stage3_topic_normalisation/` | Canonical vocabulary, cluster map, cleanup review, normalised pairing | [README](stage3_topic_normalisation/README.md) |

**Not included (by design):**

- `prod/three_stage_mapreduce/`: deprecated experiment tree
- `apply_cleanup_merges.py`: WIP; referenced by `cleanup_pass.py` but not implemented yet
- Stage 4 reporting scripts (charts, coverage tables): kept outside this repo
- Large data CSVs and stage-1 runtime checkpoint (see `.gitignore`)

---

## Pipeline run order

### Stage 0: Preprocessing

**Working directory:** `stage0_preprocessing/` · [stage README](stage0_preprocessing/README.md)

| Step | Command | Input → output |
|------|---------|----------------|
| 0a | `python filter_posts.py` | `posts.csv` → `posts_filtered.csv` |
| 0b | `python preprocess_posts_v2.py` | `posts_filtered.csv` → `posts_clean_v2.csv`, `posts_rejected_v2.csv` |
| 0c (QC) | `python database_integrity_check.py` | Reads `posts_clean_v2.csv`; stdout pre-flight audit |

`database_integrity_check.py` docstring still says `explore_csv.py`; the file on disk is `database_integrity_check.py`.

**Stage 0 filters (summary):** 28 in-scope audience rooms; non-empty text; LinkedIn `label_useful >= 0.5` (Reddit unlabeled); optional date cutoff; then 15 cleaning rules in `preprocess_posts_v2.py`.

---

### Stage 1: Topic extraction

**Working directory:** `stage1_topic_extraction/` · [stage README](stage1_topic_extraction/README.md)

**Prerequisite:** copy `posts_clean_v2.csv` from stage 0 into this folder.

| Step | Command | Input → output |
|------|---------|----------------|
| 1a (test) | `python topic_extraction_v2.py` | Uses `config.yaml` (`test_mode: true`, 5 batches) |
| 1b (full) | `python topic_extraction_v2.py --config config_fullrun.yaml` | `posts_clean_v2.csv` → `tagged_posts_v2.csv`, `checkpoint_v2.jsonl`, `topic_extraction_v2.log` |
| 1c | `python fix_csv_export.py` | `tagged_posts_v2.csv` → `tagged_posts_v2_fixed.csv` |
| 1d (QC) | `python verifyfix.py` | Reads **`tagged_posts_v2_fixed.csv`**; label sanity check to stdout |
| 1e | `python cleanup_tags.py` | `tagged_posts_v2_fixed.csv` → `tagged_posts_v2_clean.csv`, `cleanup_report.txt` |
| 1f (QC) | `python diagnose_outputs.py` | Distribution / sanity checks on tagged output |

**Requires:** `OPENAI_API_KEY`. Resume via `checkpoint_v2.jsonl`; delete checkpoint to start fresh.

---

### Stage 2: Raw pairing

**Working directory:** `stage2_pairing/` · [stage README](stage2_pairing/README.md)

**Prerequisite:** copy `tagged_posts_v2_clean.csv` from stage 1.

| Step | Command | Input → output |
|------|---------|----------------|
| 2 | `python build_pairs.py` | `tagged_posts_v2_clean.csv` → `paired_dataset_v2.csv` |

Uses `config_pairs.yaml`: groups by `topic_broad` by default; audience normalisation via `audience_map`. Rerun freely when thresholds change; no LLM cost.

---

### Stage 3: Topic normalisation

**Working directory:** `stage3_topic_normalisation/` · [stage README](stage3_topic_normalisation/README.md)

**Prerequisite:** copy `tagged_posts_v2_clean.csv` from stage 1.

| Step | Command | Input → output |
|------|---------|----------------|
| 3a | `python build_topic_vocabulary.py --step 1` | Seed pass → `canonical_topics_v1.json` |
| 3b | `python build_topic_vocabulary.py --step 2` | Mapping pass → `topic_cluster_map.json` |
| 3c (optional) | `python build_topic_vocabulary.py --dry-run` | Sample mapping → `mapping_dryrun_results.txt` |
| 3d (optional) | `python build_topic_vocabulary.py --check-map` | Near-duplicate invented topics → `consistency_check_results.txt` |
| 3e (optional) | `python build_topic_vocabulary.py --review-map` | Full audit → `mapping_review.txt` |
| 3f | `python fix_selfmap.py` | Fixes `"self-map"` artifact in JSON maps |
| 3g | `python apply_topic_map.py` | Applies map → `tagged_posts_v2_normalised.csv` |
| 3h | `python cleanup_pass.py --all-domains` | Merge **proposals** → `proposed_merges.csv` (does not modify map) |
| 3i | Human review | Edit / approve rows; run `review_merges.py`, `validate_merges.py`, `check_anchor_pattern.py` as needed |
| 3j | *(WIP)* `apply_cleanup_merges.py` | **Not implemented.** Apply approved merges after review |
| 3k | `python build_pairs.py` | Normalised pairing → `paired_dataset_normalised.csv` |

**Requires:** `OPENAI_API_KEY` for steps 3a, 3b, 3h.

`cleanup_pass.py` uses built-in defaults; optional `config_cleanup.yaml` overrides them if present.

**Shipped audit artifacts** (from completed run): logs, `mapping_review.txt`, `proposed_merges*.csv`, `anchor_pattern_review.csv`, JSON maps, vocab checkpoints.

---

## Data handoff between stages

All paths are **within the destination stage folder** unless noted.

```
posts.csv                          [external export, ~2.4 GB]
  └─ stage0 → posts_filtered.csv
       └─ stage0 → posts_clean_v2.csv
            └─ copy to stage1 → tagged_posts_v2.csv → … → tagged_posts_v2_clean.csv
                 ├─ copy to stage2 → paired_dataset_v2.csv
                 └─ copy to stage3 (same tagged_posts_v2_clean.csv)
                      └─ stage3 → tagged_posts_v2_normalised.csv, topic_cluster_map.json
                           └─ stage3 build_pairs → paired_dataset_normalised.csv
```

| File | Produced in | Needed in |
|------|-------------|-----------|
| `posts.csv` | External | stage 0 |
| `posts_clean_v2.csv` | stage 0 | stage 1 |
| `tagged_posts_v2_clean.csv` | stage 1 | stage 2, stage 3 |
| `tagged_posts_v2_normalised.csv` | stage 3 | (stage 3 final output) |
| `paired_dataset_v2.csv` | stage 2 | (stage 2 final output) |
| `paired_dataset_normalised.csv` | stage 3 | (stage 3 final output) |

---

## Environment variables

| Variable | Stages | Notes |
|----------|--------|-------|
| `OPENAI_API_KEY` | 1, 3 | Never commit. Set in shell or `.env` (gitignored). |

---

## Dependencies

```text
openai, PyYAML, tqdm     (stages 1 and 3, LLM scripts)
stdlib only              (most stage 0, 2, and QC scripts)
```

Install: `pip install -r requirements.txt`

---

## Security

- Do **not** commit API keys or raw `posts.csv`.
- `stage3_topic_normalisation/run.txt` in this repo has keys **redacted** (`[REDACTED]`).
- If your parent project still has an unredacted `prod/topic_normalisation/run.txt`, treat it as sensitive and do not publish.

---

## Audit trail

Each stage folder includes logs and review files from the reference run where applicable:

- **Stage 1:** `topic_extraction_v2.log`, `cleanup_report.txt`, `test_results.txt`
- **Stage 3:** `build_topic_vocabulary.log`, `cleanup_all_domains.log`, `mapping_review.txt`, merge review CSVs, JSON checkpoints

---

## Note

1. **`apply_cleanup_merges.py`**: WIP; pipeline stops at human review of `proposed_merges.csv`.
2. **Staged folders vs flat `prod/` layout**: original runs used a single `prod/` directory; this repo splits stages for clarity. Copy handoff files between folders as above.
3. **`build_pairs.py` exists twice**: stage 2 (raw labels, `topic_broad`) and stage 3 (normalised labels, `topic`); configs differ intentionally.
