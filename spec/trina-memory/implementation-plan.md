# Implementation Plan: trina-memory

Generated from [spec.kvx](spec.kvx); regenerate with `python3 spec/trina-memory/tools/validate_spec.py --render`. Do not edit by hand.

Task statuses below record current implementation progress. Verification commands are task deliverables; a command being listed is not evidence that it exists or passes.

Dependencies determine eligibility. Tasks in one wave with no dependency path between them run in parallel and have disjoint scopes. Component success does not qualify a complete product case or campaign.

| Task | Wave | Scope | Requires | Owner | Status |
|---|---:|---|---|---|---|
| 0.1 | 0 | Project scaffold, toolchain, gates and credential hygiene check | None | foundation | in_progress |
| 0.2 | 0 | Memory, query and assembled-context contract | 0.1 | foundation | pending |
| 0.3 | 0 | Configuration resolution and run records | 0.1 | foundation | pending |
| 0.4 | 0 | Secret loading, redaction and residual-secret audit | 0.1 | foundation | pending |
| 1.1 | 1 | Streaming bucket ingestion with snapshot, dedupe and resume | 0.2, 0.3, 0.4 | data | pending |
| 1.2 | 1 | Episode extraction from agent trajectories | 1.1 | data | pending |
| 1.3 | 1 | Teacher client with cache, budget cap and host allow-list | 0.2, 0.3, 0.4 | teacher | pending |
| 1.4 | 1 | Public dataset and benchmark adapters | 0.2, 0.3 | data | pending |
| 2.1 | 2 | General-memory history generator | 1.3 | data | pending |
| 2.2 | 2 | Memory store with BM25, dense and hybrid candidate generation | 0.2, 0.3 | retrieval | pending |
| 2.3 | 2 | Retrieval and selection metrics with confidence intervals | 0.2, 0.3 | evaluation | pending |
| 2.4 | 2 | Dataset assembly, group splits, leakage checks and manifest | 1.2, 1.4, 2.1 | data | pending |
| 2.5 | 2 | End-to-end evaluation harness and baseline report | 1.3, 2.2, 2.3, 2.4 | evaluation | pending |
| 3.1 | 3 | Utility labelling by sufficiency and leave-one-out | 1.3, 2.2, 2.4 | teacher | pending |
| 3.2 | 3 | Hard-negative mining | 2.2, 2.4 | retrieval | pending |
| 3.3 | 3 | Label quality audit | 3.1 | teacher | pending |
| 3.4 | 3 | Training core: loop, checkpoints, resume, precision and memory bounds | 0.2, 0.3 | training | pending |
| 4.1 | 4 | Retriever training | 3.1, 3.2, 3.4, 2.5 | models | pending |
| 4.2 | 4 | Set-wise selector training | 3.1, 3.2, 3.4, 2.5 | models | pending |
| 4.3 | 4 | Budgeted assembler training | 3.1, 3.4, 2.5 | models | pending |
| 4.4 | 4 | Production dataset version and label campaign | 2.4, 3.1, 3.2, 3.3 | data | pending |
| 5.1 | 5 | Inference pipeline and model bundle | 4.1, 4.2, 4.3 | serving | pending |
| 5.2 | 5 | Command-line and local HTTP interface | 5.1 | serving | pending |
| 5.3 | 5 | CPU end-to-end smoke run | 5.1, 2.5 | training | pending |
| 6.1 | 6 | GPU host bootstrap and credential handling | 0.4, 5.3 | infrastructure | pending |
| 6.2 | 6 | Remote sync, preflight, detached launch and retrieval | 6.1, 3.4 | infrastructure | pending |
| 6.3 | 6 | B200 test run | 6.2, 4.4 | infrastructure | pending |
| 6.4 | 6 | Full evaluation and ablations | 6.3 | evaluation | pending |
| 7.1 | 7 | Documentation, data card and model card | 6.4 | release | pending |
| 7.2 | 7 | Release closure with running-system evidence | 7.1 | release | pending |

## 0.1: Project scaffold, toolchain, gates and credential hygiene check

Status: **in_progress**. Owner: `foundation`. Dependencies: none.

Implementation scope:

- `pyproject.toml`
- `uv.lock`
- `.python-version`
- `Makefile`
- `.gitignore`
- `.hyperfideignore`
- `src/trina/__init__.py`
- `src/trina/cli.py`
- `tests/conftest.py`
- `tests/cases/test_0_1.py`
- `scripts/check_hygiene.py`
- `scripts/record_evidence.py`

