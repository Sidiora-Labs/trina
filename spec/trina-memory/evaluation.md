# Evaluation and qualification

Status: proposed contract, not implementation evidence. Requirements and tasks are in [spec.kvx](spec.kvx). This document owns requirement 12. **All 28 cases, TRM-001 through TRM-028, are UNRUN.** No quality, latency or throughput result is asserted here; the only measured facts are in [baseline.md](baseline.md). Parameters marked proposal are fixed by the tasks named in section 11.

## 1. Principles

- Supervision and scoring are utility for the reader, not lexical overlap (decision d_3).
- Every learned result stands beside the non-learned baselines (decision d_2, clause 12.3).
- Results are per domain and per category; an aggregate is never reported alone (clause 12.4).
- Public benchmarks are evaluation-only (decision d_6, clause 12.5).
- Evaluation calls the same pipeline interface as serving (clause 13.1, [serving.md](serving.md)).
- A dependency that could not be reached is SKIPPED, never PASS (decision d_9, clause 15.1).

## 2. Metric definitions (clause 12.1)

Implemented by task 2.3 with per-domain and per-category grouping built in. For query `q`: ranked list `r_1, r_2, …`; gold evidence set `G(q)`; selected set `S(q)`; `rel(d) = 1[d ∈ G(q)]`. `Q` is a set of queries.

```text
Recall@K(q)   = | {r_1..r_K} ∩ G(q) | / | G(q) |
DCG@K(q)      = Σ_{i=1..K} rel(r_i) / log2(i + 1)
nDCG@K(q)     = DCG@K(q) / Σ_{i=1..min(K,|G(q)|)} 1 / log2(i + 1)
RR(q)         = 1 / min{ i : r_i ∈ G(q) }        (0 when no gold item is ranked);      MRR = mean_q RR(q)

Precision(q)  = |S ∩ G| / |S|        Recall(q) = |S ∩ G| / |G|        F1(q) = 2PR / (P + R)
ExactSet(q)   = 1[ S(q) = G(q) ]     where G is the minimal sufficient set

Abstention:  U = unanswerable queries,  A = queries on which the system abstained
AbstPrecision = |A ∩ U| / |A|        AbstRecall = |A ∩ U| / |U|
```

Conventions (to be pinned by the hand-computed cases of task 2.3): retrieval metrics are averaged over queries with `G(q) ≠ ∅`; unanswerable queries are scored by the abstention metrics and by ExactSet (`S = G = ∅` scores 1); `P` with `S = ∅` and `G ≠ ∅` is 0; ties in a ranked list are broken by item identity before scoring. K is a declared configuration value (clause 8.2); proposal: K ∈ {10, 50, 100} reported, with K = 10 as the decision K. Where an item was chunked ([models.md](models.md) section 3.4), ranks are over parent items.

**Bootstrap intervals.** Each metric carries a bootstrap confidence interval (clause 12.1), and the rule by which an interval decides a comparison is declared in configuration (clause 8.2). Proposal: a percentile bootstrap interval, which resamples with replacement `R` times, recomputes the mean and reports the `α/2` and `1 − α/2` quantiles, with `R = 1,000`, a 95% interval, a recorded seed, and resampling by split group (session, persona or source document, clause 5.1) rather than by query, because queries within a group are dependent. Comparisons against a baseline use a paired bootstrap over the same resamples and report the interval of the difference.

## 3. End-to-end protocol (clause 12.2)

1. The pipeline under test turns each query into an `AssembledContext` at a **fixed budget** (proposal: 2,048 tokens under the declared tokenizer; the sweep of clause 10.4 is reported separately).
2. The **reader** answers from the assembled context only. An abstaining context is passed as empty; the reader may answer "not answerable".
3. The **judge** scores the answer against the reference as correct or incorrect, with abstention on an unanswerable query counted correct.
4. Scores are grouped per domain and category with bootstrap intervals.

Reader and judge identity are configuration and are recorded in every report (clause 12.2), with model, reasoning effort and prompt version recorded on every call. Both go through the teacher client of task 1.3 (direct API, cache, budget cap, host allow-list). Proposal: reader and judge are both `gpt-6.1-sol`. Reader, judge and label teacher being one model is a declared bias that the model card repeats (clause 15.4); whether to use a different judge model to reduce self-preference is an open operator question ([decisions.md](decisions.md) Q6).

### 3.1 Judge validation

Before a judge is used, it is validated on a labelled sample that includes **intentionally wrong answers** (task 2.5). The sample is built from generated data whose gold answers are known ([data.md](data.md)), not from benchmark answer keys: LoCoMo's published answer-key errors and lenient LLM judging are the motivating flaws.

