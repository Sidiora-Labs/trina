# Traceability: trina-memory

Generated from [spec.kvx](spec.kvx); regenerate with `python3 spec/trina-memory/tools/validate_spec.py --render`. Do not edit by hand. A clause `N.M` is `[req.N] ac_M`. Mappings describe coverage ownership, not execution evidence.

| Clause | Requirement | Implementation tasks | Product cases | Contract |
|---|---|---|---|---|
| 1.1 | Domain-agnostic memory, query and context contract | 0.2 | TRM-001 | [design.body.md](design.body.md) |
| 1.2 | Domain-agnostic memory, query and context contract | 0.2 | TRM-001 | [design.body.md](design.body.md) |
| 1.3 | Domain-agnostic memory, query and context contract | 0.2 | TRM-001 | [design.body.md](design.body.md) |
| 1.4 | Domain-agnostic memory, query and context contract | 0.2 | TRM-001 | [design.body.md](design.body.md) |
| 2.1 | Streaming ingestion of bucket trajectories | 1.1 | TRM-005 | [data.md](data.md) |
| 2.2 | Streaming ingestion of bucket trajectories | 1.1 | TRM-005 | [data.md](data.md) |
| 2.3 | Streaming ingestion of bucket trajectories | 1.1 | TRM-005 | [data.md](data.md) |
| 2.4 | Streaming ingestion of bucket trajectories | 1.1 | TRM-005 | [data.md](data.md) |
| 2.5 | Streaming ingestion of bucket trajectories | 1.2 | TRM-006 | [data.md](data.md) |
| 3.1 | Credential and data security | 0.1, 0.4 | TRM-003 | [security.md](security.md) |
| 3.2 | Credential and data security | 0.4, 1.1 | TRM-004 | [security.md](security.md) |
| 3.3 | Credential and data security | 0.4, 1.1, 2.4, 4.4 | TRM-004, TRM-028 | [security.md](security.md) |
| 3.4 | Credential and data security | 1.3 | TRM-007 | [security.md](security.md) |
| 3.5 | Credential and data security | 0.1, 0.4 | TRM-003 | [security.md](security.md) |
| 4.1 | General-memory data generation and public sets | 2.1 | TRM-009 | [data.md](data.md) |
| 4.2 | General-memory data generation and public sets | 2.1 | TRM-009 | [data.md](data.md) |
| 4.3 | General-memory data generation and public sets | 2.1 | TRM-009 | [data.md](data.md) |
| 4.4 | General-memory data generation and public sets | 1.4 | TRM-008 | [data.md](data.md) |
| 4.5 | General-memory data generation and public sets | 2.1 | TRM-009 | [data.md](data.md) |
| 5.1 | Dataset assembly, splits and leakage control | 2.4 | TRM-010 | [data.md](data.md) |
| 5.2 | Dataset assembly, splits and leakage control | 2.4 | TRM-010 | [data.md](data.md) |
| 5.3 | Dataset assembly, splits and leakage control | 2.4, 4.4 | TRM-010, TRM-028 | [data.md](data.md) |
| 5.4 | Dataset assembly, splits and leakage control | 2.4 | TRM-010 | [data.md](data.md) |
| 6.1 | Teacher access and utility labels | 1.3 | TRM-007 | [labeling.md](labeling.md) |
| 6.2 | Teacher access and utility labels | 1.3, 3.1, 4.4 | TRM-007, TRM-028 | [labeling.md](labeling.md) |
| 6.3 | Teacher access and utility labels | 3.1 | TRM-014 | [labeling.md](labeling.md) |
| 6.4 | Teacher access and utility labels | 3.1, 4.4 | TRM-014, TRM-028 | [labeling.md](labeling.md) |
| 6.5 | Teacher access and utility labels | 3.3, 4.4 | TRM-015, TRM-028 | [labeling.md](labeling.md) |
| 6.6 | Teacher access and utility labels | 3.2 | TRM-016 | [labeling.md](labeling.md) |
| 7.1 | Memory store and candidate generation | 2.2 | TRM-011 | [models.md](models.md) |
| 7.2 | Memory store and candidate generation | 2.2 | TRM-011 | [models.md](models.md) |
| 7.3 | Memory store and candidate generation | 2.2 | TRM-011 | [models.md](models.md) |
| 7.4 | Memory store and candidate generation | 2.2 | TRM-011 | [models.md](models.md) |
| 8.1 | Trained retriever | 4.1 | TRM-018 | [models.md](models.md) |
| 8.2 | Trained retriever | 4.1, 6.4 | TRM-018, TRM-025 | [models.md](models.md) |
| 8.3 | Trained retriever | 2.2, 4.1 | TRM-018 | [models.md](models.md) |
| 8.4 | Trained retriever | 4.1 | TRM-018 | [models.md](models.md) |
| 9.1 | Set-wise selector | 4.2 | TRM-019 | [models.md](models.md) |
| 9.2 | Set-wise selector | 4.2, 6.4 | TRM-019, TRM-025 | [models.md](models.md) |
| 9.3 | Set-wise selector | 4.2 | TRM-019 | [models.md](models.md) |
| 9.4 | Set-wise selector | 4.2 | TRM-019 | [models.md](models.md) |
| 10.1 | Budgeted context assembler | 4.3 | TRM-020 | [models.md](models.md) |
| 10.2 | Budgeted context assembler | 4.3, 6.4 | TRM-020, TRM-025 | [models.md](models.md) |
| 10.3 | Budgeted context assembler | 4.3 | TRM-020 | [models.md](models.md) |
| 10.4 | Budgeted context assembler | 4.3 | TRM-020 | [models.md](models.md) |
| 11.1 | Reproducible training system | 0.3 | TRM-002 | [training.md](training.md) |
| 11.2 | Reproducible training system | 3.4 | TRM-017 | [training.md](training.md) |
| 11.3 | Reproducible training system | 5.3, 6.3 | TRM-022, TRM-024 | [training.md](training.md) |
| 11.4 | Reproducible training system | 3.4 | TRM-017 | [training.md](training.md) |
| 11.5 | Reproducible training system | 3.4 | TRM-017 | [training.md](training.md) |
| 11.6 | Reproducible training system | 0.3 | TRM-002 | [training.md](training.md) |
| 12.1 | Evaluation | 2.3 | TRM-012 | [evaluation.md](evaluation.md) |
| 12.2 | Evaluation | 2.5 | TRM-013 | [evaluation.md](evaluation.md) |
| 12.3 | Evaluation | 2.5 | TRM-013 | [evaluation.md](evaluation.md) |
| 12.4 | Evaluation | 2.5, 6.4 | TRM-013, TRM-025 | [evaluation.md](evaluation.md) |
| 12.5 | Evaluation | 1.4, 2.4 | TRM-008 | [evaluation.md](evaluation.md) |
| 12.6 | Evaluation | 5.1, 6.3 | TRM-024 | [evaluation.md](evaluation.md) |
| 12.7 | Evaluation | 6.4 | TRM-025 | [evaluation.md](evaluation.md) |
| 13.1 | Inference pipeline and serving | 5.1 | TRM-021 | [serving.md](serving.md) |
| 13.2 | Inference pipeline and serving | 5.2 | TRM-021 | [serving.md](serving.md) |
| 13.3 | Inference pipeline and serving | 5.1 | TRM-021 | [serving.md](serving.md) |
| 13.4 | Inference pipeline and serving | 5.1 | TRM-021 | [serving.md](serving.md) |
| 14.1 | Remote GPU execution | 6.1 | TRM-023 | [remote-execution.md](remote-execution.md) |
| 14.2 | Remote GPU execution | 6.2 | TRM-023 | [remote-execution.md](remote-execution.md) |
| 14.3 | Remote GPU execution | 6.1, 6.2 | TRM-023 | [remote-execution.md](remote-execution.md) |
| 14.4 | Remote GPU execution | 6.2 | TRM-023 | [remote-execution.md](remote-execution.md) |
| 14.5 | Remote GPU execution | 6.3 | TRM-024 | [remote-execution.md](remote-execution.md) |
| 15.1 | Workflow, evidence and documentation | 0.1, 5.3 | TRM-022, TRM-026 | [README.md](README.md) |
| 15.2 | Workflow, evidence and documentation | 0.1, 7.2 | TRM-026 | [README.md](README.md) |
| 15.3 | Workflow, evidence and documentation | 0.1 | TRM-026 | [README.md](README.md) |
| 15.4 | Workflow, evidence and documentation | 7.1 | TRM-027 | [README.md](README.md) |
| 15.5 | Workflow, evidence and documentation | 7.2 | TRM-027 | [README.md](README.md) |