Focused gate: `uv run pytest tests/cases/test_0_1.py -q`.

Acceptance clauses: 3.1, 3.5, 15.1, 15.2, 15.3.
Canonical cases: TRM-003 (UNRUN), TRM-026 (UNRUN).

Implementation and qualification obligations:

1. Install uv for the build host, create the trina package under src/ with Python 3.12, pin dependencies in uv.lock, and provide make test and make lint as the fleet gates (pytest, ruff, mypy). Add data/ and runs/ to .gitignore and .hyperfideignore.
2. Add a pytest marker scheme for external dependencies (network, bucket, teacher, gpu) whose unmet condition reports SKIPPED with a reason; make test prints the skipped count and never folds it into the pass count. Add a --require option naming dependencies that must be met: with it, an unmet named dependency is a failure, so a verify_cmd that needs a live dependency cannot pass on skips.
3. Write scripts/check_hygiene.py and wire it into make lint: fail when a credential file is not mode 600, is not ignored by git or by Hyperfide, or when a credential pattern appears in a tracked file. Write scripts/record_evidence.py, which appends an evidence entry (revision, command, exit code, log, observation) to qualification.kvx; the main supervisor runs it when a wave lands.
4. test_0_1.py runs the gates on the real tree, proves the hygiene check fails on a planted fixture and passes on the repository, and runs the specification validator with its stale-view check.

## 0.2: Memory, query and assembled-context contract

Status: **pending**. Owner: `foundation`. Dependencies: 0.1.

Implementation scope:

- `src/trina/schema/**`
- `tests/cases/test_0_2.py`
- `tests/fixtures/schema/**`

Focused gate: `uv run pytest tests/cases/test_0_2.py -q`.

Acceptance clauses: 1.1, 1.2, 1.3, 1.4.
Canonical cases: TRM-001 (UNRUN).

Implementation and qualification obligations:

1. Define versioned MemoryItem, Query, Candidate, AssembledContext and Provenance types with canonical serialization and content-derived identities; reject unknown versions and malformed records with typed errors.
2. Define the tokenizer interface and one default implementation; expose a single count function that every budget computation must call.
3. test_0_2.py covers round trips with golden vectors, identity stability, rejection cases, and a property test that no required field is domain-specific by building items from a coding episode and a conversational session through the same constructor.

## 0.3: Configuration resolution and run records

Status: **pending**. Owner: `foundation`. Dependencies: 0.1.

Implementation scope:

- `src/trina/config/**`
- `src/trina/runs/**`
- `configs/base/**`
- `tests/cases/test_0_3.py`

Focused gate: `uv run pytest tests/cases/test_0_3.py -q`.

Acceptance clauses: 11.1, 11.6.
Canonical cases: TRM-002 (UNRUN).

Implementation and qualification obligations:

1. Implement layered configuration that resolves to one frozen document with a digest; unknown keys are errors.
2. Implement the run record: resolved configuration, code revision, dataset version, seeds, hardware facts, metrics as JSONL and checkpoint index, written under an ignored runs/ directory with no external service.
3. test_0_3.py resolves layered fixtures, proves digest stability and unknown-key rejection, and writes and reloads a run record from a real toy run.

## 0.4: Secret loading, redaction and residual-secret audit

Status: **pending**. Owner: `foundation`. Dependencies: 0.1.

Implementation scope:

- `src/trina/security/**`
- `Makefile`
- `tests/cases/test_0_4.py`
- `tests/fixtures/security/**`

Focused gate: `uv run pytest tests/cases/test_0_4.py -q`.

Acceptance clauses: 3.1, 3.2, 3.3, 3.5.
Canonical cases: TRM-003 (UNRUN), TRM-004 (UNRUN).

Implementation and qualification obligations:

1. Implement a secrets loader that reads the environment and the ignored credential files, returns opaque values that redact themselves in repr, str and logging, and never writes them. Credential files are ignored and so are absent from task worktrees: locate them through TRINA_SECRETS_DIR, defaulting to the main repository root resolved from git's common directory.
2. Implement redaction for API keys, cloud and repository tokens, bearer tokens, private keys and password and token assignments, and dropping of IP addresses and installation identifiers, with counts by kind; client headers are dropped entirely.
3. Implement the residual-secret audit over a sample of records and an artifact scanner exposed as a command; wire it into make lint for produced artifacts, and make it callable by every dataset build.
4. test_0_4.py uses synthetic secrets only: every pattern is redacted, counts are right, the audit catches a planted secret, and a secret passed through logging and an exception message does not appear in captured output.