| Sample stratum | Construction |
|---|---|
| Correct | Gold answer and paraphrases |
| Wrong entity or value | Gold answer with a substituted fact of the same type |
| Stale | Pre-update value on knowledge-update questions |
| Topically right, factually wrong | Fluent answer on the right topic that does not contain the fact |
| Partial | One of several required facts |
| Wrong abstention | "Not answerable" on an answerable question, and a confident answer on an unanswerable one |

```text
false-accept rate  FAR = (wrong answers judged correct) / (wrong answers)
false-reject rate  FRR = (correct answers judged incorrect) / (correct answers)
```

FAR is reported in every end-to-end report, overall and per stratum, with its interval. A maximum acceptable FAR is deferred (section 11). Proposed rule: a report produced with a judge whose FAR exceeds the declared maximum, or with no validation for the judge prompt version in use, is stamped `judge_unvalidated` and cannot be cited as a result. Task 2.5 proves that validation catches a deliberately lenient judge.

## 4. Mandatory baseline set (clause 12.3)

Every report contains every row; a missing row makes the report incomplete and the harness refuses to finalise it.

| Baseline | Definition |
|---|---|
| BM25 | BM25 candidates, top-k truncation to the budget |
| Untuned dense | The pretrained encoder with no training, same long-item policy, top-k truncation |
| Hybrid | Reciprocal-rank fusion of the two, top-k truncation |
| Untuned off-the-shelf reranker | A pretrained reranker with no training on Trina data, applied to the retriever's candidates, top-k truncation. It is the selector stage's baseline and its serving fallback |
| Top-k truncation | The learned retriever's ranking truncated greedily to the budget, with no selector or learned assembler |
| Full context where it fits | The whole scope history given to the reader when its token count fits the reader's context window; otherwise the cell reads `does not fit`, never blank. Needed because LongMemEval-S fits a context window |
| Oracle evidence | Gold evidence only (generated and public sets); on the bucket domain, the teacher's minimal sufficient set, labelled `teacher-oracle` |

Stage-level comparisons required by other clauses are also always present: untuned same encoder (8.2), pointwise reranker trained on the same data (9.2), greedy top-k truncation (10.2). The baseline report of task 2.5 carries an untuned dense row for each candidate retriever backbone and a row for each candidate untuned reranker; those rows fix the base-model choices (decision d_8).

## 5. Per-domain and per-category reporting (clause 12.4)

- Every example carries a domain tag (clause 5.4). Proposed domains, as in [data.md](data.md) section 1: `coding_agent` (bucket), `general_memory` (generated), `public:<set>` and `benchmark:<set>`.
- Categories are the six of clause 4.1 (single fact, multi-session reasoning, temporal reasoning, knowledge update, preference, unanswerable) plus each benchmark's own categories.
- **Rule.** The report writer takes the per-domain table as a required input. Emitting an aggregate without it raises (task 2.5 do_2, case TRM-013). Any aggregate states its weighting (macro over domains by default) and the per-domain counts beside it.
- A cell with too few queries for a meaningful interval is printed with its count and flagged, not dropped.

## 6. Evaluation sets and evaluation-only enforcement (clause 12.5)

| Set | Role | Notes |
|---|---|---|
| Held-out bucket test split | Coding-agent domain | `sha1(session_id) mod 100` rule, 5% test (clause 5.1) |
| Held-out generated set | General memory with known gold evidence | Held-out personas; same generator version |
| LongMemEval | Public, evaluation-only | Small variant fits a context window: full-context row is mandatory |
| LoCoMo | Public, evaluation-only | Answer-key errors: judge validated independently of its keys |
| ConvoMem | Public, evaluation-only | |
| MemoryAgentBench | Public, evaluation-only | |

Enforcement is three independent mechanisms: (1) the registry marks each set evaluation-only and the single shared training-loader entry point refuses it (task 1.4); (2) dataset assembly checks exact and near-duplicate content against every evaluation-only set and fails the build (clause 5.2, task 2.4); (3) the harness verifies, against the dataset manifest of the training run named in the bundle, that no evaluation item identity is present, and refuses to report otherwise. Case TRM-008 covers (1) and (3).

Prior work informing the design, cited by name only: SetR, Dynamic Passage Selector, Context-Picker, MemReranker, ConvMemory, BudgetMem. No number from them is reproduced or assumed.

## 7. Efficiency measurements (clause 12.6)

Reported per pipeline configuration on **declared hardware**, with build-host CPU and B200 figures never mixed in one row:

- latency at the median and 95th percentile **per stage** (candidate generation, retriever encoding, selector, assembler) and end to end, from timers inside the pipeline interface (task 5.1);
- assembled tokens (mean and distribution) against the budget;
- throughput in queries per second at a stated batch size and concurrency;
- candidate count, store size and precision mode, since they determine the numbers.