## Canonical case ownership

A case's tasks are the implementation packages responsible for its driver, behavior or final integration. A case status is authored state in spec.kvx; a component passing never certifies a full case.

| Case | Scope | Status | Tasks | Independent oracle |
|---|---|---|---|---|
| TRM-001 | Contract round trip, identity stability and rejection (component) | UNRUN | 0.2 | Golden vectors reproduce byte for byte; malformed and unknown-version records raise typed errors; one constructor serves two domains. |
| TRM-002 | Configuration resolution and run record completeness (component) | UNRUN | 0.3 | A run record reloads to the exact resolved configuration, revision, seeds and metrics with no network access. |
| TRM-003 | Credential hygiene (component) | UNRUN | 0.1, 0.4 | A planted credential or a loosened file mode fails the gate; no credential value appears in captured logs, errors or artifacts. |
| TRM-004 | Redaction and residual-secret audit (component) | UNRUN | 0.4, 1.1, 2.4 | Every synthetic secret kind is redacted with correct counts, and the audit fails on a planted survivor. |
| TRM-005 | Bucket ingestion determinism, read-only access and resume (integration) | UNRUN | 1.1 | Two builds over one snapshot give identical identities; an interrupted build resumes to the same result; no write reaches the source prefixes and no raw object is left on disk. |
| TRM-006 | Episode extraction fidelity (component) | UNRUN | 1.2 | Items, kinds, order and times match the fixture trajectories, and weak labels are distinguishable from gold labels. |
| TRM-007 | Teacher client cache, budget cap and host refusal (integration) | UNRUN | 1.3 | A repeated request costs nothing, the cap stops the campaign before it is exceeded, and a call to an unlisted host or with unredacted content is refused. |
| TRM-008 | Evaluation-only sets cannot reach training (component) | UNRUN | 1.4, 2.4 | A training loader given an evaluation-only set raises, and the dataset manifest contains none of its items. |
| TRM-009 | Generated history validity and indistinguishable evidence (integration) | UNRUN | 2.1 | All six categories are present with gold evidence, invalid questions are dropped and counted, and the discriminator AUC is at most 0.60. |
| TRM-010 | Group splits, leakage refusal and manifest (integration) | UNRUN | 2.4 | A planted cross-split group, near-duplicate or benchmark item fails the build; a clean build has a stable version identity and a complete manifest. |
| TRM-011 | Store scope isolation and baseline retrieval (component) | UNRUN | 2.2 | No query returns an item from another scope; incremental and full index builds agree under exact search; results are deterministic. |
| TRM-012 | Metric correctness (component) | UNRUN | 2.3 | Every metric matches hand-computed values and an independent reference, including edge cases. |
| TRM-013 | End-to-end evaluation with validated judge and baselines (integration) | UNRUN | 2.5 | The report holds every baseline per domain and category, the judge's false-accept rate is measured, and an aggregate-only report cannot be produced. |
| TRM-014 | Utility labels recover a known minimal sufficient set (integration) | UNRUN | 3.1 | On a teacher with known behaviour the minimal set is recovered exactly, and a killed campaign resumes without duplicate spend. |
| TRM-015 | Label audit accuracy (integration) | UNRUN | 3.3 | Reported precision, recall and self-agreement match fixtures with a known error rate. |
| TRM-016 | Hard negatives exclude evidence (component) | UNRUN | 3.2 | No evidence item is among the negatives, and negatives score above random by the baseline. |
| TRM-017 | Training resume, determinism and memory bound (component) | UNRUN | 3.4 | Resumed and uninterrupted runs agree within tolerance, repeated seeds agree, and an unbounded loader trips the memory limit. |
| TRM-018 | Retriever compared against its untuned baseline (integration) | UNRUN | 2.2, 4.1 | Held-out Recall@K exceeds BM25 and the untuned encoder per domain or the run is recorded as failing; a mismatched index is refused. |
| TRM-019 | Set-wise selection, order invariance and abstention (integration) | UNRUN | 4.2 | On jointly-required evidence the set-wise model beats the pointwise one, selections are order-invariant within tolerance, and unanswerable queries yield an empty set. |
| TRM-020 | Assembler budget invariant and budget sweep (integration) | UNRUN | 4.3 | No assembled context exceeds its budget over randomised inputs; provenance and truncation marks are intact; quality against top-k truncation is reported at every budget. |
| TRM-021 | Pipeline, bundle integrity, degradation and interfaces (integration) | UNRUN | 5.1, 5.2 | Command line, HTTP and in-process results agree; a corrupted bundle is refused; a missing stage yields a degraded-marked response. |
| TRM-022 | CPU end-to-end smoke run (system) | UNRUN | 5.3 | Every stage completes on the build host within 30 minutes with a complete per-domain report and an explicit list of skipped dependencies. |
| TRM-023 | Remote bootstrap, preflight, detached launch and retrieval (system) | UNRUN | 6.1, 6.2 | Bootstrap is idempotent, a failing preflight refuses launch, a run survives a dropped connection, artifacts verify by checksum, and no credential appears in a process listing or log. |
| TRM-024 | B200 test run (campaign) | UNRUN | 6.3 | All three stages train and evaluate on the B200 and the run record holds GPU utilisation, peak GPU memory, throughput and stage latency. |
| TRM-025 | Full evaluation and ablations (campaign) | UNRUN | 6.4 | Per-domain results with every baseline and ablation are reported, and each stage is recorded as passing or failing against its baseline. |
| TRM-026 | Specification and evidence integrity (component) | UNRUN | 0.1, 7.2 | The validator passes on the specification and fails on a planted dangling reference, uncovered clause and stale derived view; a verify_cmd with an unmet required dependency fails; no case is marked passed without a log. |
| TRM-027 | Documentation and release closure (system) | UNRUN | 7.1, 7.2 | Documented commands execute, card figures match run records, stated limitations are present, and operator acceptance is recorded. |
| TRM-028 | Production dataset and label campaign (campaign) | UNRUN | 4.4 | The production dataset and label versions match their manifests, spend stayed under the operator's cap, both audits ran on these versions, and a rerun resumes without new spend. |
