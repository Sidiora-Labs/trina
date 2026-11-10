# Decisions and unresolved qualifications

Status: product design, not implementation evidence. Requirements and task statuses are in [spec.kvx](spec.kvx); measured facts are in [baseline.md](baseline.md). Nothing in the proposal tables is a result. Identifiers of the form N.M are ambiguous between clauses and tasks, so this document always writes "clause" or "task".

## Fixed product constraints

D01 to D10 restate `[decisions]` d_1 to d_10 of the specification. O1 to O4 are the operator decisions recorded in baseline.md section 5.

| ID | Constraint | Consequence |
|---|---|---|
| D01 | General memory is the target; a memory item is typed text with metadata | No stage requires a domain-specific field; generated and public data are required; results are per domain. |
| D02 | Three learned stages, each trained and evaluated separately, with a non-learned baseline first | BM25, untuned dense, hybrid fusion, an untuned off-the-shelf reranker and top-k truncation appear beside every learned result. The untuned reranker is the selector stage's baseline and fallback. |
| D03 | Supervision is utility for the reader, mined by leave-one-out to minimal sufficient sets | The bucket's lexical context labels are a pre-filter and weak label only. |
| D04 | Teacher is `gpt-6.1-sol` through the direct provider API | The gateway route is not used for labels; teacher identity, reasoning effort and prompt version are recorded on every label. |
| D05 | Raw bucket objects are streamed and never persisted; source prefixes are read-only | Only redacted, derived records are stored; the client exposes list and streamed get only. |
| D06 | Public memory benchmarks are evaluation-only | Training loaders refuse them; the judge is validated; a held-out generated set and a held-out bucket slice are reported beside them. |
| D07 | One code path on CPU and GPU | The CPU host proves the pipeline with tiny models; the B200 runs the real training; loading streams and is bounded. |
| D08 | Base models, numerical thresholds and domain mix are fixed by measurement | Base models are fixed in task 2.5 and reviewed in task 6.4. Tolerances and thresholds named in clauses are declared in configuration by the task that first needs them and listed in this document. Their presence here is not a result. |
| D09 | Work runs as a Hyperfide fleet; done means `verify_cmd` passed | The main agent supervises and merges; SKIPPED is never PASS. |
| D10 | Use of a provider teacher and provider-derived trajectories is an operator decision | The licence position is recorded in [data.md](data.md) and the model card; the system does not judge it. |
| O1 | Target is general memory; the bucket supplies a coding-agent slice | Same as D01. |
| O2 | Teacher through the direct API key | Same as D04. |
| O3 | Fleet mode; the main agent does not implement tasks | Same as D09. |
| O4 | Build and smoke-test on the CPU host, then a test run on the B200 | Same as D07; wave 6 follows the CPU smoke run of task 5.3. |

## Proposed implementation choices