Warm-up queries are excluded and their count stated; failed queries are counted, not removed. Reader and judge time is reported separately and is not pipeline latency. Task 6.3 records GPU utilisation, peak GPU memory, throughput and stage latency in the B200 run record (case TRM-024).

## 8. Ablations (clause 12.7)

Run by task 6.4 on the GPU host, on the trained bundle, each through the same interface with one stage swapped for its declared baseline:

| Ablation | Replacement | Contribution reported as |
|---|---|---|
| No learned retriever | Hybrid baseline candidates | Difference in Recall@K and end-to-end accuracy |
| No selector | Untuned off-the-shelf reranker | Difference in evidence F1, exact set, abstention, accuracy |
| Set-wise vs pointwise | Pointwise reranker | Same metrics (clause 9.2) |
| No learned assembler | Greedy top-k truncation | Accuracy across the budget sweep (clauses 10.2, 10.4) |
| Ordering policy | Chronological, relevance, learned | Accuracy at the fixed budget (clause 10.3) |

Each contribution is a paired difference with an interval, per domain. A stage that fails to beat its baseline in a domain is recorded as **failing** for that domain and stays in the report and the model card (task 7.1).

## 9. Stable case registry

Copied from [spec.kvx](spec.kvx); the specification is authoritative. All cases are **UNRUN**.

| Case | Title | Tier | Clauses | Tasks | Status |
|---|---|---|---|---|---|
| TRM-001 | Contract round trip, identity stability and rejection | component | 1.1, 1.2, 1.3, 1.4 | 0.2 | UNRUN |
| TRM-002 | Configuration resolution and run record completeness | component | 11.1, 11.6 | 0.3 | UNRUN |
| TRM-003 | Credential hygiene | component | 3.1, 3.5 | 0.1, 0.4 | UNRUN |
| TRM-004 | Redaction and residual-secret audit | component | 3.2, 3.3 | 0.4, 1.1, 2.4 | UNRUN |
| TRM-005 | Bucket ingestion determinism, read-only access and resume | integration | 2.1, 2.2, 2.3, 2.4 | 1.1 | UNRUN |
| TRM-006 | Episode extraction fidelity | component | 2.5 | 1.2 | UNRUN |
| TRM-007 | Teacher client cache, budget cap and host refusal | integration | 3.4, 6.1, 6.2 | 1.3 | UNRUN |
| TRM-008 | Evaluation-only sets cannot reach training | component | 4.4, 12.5 | 1.4, 2.4 | UNRUN |
| TRM-009 | Generated history validity and indistinguishable evidence | integration | 4.1, 4.2, 4.3, 4.5 | 2.1 | UNRUN |
| TRM-010 | Group splits, leakage refusal and manifest | integration | 5.1, 5.2, 5.3, 5.4 | 2.4 | UNRUN |
| TRM-011 | Store scope isolation and baseline retrieval | component | 7.1, 7.2, 7.3, 7.4 | 2.2 | UNRUN |
| TRM-012 | Metric correctness | component | 12.1 | 2.3 | UNRUN |
| TRM-013 | End-to-end evaluation with validated judge and baselines | integration | 12.2, 12.3, 12.4 | 2.5 | UNRUN |
| TRM-014 | Utility labels recover a known minimal sufficient set | integration | 6.3, 6.4 | 3.1 | UNRUN |
| TRM-015 | Label audit accuracy | integration | 6.5 | 3.3 | UNRUN |
| TRM-016 | Hard negatives exclude evidence | component | 6.6 | 3.2 | UNRUN |
| TRM-017 | Training resume, determinism and memory bound | component | 11.2, 11.4, 11.5 | 3.4 | UNRUN |
| TRM-018 | Retriever compared against its untuned baseline | integration | 8.1, 8.2, 8.3, 8.4 | 2.2, 4.1 | UNRUN |
| TRM-019 | Set-wise selection, order invariance and abstention | integration | 9.1, 9.2, 9.3, 9.4 | 4.2 | UNRUN |
| TRM-020 | Assembler budget invariant and budget sweep | integration | 10.1, 10.2, 10.3, 10.4 | 4.3 | UNRUN |
| TRM-021 | Pipeline, bundle integrity, degradation and interfaces | integration | 13.1, 13.2, 13.3, 13.4 | 5.1, 5.2 | UNRUN |
| TRM-022 | CPU end-to-end smoke run | system | 11.3, 15.1 | 5.3 | UNRUN |
| TRM-023 | Remote bootstrap, preflight, detached launch and retrieval | system | 14.1, 14.2, 14.3, 14.4 | 6.1, 6.2 | UNRUN |
| TRM-024 | B200 test run | campaign | 11.3, 12.6, 14.5 | 6.3 | UNRUN |
| TRM-025 | Full evaluation and ablations | campaign | 8.2, 9.2, 10.2, 12.4, 12.7 | 6.4 | UNRUN |
| TRM-026 | Specification and evidence integrity | component | 15.1, 15.2, 15.3 | 0.1, 7.2 | UNRUN |
| TRM-027 | Documentation and release closure | system | 15.4, 15.5 | 7.1, 7.2 | UNRUN |
| TRM-028 | Production dataset and label campaign | campaign | 3.3, 5.3, 6.2, 6.4, 6.5 | 4.4 | UNRUN |