## 1.1: Streaming bucket ingestion with snapshot, dedupe and resume

Status: **pending**. Owner: `data`. Dependencies: 0.2, 0.3, 0.4.

Implementation scope:

- `src/trina/data/bucket/**`
- `configs/data/bucket.toml`
- `tests/cases/test_1_1.py`
- `tests/fixtures/bucket/**`

Focused gate: `uv run pytest tests/cases/test_1_1.py -q --require bucket`.

Acceptance clauses: 2.1, 2.2, 2.3, 2.4, 3.2, 3.3.
Canonical cases: TRM-004 (UNRUN), TRM-005 (UNRUN).

Implementation and qualification obligations:

1. Implement a read-only S3 client wrapper that exposes list and streamed get only, so writes and deletes under source prefixes are impossible through it; read datasets/v2 trajectories first and raw call objects second.
2. Implement snapshot listing, parallel streamed extraction with bounded memory per worker, session collapse to segments, retry and cross-source dedupe, skip reasons with counts, a failed-object ledger and resumable shards. Redact before any record is written, and run the residual-secret audit on the written shards.
3. Record the listing snapshot manifest and prove a rebuild over the same snapshot yields identical record identities.
4. test_1_1.py runs the real pipeline against synthetic fixture objects served by a local S3-compatible endpoint, including an interrupted run that resumes, and runs a small live-bucket slice marked bucket. Take split assignments from the existing v2 session index and verify the residue mapping against it instead of assuming which residues are train, validation and test.

## 1.2: Episode extraction from agent trajectories

Status: **pending**. Owner: `data`. Dependencies: 1.1.

Implementation scope:

- `src/trina/data/episodes/**`
- `tests/cases/test_1_2.py`
- `tests/fixtures/episodes/**`

Focused gate: `uv run pytest tests/cases/test_1_2.py -q`.

Acceptance clauses: 2.5.
Canonical cases: TRM-006 (UNRUN).

Implementation and qualification obligations:

1. Convert each segment into ordered memory items by kind with times, and into decision points: at each model step, the query is the step's situation and the memory is every earlier item in the session.
2. Carry the bucket's lexical reference labels as weak labels marked as such, and derive natural signals where present: an item re-read later, and items referenced after a compaction.
3. test_1_2.py checks order, kinds, times and counts on fixtures built from the documented trajectory format, and that weak labels are never exposed under the name of gold labels.

## 1.3: Teacher client with cache, budget cap and host allow-list

Status: **pending**. Owner: `teacher`. Dependencies: 0.2, 0.3, 0.4.

Implementation scope:

- `src/trina/teacher/client/**`
- `configs/teacher/**`
- `tests/cases/test_1_3.py`

Focused gate: `uv run pytest tests/cases/test_1_3.py -q --require teacher`.

Acceptance clauses: 3.4, 6.1, 6.2.
Canonical cases: TRM-007 (UNRUN).

Implementation and qualification obligations:

1. Implement the teacher client against the direct provider endpoint with model and reasoning effort from configuration, a response cache keyed by canonical request digest, rate limiting, bounded retries and concurrency.
2. Implement spend accounting per call, a dry-run cost estimate, and a hard budget cap that reserves each request's worst-case cost from a configured price table and a bounded output length, so it stops before the cap is exceeded. Refuse any destination host not on the configured allow-list, and refuse content that fails the redaction check.
3. test_1_3.py runs the real client against a local fake provider for cache hits, retry, rate limit, cap enforcement and host refusal, and one live call marked teacher that records prompt tokens.

## 1.4: Public dataset and benchmark adapters

Status: **pending**. Owner: `data`. Dependencies: 0.2, 0.3.

Implementation scope:

- `src/trina/data/public/**`
- `configs/data/public.toml`
- `tests/cases/test_1_4.py`
- `tests/fixtures/public/**`

Focused gate: `uv run pytest tests/cases/test_1_4.py -q --require network`.

Acceptance clauses: 4.4, 12.5.
Canonical cases: TRM-008 (UNRUN).

