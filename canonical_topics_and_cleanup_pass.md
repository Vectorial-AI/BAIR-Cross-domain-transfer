# Canonical Topic Vocabulary and Cleanup Pass

---

## 1. From Raw Labels to Canonical Topics

### The fragmentation problem

The initial topic extraction run (`topic_extraction_v2.py`) gave the LLM complete vocabulary freedom. Each post was tagged with a 2-5 word topic label, lowercase, with no constraint on phrasing. The intent was to observe what labels emerged naturally from the data before imposing any vocabulary structure.

The result: **17,081 unique topic labels** from 22,954 posts. The average label appeared 1.3 times. 70% of posts carried a label that appeared exactly once. The vocabulary was too fragmented to group or compare across platforms.

The fragmentation had a predictable cause: the same underlying subject generated dozens of surface variants. "User experience," "user experience design," "developer user experience," "ux design challenges," and "user experience issues" all referred to the same evaluation dimension, but as distinct strings they formed separate cells with no overlap.

### The identify-and-invent pass

The normalisation approach, called identify-and-invent, works in two stages per domain. First, the most frequently occurring raw labels are taken as seed topics. These represent concepts the data itself surfaced as recurring, without any prior vocabulary imposition. Second, every remaining raw label is passed in batches to an LLM with the seed list: map this label to the nearest seed if one fits, or invent a new canonical label if none does. Labels that are genuinely too specific to cluster with anything (named events, highly particular releases, one-off discussions) map to themselves.

The pass was run across nine active domains: Software Engineering, AI, Security, EdTech, Career, Business and Strategy, Data and Analytics, Leadership and Management, Open Source. Healthcare and Other are excluded from the active analysis scope.

### Results

17,081 raw labels collapsed to **5,408 canonical topics**, a 68% reduction. Post coverage went from 30% to 86.9% (the fraction of posts carrying a label that appears at least twice). The distribution of topic frequency shifted from a near-flat long tail to a power-law shape with meaningful high-frequency anchors.

Per-domain breakdown:

| Domain | Canonical topics | Self-map rate |
|---|---|---|
| Software Engineering | 2,273 | 43.1% |
| Business and Strategy | 751 | 42.1% |
| Security | 574 | 38.1% |
| Open Source | 346 | 34.2% |
| Career | 440 | 21.6% |
| Data and Analytics | 263 | 24.7% |
| EdTech | 410 | 18.3% |
| AI | 226 | 10.9% |
| Leadership and Management | 125 | 12.0% |
| **Total** | **5,408** | **30.1% overall** |

The self-map rate is the fraction of raw labels that mapped to themselves rather than clustering with an existing canonical topic. High self-map rates in Software Engineering and Business and Strategy reflect genuinely large and diverse corpora with many named-event labels that warrant their own entries. Low self-map rates in AI and Leadership and Management reflect more concentrated vocabulary with cleaner clustering.

**Before and after, aggregate:**

| Metric | Before normalisation | After normalisation |
|---|---|---|
| Unique topic labels | 17,081 | 5,408 |
| Posts on singleton label | 70.0% | 13.1% |
| Posts on label appearing 2+ times | 30.0% | 86.9% |
| Topics with 10+ posts | 50 | 443 |
| Topics with 30+ posts | 4 | 88 |

---

## 2. The Cleanup Pass

### Why a second pass was needed

The identify-and-invent pass is effective at collapsing high-frequency synonym clusters and routing mid-frequency labels into nearby seeds. It is structurally less effective at the long tail of singleton labels that are genuinely near-synonymous with an existing canonical topic but weren't close enough to be matched in the first pass. Examples:

- "browser automation tools" should map to "automation tools" but the seed match wasn't triggered
- "ab testing design patterns" should map to "design patterns" for the same reason
- "incident response culture" and "incident response resilience" should both collapse into "incident response"

These are vocabulary variants of the same underlying stimulus. The cleanup pass exists specifically to find and merge them before downstream analysis runs.

### Architecture

Four stages, strictly separated. No merge is applied automatically at any stage.

**Stage 1a: Jaccard synonym matching.** For each singleton label in a domain, compute word-level Jaccard similarity against every canonical topic in that domain. Any pair scoring 0.5 or above becomes a synonym candidate. The threshold captures strong surface overlap while filtering pairs that share only one or two incidental words.

**Stage 1b: Token-overlap grouping.** Singleton labels not matched in Stage 1a are grouped by shared token sets. Groups are capped at 9 labels. Groups exceeding the cap are recursively re-split up to three times at a stricter threshold. Labels that find no group after re-splitting are explicitly logged as NO_MATCH and tracked separately. No label is silently dropped.