| ID | Proposal | Reason | What would change it |
|---|---|---|---|
| P01 | Python 3.12, `uv` with a pinned `uv.lock`, PyTorch, Hugging Face transformers; `pytest`, `ruff` and `mypy` as the gates named by task 0.1 | Python 3.12.3 is the only software on both hosts; a lockfile makes the bare GPU host reproducible (clause 14.1) | A dependency that cannot be installed on the GPU host from the lockfile. |
| P02 | `data/` and `runs/` are working directories ignored by git and by Hyperfide; task 0.1 adds them to `.gitignore` and `.hyperfideignore` | Datasets, caches, checkpoints and run records must never be committed (workflow rule `no_raw_data`) | Nothing foreseen. |
| P03 | Retriever is a bi-encoder; backbone candidates Qwen3-Embedding-0.6B (default), EmbeddingGemma, a ModernBERT-base encoder; an encoder of about 20 to 30 million parameters for the CPU smoke run | Clause 8.1 requires a bi-encoder from a configurable pretrained encoder; several candidates let task 2.5 choose by measurement | The untuned-dense rows of the baseline report, which task 2.5 produces for each candidate backbone; licence terms; throughput on the B200. |
| P04 | Retriever returns 100 candidates; retrieval metrics at K = 10, 50 and 100 | Leaves room for the selector to recover evidence ranked low by a single-vector model | Recall@K curves in the baseline report; stage latency. |
| P05 | Selector: each (query, candidate) pair encoded by a shared cross-encoder (ModernBERT-large or Qwen3-Reranker-0.6B class), then a small set transformer over the candidate vectors with no positional encoding, plus a learned no-evidence token; up to 50 candidates | Joint scoring (clause 9.1); order invariance by construction (clause 9.3); abstention as a first-class output (clause 9.4) | The untuned-reranker rows of the baseline report, which task 2.5 produces for each candidate reranker; failing to beat the pointwise reranker (clause 9.2); memory or latency at 50 candidates. |
| P06 | Assembler: learned utility head, budgeted greedy selection by utility per token with a redundancy penalty, unconditional final budget check | Clause 10.2 weighs utility against cost and redundancy; a greedy rule keeps the budget invariant simple to prove | Failing to beat top-k truncation across the sweep. |
| P07 | Ordering policies evaluated: chronological, by relevance, learned | Clause 10.3 requires a declared and evaluated policy | The ordering ablation in task 6.4. |
| P08 | Budget sweep: 512, 1,024, 2,048, 4,096 and 8,192 tokens | Clause 10.4 requires a declared sweep | Reader context limits; the fixed budget chosen for clause 12.2. |
| P09 | Store: SQLite for items, a BM25 library, a vector index library of the FAISS or hnswlib class, reciprocal-rank fusion | Embedded, no service to run on either host (clause 11.6); fusion needs no training | Approximate-against-exact recall (clause 7.3), incremental build cost, scope-filter behaviour. |
| P10 | Teacher, reader and judge are all `gpt-6.1-sol` through the teacher client; reader and judge identity are configuration and are recorded (clause 12.2) | One audited, capped route (clauses 6.1 and 6.2) | A judge false-accept rate that is too high (clause 12.2); cost; the operator's answer to Q6. Single-teacher bias is a stated limit (clause 15.4). |
| P11 | A local open model on the B200 as teacher is a later cost option | Could reduce label cost | Out of scope for this feature; needs its own decision. |
| P12 | Budget tokenizer is configurable; default is the reader's tokenizer | The budget is meaningful only in the reader's units | No local implementation of the reader's tokenizer being available; then a declared stand-in is used and recorded. |
| P13 | Contract encoding: canonical UTF-8 JSON, sorted keys, NFC text, fixed-precision UTC instants, `sha256:` identities with a domain-separated prefix, metadata excluded from identity | Clause 1.3; see [design.body.md](design.body.md) section 4 | Golden-vector disagreement between hosts; a need for metadata to affect identity. |
| P14 | Fallbacks per stage: hybrid fusion for the retriever, the untuned off-the-shelf reranker for the selector, top-k truncation for the assembler | Clause 13.4 requires a declared baseline per stage; these are the baselines of decision d_2 and clause 12.3. The selector's fallback is fixed by the specification; which reranker checkpoint is used is a proposal settled with M02 | A different retriever baseline winning in the baseline report. |
| P15 | Metrics and run records as JSONL and TOML/JSON files under `runs/`; configurations as TOML | Clause 11.6: open line-oriented format, no external service | Nothing foreseen. |

## Prior work that motivates the proposals

Names only; no figure from these works is used as a target or a result.

| Work | What it motivates here |
|---|---|
| SetR, Dynamic Passage Selector | Selecting a set rather than ranking independently (P05, clause 9.1). |
| Context-Picker | Minimal sufficient sets by leave-one-out (D03, task 3.1). |
| MemReranker, ConvMemory | Distilling a teacher into a memory reranker (clause 8.1, clause 9.2 comparison). |
| BudgetMem | Utility under a token budget (P06). |
| LongMemEval, LoCoMo, ConvoMem | Evaluation-only benchmarks (D06). Known flaws handled in [evaluation.md](evaluation.md): LoCoMo has answer-key errors and a lenient judge; LongMemEval-S fits a context window and its filler may be distinguishable from evidence, which is why clause 4.3 tests the generator with a discriminator. |

## Decisions deferred to measurement