Implementation and qualification obligations:

1. Implement adapters that convert public retrieval sets and memory benchmarks to the contract, recording licence, provenance and download checksum in a registry.
2. Mark memory benchmarks evaluation-only in the registry and make the shared training-loader entry point refuse any set so marked.
3. test_1_4.py converts fixture samples of each format, checks the registry fields, proves the training loader refuses an evaluation-only set, and runs real downloads marked network.

## 2.1: General-memory history generator

Status: **pending**. Owner: `data`. Dependencies: 1.3.

Implementation scope:

- `src/trina/data/synth/**`
- `configs/data/synth.toml`
- `tests/cases/test_2_1.py`
- `tests/fixtures/synth/**`

Focused gate: `uv run pytest tests/cases/test_2_1.py -q --require teacher`.

Acceptance clauses: 4.1, 4.2, 4.3, 4.5.
Canonical cases: TRM-009 (UNRUN).

Implementation and qualification obligations:

1. Generate multi-session histories from sampled personas, topics and styles with recorded seeds, planting evidence and writing questions for the six categories with gold evidence item identities.
2. Generate evidence and filler sessions through the same process, filter near-duplicates, and report distribution by category and length.
3. Validate each answerable question with the teacher (correct from gold evidence alone, wrong on the history with that evidence removed) and each unanswerable question by the teacher abstaining on the full history; drop and count failures. Train the surface-feature discriminator and fail when its held-out AUC exceeds 0.60.
4. test_2_1.py runs the generator end to end on a fake teacher with deterministic output, and a small live generation marked teacher with a spend cap.

## 2.2: Memory store with BM25, dense and hybrid candidate generation

Status: **pending**. Owner: `retrieval`. Dependencies: 0.2, 0.3.

Implementation scope:

- `src/trina/store/**`
- `configs/store/**`
- `tests/cases/test_2_2.py`

Focused gate: `uv run pytest tests/cases/test_2_2.py -q`.

Acceptance clauses: 7.1, 7.2, 7.3, 7.4, 8.3.
Canonical cases: TRM-011 (UNRUN), TRM-018 (UNRUN).

Implementation and qualification obligations:

1. Implement the persisted store with add, supersede and delete under a stable logical key, scope isolation, and time and kind filters; expose recency as a candidate feature.
2. Implement BM25, dense retrieval with a pluggable encoder, and reciprocal-rank fusion; build indexes incrementally, record the encoder version in the index, and refuse a query whose encoder version differs from the index.
3. test_2_2.py proves scope isolation with adversarially similar items in another scope, determinism for a fixed index, incremental build equivalence to a full build under exact search, filter behaviour, and reports approximate against exact recall.

## 2.3: Retrieval and selection metrics with confidence intervals

Status: **pending**. Owner: `evaluation`. Dependencies: 0.2, 0.3.

Implementation scope:

- `src/trina/eval/metrics/**`
- `tests/cases/test_2_3.py`

Focused gate: `uv run pytest tests/cases/test_2_3.py -q`.

Acceptance clauses: 12.1.
Canonical cases: TRM-012 (UNRUN).

Implementation and qualification obligations:

1. Implement Recall@K, nDCG@K, MRR, evidence precision, recall and F1, exact sufficient-set match, abstention precision and recall, and bootstrap confidence intervals, with per-domain and per-category grouping built in.
2. test_2_3.py checks every metric against hand-computed cases and an independent reference implementation, including empty gold sets and ties.

## 2.4: Dataset assembly, group splits, leakage checks and manifest

Status: **pending**. Owner: `data`. Dependencies: 1.2, 1.4, 2.1.

Implementation scope:

- `src/trina/data/assemble/**`
- `configs/data/mix.toml`
- `tests/cases/test_2_4.py`
- `tests/fixtures/assemble/**`

Focused gate: `uv run pytest tests/cases/test_2_4.py -q`.

Acceptance clauses: 3.3, 5.1, 5.2, 5.3, 5.4, 12.5.
Canonical cases: TRM-004 (UNRUN), TRM-008 (UNRUN), TRM-010 (UNRUN).

Implementation and qualification obligations:

1. Assemble bucket episodes, generated histories and public sets into one dataset version with domain tags, group-level splits (bucket sessions by the sha1 mod 100 rule) and declared per-domain sampling weights.
2. Check exact and near-duplicate content across splits and against every evaluation-only set; fail the build on a violation. Run the residual-secret audit on the output.
3. Write the manifest and immutable version identity. test_2_4.py builds from fixtures, plants a cross-split group, a near-duplicate and a benchmark item and proves each fails the build.