A real attempt yields PASS, FAIL or SKIPPED with a reason; evidence goes to [qualification.kvx](qualification.kvx) with revision, command, exit code, log and observation (clause 15.2). A component pass on fixtures with a fake teacher does not qualify a campaign-tier case.

## 10. Ordered execution and closure

| Stage | Work | Cases | Exit requirement |
|---|---|---|---|
| A: authoring | Specification validation, references, clause coverage | (TRM-026 planned) | Reviewable specification; every case UNRUN |
| B: foundation | Contract, configuration, hygiene, redaction | TRM-001–004, 026 | Component cases pass on real code paths |
| C: data and teacher | Ingestion, episodes, teacher client, adapters, generator, assembly | TRM-005–010 | Dataset version with manifest. Tasks 1.1, 1.3, 1.4 and 2.1 name their live dependency with `--require`, so their live parts must pass; a skip fails the task |
| D: baseline | Store, metrics, harness, judge validation, baseline report | TRM-011–013 | Baseline report exists, produced with a live teacher (`--require teacher`); base-model choices fixed and the thresholds of section 11 that task 2.5 owns declared |
| E: labels and stages | Labels, audit, negatives, training core, three stages, production dataset and label campaign | TRM-014–020, 028 | Each stage reported against its baseline, passing or failing; production dataset and label versions built under the operator's cap with both audits run |
| F: pipeline and CPU proof | Bundle, interfaces, smoke run | TRM-021, 022 | Smoke run within 30 minutes with a complete per-domain report |
| G: GPU campaign | Remote execution, B200 test run with the second labelling round, full evaluation and ablations on the GPU host | TRM-023–025 | Retrieved, checksum-verified run record and report; every task here names `--require gpu` |
| H: release | Documentation, evidence collection, operator acceptance | TRM-026, 027 | Clause 15.5: B200 end-to-end report and recorded operator acceptance |

The stage order controls qualification; implementation follows the task dependency graph in [implementation-plan.md](implementation-plan.md). A SKIPPED campaign case blocks closure; it is not converted to a pass by component evidence.

## 11. Thresholds deferred to measurement

The specification fixes three numbers outright: discriminator AUC at most 0.60 (clause 4.3), the 30-minute smoke bound (clause 11.3) and the 90/5/5 bucket split (clause 5.1). Everything below is **deferred** (decision d_8): base models are fixed in task 2.5 and reviewed in task 6.4, and each tolerance or threshold is declared in configuration by the task that first needs it and listed in [decisions.md](decisions.md). None has a value until the named task records one.

| Deferred number | Clause | Fixed by | Proposal until then |
|---|---|---|---|
| Retriever and selector base models; the untuned reranker | 8.1, 9.1, 12.3 | 2.5, reviewed in 6.4 | Qwen3-Embedding-0.6B; ModernBERT-large class; Qwen3-Reranker-0.6B class |
| Declared K and declared confidence-interval rule for "beats" a baseline; failing is per domain | 8.2, 9.2, 10.2 | 2.5 | In each domain, the lower bound of the paired 95% interval above zero, at K = 10 |
| Maximum judge false-accept rate | 12.2 | 2.5 | none stated; measured first |
| Fixed end-to-end budget | 12.2 | 2.5 | 2,048 tokens |
| Bootstrap resamples and level | 12.1 | 2.3 | 1,000 resamples, 95% |
| Approximate-search recall floor; build-agreement tolerance | 7.3 | 2.2 | report only |
| Order-invariance tolerances `δ_p`, `δ_set` | 9.3 | 4.2, re-measured in 6.3 | none stated |
| Selection thresholds `θ`, `θ_∅` | 9.4 | 4.2 (validation split) | tuned, not preset |
| Resume tolerance `tol_resume`; seed tolerance `tol_seed` | 11.2, 11.5 | 3.4, GPU values in 6.3 | exact on CPU deterministic mode |
| Host-memory limit | 11.4 | 3.4, 6.3 | 16 GB smoke, 40 GB B200 |
| Domain mix and sampling weights (training configuration, outside the dataset version identity) | 5.4 | 2.5, reviewed in 6.4 | see [data.md](data.md) |
| B200 test-run wall-clock and size | 14.5 | 6.3 | see [remote-execution.md](remote-execution.md) |
| Minimum cell size for an interval | 12.4 | 2.5 | flag, do not drop |