**Stage 2: LLM audit.** Every candidate from Stage 1 goes to the LLM for a binary judgment, without exception. Synonym pairs receive a prompt asking whether the raw label is a narrower facet or different phrasing of the same subject, or a genuinely different subject. Token-overlap groups receive a prompt asking whether all labels in the group concern the same underlying concept. An API failure at this stage produces an explicit REJECTED verdict rather than a silent approval.

**Stage 3: Output to `proposed_merges.csv`.** Every candidate is written with full provenance: the raw label, the proposed canonical target, the Jaccard score, the stage it came from (synonym or group), the LLM verdict, and the LLM's stated reasoning. The file is auditable at the row level. Nothing in it has been applied.

**Stage 4: Apply merges.** This stage has not been built. The script that would write approved merges into `topic_cluster_map.json` and trigger downstream re-runs has been deliberately withheld until the proposed_merges.csv review is complete.

### Prompt iteration

The first live run on Software Engineering produced 117 approvals out of 380 synonym candidates (30.8%). Review of the output revealed the LLM was applying an inconsistent standard: it correctly rejected genuinely different subjects but also rejected valid merges using the criterion "this is a narrower subset, not the same thing." This contradicted the normalisation logic that preceded it, which explicitly treats specificity narrowing as the same stimulus. "Postgresql performance optimization" maps to "postgresql performance" in the identify-and-invent pass. The cleanup audit should follow the same rule.

Both audit prompts were rewritten to state explicitly that a narrower label or different facet of the same subject should be approved, and only a genuinely different subject should be rejected. Worked examples from the actual found errors were embedded in both prompts.

After the rewrite, Software Engineering re-run: 249 approvals out of 380 candidates (65.5%). The same known-bad pairs were still correctly rejected. Spot-checks at the same Jaccard band confirmed the model continued to discriminate by content rather than blanket-approving.

### Results across all nine domains

| Domain | Synonym approved | Synonym rejected | Group approved | Group rejected |
|---|---|---|---|---|
| Software Engineering | 249 | 131 | 7 | 2 |
| AI | 7 | 9 | 9 | 8 |
| Security | 71 | 37 | 18 | 8 |
| EdTech | 12 | 23 | 7 | 2 |
| Career | 28 | 27 | 8 | 5 |
| Business and Strategy | 52 | 34 | 23 | 19 |
| Data and Analytics | 18 | 10 | 5 | 4 |
| Leadership and Management | 4 | 1 | 4 | 4 |
| Open Source | 15 | 20 | 8 | 6 |
| **Total** | **456** | **292** | **89** | **58** |

Total proposed approvals: **545** (456 synonym + 89 group).
Total proposed rejections: **350** (292 + 58).
Overall approval rate: **60.9%**.

Career and Open Source reject more synonyms than they approve, which is a positive signal. It indicates the model is discriminating per-domain rather than blanket-approving.

**Projected impact if all 545 merges are committed:**

Each synonym merge removes exactly one entry from the canonical vocabulary. Each group merge of size N removes N-1 entries. The projected reduction across all nine domains is approximately 530-560 topics, bringing the total from 5,408 to approximately 4,850-4,880. For Software Engineering specifically: 2,273 projected to 2,020, a reduction of 253 (-11.1%).

---

## 3. Known-Good and Known-Bad Merges

### Known-good (high confidence)

These cases were confirmed correct by both the LLM audit and independent review:

**Synonym merges:**
- `browser automation tools` to `automation tools` - facet of the same subject
- `ab testing design patterns` to `design patterns` - narrower instance of the canonical
- `incident response challenges` to `incident response` - angle variant of the same practice
- `incident response culture` to `incident response` - angle variant
- `malware detection tools` to `malware detection` - tool vs. practice, same subject
- `malware detection techniques` to `malware detection` - phrasing variant
- `personal branding impact` to `personal branding` - consequence framing, same stimulus
- `career transition stability` to `career transition` - facet variant
- `ai coding workflow tools` to `ai coding tools` - phrasing variant

**Group merges:**
- `[ai ethics | ai risks and ethics]` - two phrasings of the same topic
- `[multi-model ai systems | multi-modal ai systems]` - spelling variant
- `[compliance automation tools | compliance automation]` - tool vs. practice
- `[security vulnerability fixes | security vulnerability trends]` - two discussion angles on the same subject
- `[study habits | study techniques]` - phrasing variants