## 2.5: End-to-end evaluation harness and baseline report

Status: **pending**. Owner: `evaluation`. Dependencies: 1.3, 2.2, 2.3, 2.4.

Implementation scope:

- `src/trina/eval/harness/**`
- `configs/eval/**`
- `tests/cases/test_2_5.py`
- `tests/fixtures/eval/**`

Focused gate: `uv run pytest tests/cases/test_2_5.py -q --require teacher`.

Acceptance clauses: 12.2, 12.3, 12.4.
Canonical cases: TRM-013 (UNRUN).

Implementation and qualification obligations:

1. Implement the harness: run a pipeline over an evaluation set, have a reader answer from the assembled context at a fixed budget, judge the answer, and report per domain and category with the metrics of 2.3.
2. Validate the judge on a labelled sample that includes intentionally wrong answers and report its false-accept rate. Refuse to emit an aggregate without the per-domain table.
3. Implement the baseline set: BM25, untuned dense for each candidate retriever backbone, hybrid, each candidate untuned reranker, top-k truncation, full context where it fits, oracle evidence. Produce the baseline report that later tasks compare against and that fixes the open base-model choices.
4. test_2_5.py runs the harness on fixtures with a fake reader and judge, proves the judge validation catches a lenient judge, and runs a small live baseline marked teacher.

## 3.1: Utility labelling by sufficiency and leave-one-out

Status: **pending**. Owner: `teacher`. Dependencies: 1.3, 2.2, 2.4.

Implementation scope:

- `src/trina/teacher/labels/**`
- `configs/labels/utility.toml`
- `tests/cases/test_3_1.py`

Focused gate: `uv run pytest tests/cases/test_3_1.py -q`.

Acceptance clauses: 6.2, 6.3, 6.4.
Canonical cases: TRM-014 (UNRUN).

Implementation and qualification obligations:

1. For each query, take baseline candidates plus known evidence, ask the teacher to answer with the set, judge sufficiency, then prune by leave-one-out to a minimal sufficient set; record per-item necessity, a graded utility score per item, teacher identity, prompt version and judge outcome. For bucket queries the reference is the recorded next action; exclude and count queries whose reference the teacher cannot reproduce.
2. Use weak lexical labels and gold evidence to order pruning so that calls are spent where the outcome is uncertain. Make the campaign resumable and idempotent with a valid partial output at every point.
3. test_3_1.py runs the labeller on a fake teacher whose behaviour encodes a known minimal set and proves it is recovered, kills and resumes a campaign, and checks the cost estimate against actual spend.

## 3.2: Hard-negative mining

Status: **pending**. Owner: `retrieval`. Dependencies: 2.2, 2.4.

Implementation scope:

- `src/trina/data/negatives/**`
- `configs/labels/negatives.toml`
- `tests/cases/test_3_2.py`

Focused gate: `uv run pytest tests/cases/test_3_2.py -q`.

Acceptance clauses: 6.6.
Canonical cases: TRM-016 (UNRUN).

Implementation and qualification obligations:

1. Mine negatives from BM25 and dense baseline rankings within the query's scope, sampled from a declared rank band, and exclude every item marked as evidence by gold or teacher labels when those exist. Re-apply the exclusion when training batches are built, so labels produced after mining still take effect.
2. test_3_2.py proves no evidence item appears among negatives, that negatives are harder than random by baseline score, and that mining is deterministic for a fixed seed.

## 3.3: Label quality audit

Status: **pending**. Owner: `teacher`. Dependencies: 3.1.

Implementation scope:

- `src/trina/teacher/audit/**`
- `tests/cases/test_3_3.py`

Focused gate: `uv run pytest tests/cases/test_3_3.py -q --require teacher`.

Acceptance clauses: 6.5.
Canonical cases: TRM-015 (UNRUN).

Implementation and qualification obligations:

1. Measure teacher labels against known gold evidence on generated data (precision and recall of necessity and of minimal sets) and self-agreement on a repeated sample; write an audit report tied to the label set version.
2. test_3_3.py runs the audit on fixtures with a known error rate and proves the reported figures match; a live audit sample is marked teacher.