| ID | Decision | Settled by |
|---|---|---|
| M01 | Retriever base model | Fixed in task 2.5 from the baseline report (untuned dense rows, one per candidate backbone); reviewed in task 6.4. |
| M02 | Selector base model and the untuned reranker used as the selector's baseline and fallback | Fixed in task 2.5 from the baseline report (untuned reranker rows, one per candidate reranker); reviewed in task 6.4. |
| M03 | Candidate counts (100 retrieved, 50 to the selector) | Task 2.5 recall curves; task 6.4 latency. |
| M04 | Per-stage quality thresholds and the pass rule against baselines, including the declared K and the declared confidence-interval rule of clause 8.2. Failing is per domain | Declared in configuration by the task that first needs each: the interval rule by task 2.3, K and the pass rule by task 2.5; reviewed in task 6.4. A threshold is frozen before the run it judges; a failing result is not turned into a pass by changing it afterwards. |
| M05 | Domain mix and per-domain sampling weights (clause 5.4) | Training configuration, outside the dataset version identity; declared in `configs/data/mix.toml` by task 2.4, fixed by measurement in task 2.5 and reviewed in task 6.4. |
| M06 | Tolerances: selector order invariance (clause 9.3), resume (clause 11.2), seed repeatability (clause 11.5) | Declared in configuration by the task that first needs each: task 4.2 for order invariance, task 3.4 for resume and seed repeatability. GPU values are re-measured in task 6.3. |
| M07 | Host-memory limit and B200 batch sizes | Task 3.4 measures; task 6.3 sizes for the host. |
| M08 | Approximate or exact vector search, and the library | Task 2.2 reports approximate against exact recall. |
| M09 | Long-item policy: chunking or truncation (clause 8.4) | Task 4.1. |
| M10 | Ordering policy used by default | Task 4.3; task 6.4 ablation. |
| M11 | Fixed reader budget for end-to-end evaluation (clause 12.2) and the acceptable judge false-accept rate | Task 2.5. |
| M12 | Teacher reasoning effort and prompt version | Task 1.3 configuration; task 3.3 audit. |
| M13 | Size of the production dataset and labelling campaign | The operator's answer to the Ask of task 4.4 (spend cap and target dataset size), given after the dry-run cost estimate is printed. The second labelling round of task 6.3 runs under the same cap. |
| M14 | Near-duplicate thresholds for generation and leakage checks | Tasks 2.1 and 2.4. |
| M15 | Sufficiency of the redaction pattern set on real data | Task 0.4 audit, run on real output in tasks 1.1, 2.4 and 4.4. |

## Open questions for the operator

| ID | Question | Current position |
|---|---|---|
| Q1 | Licence position on a provider teacher and provider-derived training data | The operator's decision (D10, decision d_10). The existing dataset's README notes that the provider's terms restrict using its outputs to develop competing models. Recorded in [data.md](data.md) and the model card, not judged. |
| Q2 | Fleet agent driver | `claude` by default in the workflow; `codex` is also present on the build host. |
| Q3 | Git remote and pull requests | No remote exists, so pull requests are off, although the workflow names a remote `origin`. Waves land on `main` locally. |
| Q4 | Cluster keys 4 and 5 and the Messages endpoints | Untested. Not used by any task. |
| Q5 | Price table and spend cap for the campaign, and the target dataset size | Not given. Clause 6.2 takes prices from a price table in configuration and stops before the cap; baseline.md records no price. Task 4.4 raises the Ask for the cap and the target size and prints the dry-run estimate before any paid call; neither is defaulted. The second labelling round of task 6.3 runs under the same cap. |
| Q6 | Whether the judge should be a different model from the teacher | Proposal P10 uses one model for teacher, reader and judge. A judge of the same family may prefer its own answers. Clause 12.2 makes reader and judge identity configuration, so a different judge is a configuration change; the choice and its cost are the operator's. |
| Q7 | Which public retrieval sets are trainable, and their licences | Benchmarks named above are evaluation-only. The trainable public retrieval sets are not yet chosen; each needs a licence entry in the registry of task 1.4, and a set whose licence is unknown is registered evaluation-only. |
| Q8 | Scope of the bucket snapshot | The bucket held 18 days of data at authoring and is still growing; the snapshot date for the production dataset version of task 4.4 is the operator's choice. |
| Q9 | GPU host access and persistence | Reached by password from the ignored credential file. Whether key-based access is preferred and whether the overlay root survives a restart are not settled; the second was not measured. |
| Q10 | Acceptance procedure for clause 15.5 | What the operator will exercise on the running system is not yet written down. |