### Known-bad: the anchor-topic over-absorption pattern

The most consequential failure mode identified in review: a broad meta-label absorbs unrelated singletons that share only a generic surface phrase. The raw label is a post about some opinion adjacent to the canonical anchor's domain but not about the same underlying subject.

Confirmed cases:
- `software engineering adaptability` to `software engineering best practices` - adaptability is a career and mindset topic, not a best-practices discussion
- `software engineering impact` to `software engineering best practices` - impact is an outcome framing, not a methodology
- `software engineering mistakes` to `software engineering best practices` - mistakes is a reflection framing
- `developer tools career update` to `developer tools` - career update is a personal milestone, not a technical tools discussion
- `developer tools appreciation` to `developer tools` - appreciation is a community sentiment post, not a tools discussion
- `community recognition` to `postgresql community recognition` - routing a generic label to a product-specific anchor incorrectly
- `build system comparison` to `build system improvements` - comparison and improvement are different discussion types

These share a common structure: the raw label sounds related to the canonical anchor because it borrows words from the same domain, but the actual post is about a different subject.

**Correctly rejected (genuinely different subjects despite surface overlap):**
- `auth service scaling` vs `notification service scaling` - two different services
- `c10k problem` vs `hibernate n+1 problem` - two distinct named technical problems
- `api documentation issues` vs `api monitoring issues` - documentation and monitoring are different dimensions
- `log injection vulnerabilities` vs `command injection vulnerabilities` - two distinct vulnerability types

**One unresolved case:**
- `async data fetching` to `frontend data fetching` - approved by LLM audit, disputed by independent review on the grounds that async does not imply frontend. Not yet adjudicated.

---

## 4. Current State and Blocking Items

### What is in the repository

`topic_cluster_map.json` contains the current canonical vocabulary: 5,408 topics across nine domains, produced by the identify-and-invent normalisation pass. The cleanup pass merges are in `proposed_merges.csv` as proposals. They have not been applied.

### What is blocking the commit

Four items, in order of priority:

**1. Two domains have not been validated.** Data and Analytics and Open Source were flagged during development as having visible anchor-topic risk patterns in their proposed merge candidates. The validator script has not been run against their live output. It is unknown whether those specific risk cases were approved or rejected.

**2. 59 disputed merges have not been reviewed row by row.** An independent review of Software Engineering's 389-row proposed merge list produced 95 disagreements with the LLM audit. 59 are cases where the independent review rejected LLM-approved merges, mostly on anchor-topic grounds. These are likely correct catches but have not been individually adjudicated.

**3. Stage 4 has not been built.** The apply script that writes approved merges into `topic_cluster_map.json` and triggers downstream re-runs does not exist. It was deliberately withheld until the review is complete.

**4. One specific unresolved merge.** "async data fetching" to "frontend data fetching" needs a decision.

---

## 5. Evaluation Design

Evaluating the cleanup pass requires checking two things independently: whether the approved merges are correct (do the two labels actually describe the same subject) and whether the rejections are correct (are the rejected pairs genuinely different subjects, or did the model err on the side of caution).

**Approval evaluation - stratified sample.** Draw from `proposed_merges.csv` stratified by Jaccard band (0.5-0.6, 0.6-0.75, 0.75+), merge type (synonym vs group), and domain. Aim for 30-50 rows per stratum. Lower-Jaccard approvals are where false positives concentrate and deserve the most scrutiny.

**Human judgment task.** For each sampled pair, the evaluator reads the raw label, the proposed canonical target, and 3-5 posts drawn from each label. The judgment is binary: do these posts describe the same underlying subject, or different subjects? This is a simpler task than free-form topic assignment. It is a same-thing/different-thing call.

**Rejection evaluation.** The same process applied to a sample of rejected merges. If human evaluation finds that rejected pairs are frequently the same subject, the audit prompt is too strict. If approved pairs frequently describe different subjects, the prompt is too loose.

**Anchor-topic check.** For all approvals where the canonical target is a high-frequency, broadly-named topic such as "developer tools," "software engineering best practices," or "database management," the evaluator checks whether the raw label is actually about that subject or merely adjacent to it. This check was identified as necessary based on the failure pattern described in Section 3.

**Inter-annotator agreement.** Running the same sample with two independent annotators produces an agreement rate that measures how well-defined the judgment task is. Disagreements surface the cases that require a third opinion or an explicit rule.

The data for this evaluation is in `proposed_merges.csv` and the main corpus CSV, both of which are in the repository.