## 3.4: Training core: loop, checkpoints, resume, precision and memory bounds

Status: **pending**. Owner: `training`. Dependencies: 0.2, 0.3.

Implementation scope:

- `src/trina/train/core/**`
- `configs/train/base.toml`
- `tests/cases/test_3_4.py`

Focused gate: `uv run pytest tests/cases/test_3_4.py -q`.

Acceptance clauses: 11.2, 11.4, 11.5.
Canonical cases: TRM-017 (UNRUN).

Implementation and qualification obligations:

1. Implement the shared training loop with device selection, mixed precision, gradient accumulation, schedulers, streaming data loading, atomic checkpoints and resume, driven only by resolved configuration and writing run records.
2. Measure peak host memory and enforce the configured limit; record device utilisation, peak device memory and throughput in the run record; document remaining nondeterminism.
3. test_3_4.py trains a toy model on CPU, kills and resumes it and compares metrics with an uninterrupted run, repeats with the same seed, and proves the memory limit trips on an unbounded loader.

## 4.1: Retriever training

Status: **pending**. Owner: `models`. Dependencies: 3.1, 3.2, 3.4, 2.5.

Implementation scope:

- `src/trina/models/retriever/**`
- `src/trina/train/retriever/**`
- `configs/train/retriever*.toml`
- `tests/cases/test_4_1.py`

Focused gate: `uv run pytest tests/cases/test_4_1.py -q`.

Acceptance clauses: 8.1, 8.2, 8.3, 8.4.
Canonical cases: TRM-018 (UNRUN).

Implementation and qualification obligations:

1. Implement the bi-encoder with a configurable pretrained backbone, contrastive loss with in-batch and mined hard negatives, and distillation from teacher utility scores; declare and record the long-item policy.
2. Export embeddings with the model version and prove through the store interface of task 2.2 that an index built with another version is refused.
3. Report against BM25 and the untuned encoder per domain through the harness; a model below baseline is recorded as failing.
4. test_4_1.py trains the tiny configuration on CPU on fixture data, proves loss decreases and held-out Recall@K beats the untuned tiny encoder on the fixture, and proves the version mismatch refusal.

## 4.2: Set-wise selector training

Status: **pending**. Owner: `models`. Dependencies: 3.1, 3.2, 3.4, 2.5.

Implementation scope:

- `src/trina/models/selector/**`
- `src/trina/train/selector/**`
- `configs/train/selector*.toml`
- `tests/cases/test_4_2.py`

Focused gate: `uv run pytest tests/cases/test_4_2.py -q`.

Acceptance clauses: 9.1, 9.2, 9.3, 9.4.
Canonical cases: TRM-019 (UNRUN).

Implementation and qualification obligations:

1. Implement the selector so candidates are scored jointly with the query, with per-item inclusion probabilities and a no-evidence score; train on sufficient-set labels with a set-level objective.
2. Train a pointwise reranker on the same data as the comparison. Support variable candidate counts and train with candidate order shuffled.
3. test_4_2.py trains the tiny configuration on a fixture where the answer needs two items jointly, proves the set-wise model beats the pointwise one there, checks order invariance within tolerance, and checks an empty selection on unanswerable fixtures.

## 4.3: Budgeted assembler training

Status: **pending**. Owner: `models`. Dependencies: 3.1, 3.4, 2.5.

Implementation scope:

- `src/trina/models/assembler/**`
- `src/trina/train/assembler/**`
- `configs/train/assembler*.toml`
- `tests/cases/test_4_3.py`

Focused gate: `uv run pytest tests/cases/test_4_3.py -q`.

Acceptance clauses: 10.1, 10.2, 10.3, 10.4.
Canonical cases: TRM-020 (UNRUN).

Implementation and qualification obligations:

1. Implement budgeted selection that weighs learned utility against token cost and redundancy, with the budget as a hard constraint checked after assembly under the declared tokenizer.
2. Implement and evaluate declared ordering policies; mark any truncation or compression and map it to its source span, keeping provenance.
3. test_4_3.py property-tests the budget invariant over random candidates and budgets, compares against top-k truncation on fixtures across the budget sweep, and checks provenance and truncation marks.

## 4.4: Production dataset version and label campaign

Status: **pending**. Owner: `data`. Dependencies: 2.4, 3.1, 3.2, 3.3.

Implementation scope:

- `configs/campaign/**`
- `scripts/campaign.py`
- `tests/cases/test_4_4.py`

Focused gate: `uv run pytest tests/cases/test_4_4.py -q --require bucket,teacher`.

Acceptance clauses: 3.3, 5.3, 6.2, 6.4, 6.5.
Canonical cases: TRM-028 (UNRUN).

Implementation and qualification obligations:

1. Raise an Ask for the campaign spend cap and the target dataset size; neither is defaulted. Print the dry-run cost estimate for the operator before any paid call.
2. Build the production dataset version from the full bucket snapshot, the generator and the public sets; run utility labelling and hard-negative mining under the cap; run the residual-secret audit and the label audit on the result.
3. Write the manifests, the audit reports and the spend record. test_4_4.py verifies the produced versions against their manifests, that spend stayed under the cap, that both audits ran on these versions, and that a rerun resumes without new spend.

## 5.1: Inference pipeline and model bundle

Status: **pending**. Owner: `serving`. Dependencies: 4.1, 4.2, 4.3.

Implementation scope:

- `src/trina/pipeline/**`
- `src/trina/eval/harness/**`
- `tests/cases/test_5_1.py`

Focused gate: `uv run pytest tests/cases/test_5_1.py -q`.

Acceptance clauses: 12.6, 13.1, 13.3, 13.4.
Canonical cases: TRM-021 (UNRUN).

Implementation and qualification obligations:

1. Implement the single pipeline interface from query to assembled context over the store and the three stages, with latency measured per stage, and switch the evaluation harness to call it.
2. Define the versioned model bundle and refuse an inconsistent one. When a learned stage is missing, fall back to the declared baseline for that stage and mark the response degraded.
3. test_5_1.py runs the pipeline with tiny trained stages, with each stage removed in turn, and with a corrupted bundle.

## 5.2: Command-line and local HTTP interface

Status: **pending**. Owner: `serving`. Dependencies: 5.1.

Implementation scope:

- `src/trina/serve/**`
- `tests/cases/test_5_2.py`

Focused gate: `uv run pytest tests/cases/test_5_2.py -q`.

Acceptance clauses: 13.2.
Canonical cases: TRM-021 (UNRUN).

Implementation and qualification obligations:

1. Expose the pipeline as a command-line tool and a local HTTP service whose request and response are the versioned contract; bind to localhost by default.
2. test_5_2.py starts the real service, ingests memories, queries it over HTTP and through the command line, and checks the two agree with the in-process pipeline.

## 5.3: CPU end-to-end smoke run

Status: **pending**. Owner: `training`. Dependencies: 5.1, 2.5.

Implementation scope:

- `configs/smoke/**`
- `scripts/smoke.py`
- `tests/cases/test_5_3.py`

Focused gate: `uv run pytest tests/cases/test_5_3.py -q`.

Acceptance clauses: 11.3, 15.1.
Canonical cases: TRM-022 (UNRUN).

Implementation and qualification obligations:

1. Define the smoke configuration: a small dataset slice, tiny backbones, every stage from ingestion fixtures through labels, training of the three stages, bundle, pipeline and evaluation report.
2. test_5_3.py runs it on the build host, asserts completion within 30 minutes and a complete per-domain report, and lists every external dependency that was skipped.

## 6.1: GPU host bootstrap and credential handling

Status: **pending**. Owner: `infrastructure`. Dependencies: 0.4, 5.3.

Implementation scope:

- `src/trina/remote/bootstrap/**`
- `scripts/remote_bootstrap.sh`
- `tests/cases/test_6_1.py`

Focused gate: `uv run pytest tests/cases/test_6_1.py -q --require gpu`.

Acceptance clauses: 14.1, 14.3.
Canonical cases: TRM-023 (UNRUN).

Implementation and qualification obligations:

1. Implement an idempotent bootstrap for a bare host: toolchain and dependencies from the pinned lockfile, with host facts (GPU, driver, memory, disk) recorded.
2. Connect using the ignored credential file through an askpass mechanism so the password is never on a command line or in a log; place run credentials on the host in a mode-600 file and remove them on teardown.
3. test_6_1.py runs the bootstrap twice against a local container or the real host marked gpu, checks idempotence and recorded facts, and inspects process listings and logs for the credential.

## 6.2: Remote sync, preflight, detached launch and retrieval

Status: **pending**. Owner: `infrastructure`. Dependencies: 6.1, 3.4.

Implementation scope:

- `src/trina/remote/run/**`
- `tests/cases/test_6_2.py`

Focused gate: `uv run pytest tests/cases/test_6_2.py -q --require gpu`.

Acceptance clauses: 14.2, 14.3, 14.4.
Canonical cases: TRM-023 (UNRUN).

Implementation and qualification obligations:

1. Synchronise code and datasets by content hash; implement the preflight (GPU visible, free GPU memory, disk space, dataset checksums) that refuses to launch on failure.
2. Launch runs detached so they survive a dropped connection; implement status, log tailing, and retrieval of metrics, checkpoints and run records with checksum verification.
3. test_6_2.py launches the smoke configuration remotely, drops the connection mid-run, reattaches, retrieves the artifacts and verifies them; failing preflight fixtures must refuse to launch.

## 6.3: B200 test run

Status: **pending**. Owner: `infrastructure`. Dependencies: 6.2, 4.4.

Implementation scope:

- `configs/b200/**`
- `tests/cases/test_6_3.py`

Focused gate: `uv run pytest tests/cases/test_6_3.py -q --require gpu`.

Acceptance clauses: 11.3, 12.6, 14.5.
Canonical cases: TRM-024 (UNRUN).

Implementation and qualification obligations:

1. Define the B200 test-run configuration using the base models fixed by the baseline report, sized for 183 GB of GPU memory and 59 GB of host memory, training on the dataset and label versions produced by task 4.4.
2. Train the retriever, then run a second labelling round under the campaign cap on the trained retriever's candidates so the selector and assembler train on the candidates they will serve. Run all three stages and the evaluation on the host through the same code path as the CPU smoke run; record GPU utilisation, peak GPU memory, throughput and stage latency in the run record and retrieve the report.
3. test_6_3.py verifies the retrieved run record and report are complete and tied to the dataset version and code revision.

## 6.4: Full evaluation and ablations

Status: **pending**. Owner: `evaluation`. Dependencies: 6.3.

Implementation scope:

- `src/trina/eval/ablation/**`
- `configs/eval/ablation.toml`
- `tests/cases/test_6_4.py`

Focused gate: `uv run pytest tests/cases/test_6_4.py -q --require gpu`.

Acceptance clauses: 8.2, 9.2, 10.2, 12.4, 12.7.
Canonical cases: TRM-025 (UNRUN).

Implementation and qualification obligations:

1. On the GPU host, evaluate the trained bundle on the held-out generated set, the held-out bucket slice and the public benchmarks, per domain and category, with every baseline and the budget sweep.
2. Run the ablations: each learned stage replaced by its baseline, set-wise against pointwise selection, learned against top-k assembly. Record any stage that fails to beat its baseline as failing.
3. test_6_4.py checks the ablation runner on tiny bundles and validates the structure and completeness of the retrieved full report.

## 7.1: Documentation, data card and model card

Status: **pending**. Owner: `release`. Dependencies: 6.4.

Implementation scope:

- `README.md`
- `docs/**`
- `tests/cases/test_7_1.py`

Focused gate: `uv run pytest tests/cases/test_7_1.py -q`.

Acceptance clauses: 15.4.
Canonical cases: TRM-027 (UNRUN).

Implementation and qualification obligations:

1. Write the README, usage guide, data card and model card from the recorded runs, stating the single-teacher bias, the coding-heavy source data, the licence position and every result that failed its baseline.
2. test_7_1.py executes the documented commands against the smoke configuration and checks that every number in the cards matches a run record.

## 7.2: Release closure with running-system evidence

Status: **pending**. Owner: `release`. Dependencies: 7.1.

Implementation scope:

- `spec/trina-memory/qualification.kvx`
- `tests/cases/test_7_2.py`

Focused gate: `uv run pytest tests/cases/test_7_2.py -q`.

Acceptance clauses: 15.2, 15.5.
Canonical cases: TRM-026 (UNRUN), TRM-027 (UNRUN).

Implementation and qualification obligations:

1. As the one task permitted to edit qualification.kvx directly, collect the B200 run report, the full evaluation and the case results into qualification.kvx with revision, command, exit code, log and observation for each.
2. Present the running system to the operator for acceptance; closure is recorded only after that acceptance.
3. test_7_2.py checks that every case has recorded evidence or an explicit UNRUN or SKIPPED status and that no case is marked passed without a log.
