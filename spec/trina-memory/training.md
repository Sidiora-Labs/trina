# Reproducible training system

Status: proposed contract, not implementation evidence. Requirements and tasks are in [spec.kvx](spec.kvx). This document owns requirement 11. Toolchain, file layouts, limits and time budgets below are **proposals**; the only measured facts are in [baseline.md](baseline.md). Cases TRM-002, TRM-017 and TRM-022 are UNRUN.

## 1. Scope and toolchain

One training core (task 3.4) drives the three learned stages of [models.md](models.md). It is driven only by a resolved configuration and writes only run records. Proposed toolchain: Python 3.12, `uv` with a pinned `uv.lock`, PyTorch, Hugging Face transformers, with `pytest`, `ruff` and `mypy` as the gates of task 0.1. `data/` and `runs/` are ignored by git and by Hyperfide. There is no external experiment-tracking service (clause 11.6).

| Clause | Subject | Section | Task | Case |
|---|---|---|---|---|
| 11.1 | Resolved configuration and run record | 2, 3 | 0.3 | TRM-002 |
| 11.2 | Atomic checkpoints, resume equivalence | 4 | 3.4 | TRM-017 |
| 11.3 | One code path; 30-minute CPU smoke | 6, 9 | 5.3, 6.3 | TRM-022, TRM-024 |
| 11.4 | Mixed precision, accumulation, streaming, host-memory limit | 7, 8 | 3.4 | TRM-017 |
| 11.5 | Seed determinism, documented nondeterminism | 5 | 3.4 | TRM-017 |
| 11.6 | Local line-oriented metrics, no external service | 3.2 | 0.3 | TRM-002 |

## 2. Resolved configuration and digest (clause 11.1)

Configuration is layered: `configs/base/` then a stage file then a host profile (`smoke`, `b200`) then command-line overrides. Resolution produces **one frozen document**; a run reads nothing else.

- Unknown keys are errors at resolution time (task 0.3). There are no implicit defaults in code: a value absent from every layer is an error, so the resolved document is complete.
- Canonical form: sorted keys, normalised numbers, UTF-8, no comments. `config_digest = sha256(canonical bytes)`.
- The document names, never contains, secrets (clause 3.1): a credential appears as a reference such as an environment variable name. See [security.md](security.md).
- The document pins every input that affects results: dataset version (clause 5.3), label-set version, tokenizer identity, base checkpoint identity and revision, seeds, precision mode, accumulation, memory limit, the domain mix and per-domain sampling weights (clause 5.4: training configuration, outside the dataset version identity), and the tolerances of sections 4 and 5.
- A resumed run must present the same `config_digest` as the checkpoint; a mismatch is refused unless the changed keys are on a declared resume-safe list (proposal: output paths and logging interval only).

## 3. Run record (clauses 11.1, 11.6)

### 3.1 Layout

Proposed layout under the ignored `runs/` directory. `run_id` is `<UTC timestamp>-<stage>-<first 8 hex of config_digest>`.

```text
runs/<run_id>/
  config.resolved.json     the frozen document
  record.json              identity and provenance (table below)
  metrics.jsonl            append-only metrics, one JSON object per line
  telemetry.jsonl          host memory, device utilisation and device memory samples
  checkpoints/index.jsonl  append-only checkpoint index
  checkpoints/step-<n>/    one directory per checkpoint
  logs/train.log           redacted text log
  report/                  evaluation output written by the harness
  SHA256SUMS               digests of every file above, written at close
```

| `record.json` field | Content |
|---|---|
| `config_digest`, `run_id`, `stage`, `status` | `status` is `running`, `completed`, `failed`, `aborted_memory_limit` or `failing_baseline` |
| `code_revision` | Commit identity and a dirty-tree digest; on a host without `git` it is the value carried by the sync manifest ([remote-execution.md](remote-execution.md)) |
| `dataset_version`, `label_set_version` | Immutable identities from [data.md](data.md) and [labeling.md](labeling.md) |
| `seeds` | Global, data-order, initialisation and evaluation-bootstrap seeds |
| `hardware` | CPU count, RAM, device type, GPU model, GPU memory, driver, library versions, lockfile digest |
| `parents` | Run identities this run consumed (section 10) |
| `limits` | Configured host-memory limit and the measured peak (section 8) |
| `device` | Device utilisation summary, peak device memory and throughput, recorded by the training core (task 3.4 do_2); on a host with no GPU the device fields say so and throughput is still recorded |
| `nondeterminism` | The flags actually in effect (section 5) |

### 3.2 Metrics

`metrics.jsonl` is the open line-oriented format of clause 11.6. Each line: `{"t": <UTC>, "step": n, "split": "...", "name": "...", "value": x, "domain": "...", "resume": k}`. The file is append-only; after a resume, lines for steps beyond the restored checkpoint are superseded and readers take the last line per `(step, split, name, domain)`. Training and evaluation need no network service: the only network uses are fetching a base checkpoint and calling the teacher, both of which are explicit and cached. Under the marker scheme of task 0.1 a test that cannot reach them reports SKIPPED, and fails when its task's `verify_cmd` names the dependency with `--require`. Case TRM-002 reloads a record with network access disabled.

## 4. Checkpoints and resume (clause 11.2)

**Atomic write.** A checkpoint is written into a temporary sibling directory, every file is flushed and synced, the directory is renamed to `step-<n>`, the parent directory is synced, and only then is a line appended to `checkpoints/index.jsonl` with the step, file digests and `config_digest`. A directory absent from the index, or whose digests do not match, is not a checkpoint and is ignored and reported. A kill at any instant therefore leaves either the previous checkpoint or the new one, never a partial one.

**Contents.** Model weights, optimizer, scheduler, gradient-scaler state, the accumulation phase (always zero: checkpoints are taken only on optimizer-step boundaries), every random-generator state (Python, NumPy, PyTorch CPU and device), the data cursor (epoch, shard order, shard index, in-shard offset, shuffle-buffer seed), the step and the best-metric bookkeeping.

**Resume equivalence.** A run killed and resumed must reproduce the metrics of an uninterrupted run within a declared tolerance:

```text
for every logged (step, split, name):   | m_resumed − m_uninterrupted |  ≤  tol_resume(name)
```

`tol_resume` is a configuration value declared by the task that first needs it (decision d_8): task 3.4 measures it on the CPU toy model and declares it; the proposal for the CPU deterministic mode is exact equality, and the GPU value is measured during task 6.3. Retention (proposal): keep the last 2 and the best checkpoint, since the GPU host has finite disk.

## 5. Determinism policy (clause 11.5)

Fixed seeds must give the same metrics within a declared tolerance `tol_seed` **on the same hardware**; no cross-hardware equality is claimed. `tol_seed` is declared in configuration by task 3.4 alongside `tol_resume`.

Controls: one global seed from which data-order, initialisation and per-worker seeds are derived; deterministic shard order and shuffle buffer; deterministic-algorithm mode on in the smoke profile; fixed tie-breaks in mining and retrieval ([models.md](models.md) section 2.3).

Remaining nondeterminism that the run record must document (task 3.4 do_2):

| Source | Treatment |
|---|---|
| Nondeterministic GPU kernels and reduced-precision autocast | Deterministic mode attempted; kernels without a deterministic form are listed in `record.json` and covered by `tol_seed` |
| Data-loader worker timing | Order is fixed by the cursor, not arrival; batches are assembled in shard order |
| Approximate-index construction with threads | Single-threaded seeded builds for anything that feeds training |
| Teacher, reader and judge responses | Never called inside a training step; labels are a versioned input; evaluation calls are cached by request digest (clause 6.1) |
| Library and driver versions, hardware | Recorded; a change voids the comparison |
| Tokenizer parallelism | Output order is fixed; thread count is recorded |

## 6. Device handling: one code path (clause 11.3, decision d_7)

Device is a configuration value (`cpu`, `cuda`), never a branch in model or loop code. The stage code calls the same functions on both hosts; profile differences are data: model identity, sizes, precision mode, batch, worker count, memory limit. Forbidden: a "smoke" implementation of a stage, a mocked loss, or a skipped stage on CPU. The smoke profile differs from the B200 profile only in the resolved configuration, and the two documents are diffable. The build host has no GPU ([baseline.md](baseline.md)), so the GPU half of clause 11.3 is first exercised on the B200 in task 6.2 and is covered by task 6.3, which runs all three stages and the evaluation there through the same code path as the CPU smoke run (case TRM-024).

## 7. Mixed precision and gradient accumulation (clause 11.4)

- `precision` is `fp32` or `bf16` (proposal: `fp32` on CPU, `bf16` autocast on the B200). The same autocast context wraps both; on CPU with `fp32` it is a no-op. Loss computation for contrastive similarities stays in `fp32`.
- `accumulation = a` means one optimizer step per `a` micro-batches; the logged step is the optimizer step; gradient clipping and the scheduler act per optimizer step. The effective batch is recorded as `micro_batch × a`.
- Accumulation does not enlarge the in-batch negative pool of the retriever; that limitation and the gradient-caching option are in [models.md](models.md) section 3.2.
- Task 3.4 tests that `a = 1` with batch `2b` and `a = 2` with batch `b` agree on a toy model with a mean-reduced, non-contrastive loss within `tol_seed`.

## 8. Streaming data and the host-memory bound (clause 11.4)

**Why.** The GPU host has 59 GB of RAM against 183,359 MiB of GPU memory and 224 CPUs ([baseline.md](baseline.md) section 4). A loader that materialises a dataset, or that starts one worker per CPU with a private copy of anything large, exhausts host memory long before the GPU is full. Host memory, not GPU memory, is the binding constraint, so loading streams and is bounded by construction and by an enforced limit.

**Rules.**

1. Datasets are read as an iterable stream of shards; nothing requires the whole split in memory. Shuffling is shard-order shuffling plus a bounded shuffle buffer.
2. Tokenisation happens in the stream; no pre-tokenised full-dataset tensor is held in RAM. Large static arrays (embedding matrices for mining, indexes) are memory-mapped.
3. Worker count, prefetch depth, shuffle-buffer size and pinned-memory use are explicit configuration values with no "all CPUs" default.
4. `train.host_memory_limit` is a required configuration value. A monitor samples the resident memory of the process tree, writes it to `telemetry.jsonl`, and records the peak in `record.json`. When the limit is crossed the run stops with status `aborted_memory_limit` after writing its record; it never continues degraded.
5. The limit is "measured" in two senses: the peak is measured on every run, and the limit value itself is chosen from a measured profile. Proposal: 16 GB on the smoke profile and 40 GB on the B200 profile, leaving headroom below 59 GB; both are replaced by values measured in tasks 3.4 and 6.3.

Task 3.4 proves the limit trips on a deliberately unbounded loader (case TRM-017).

## 9. CPU smoke configuration (clause 11.3)

The smoke profile (task 5.3, `configs/smoke/`) completes **every stage with tiny models within 30 minutes on the build host** (64 CPUs, 251 GB RAM, no GPU). The 30-minute bound is normative; the split below is a proposed planning budget, not a measurement.

| Step | Content | Proposed budget |
|---|---|---|
| Fixture ingestion, episodes, generated slice, assembly | Real pipeline code on fixtures; fake teacher with deterministic output | 3 min |
| Baseline store and index | BM25 + tiny untuned encoder, flat index | 2 min |
| Labels and negatives | Fake-teacher utility labels, mined negatives | 2 min |
| Retriever, selector (+ pointwise), assembler | Tiny configurations of [models.md](models.md) | 5 + 6 + 2 min |
| Bundle, pipeline, evaluation report | Same interface as serving; per-domain report | 5 min |
| Slack | | 5 min |

The test asserts wall-clock completion, a complete per-domain report, and an explicit list of skipped external dependencies (clause 15.1). The smoke run proves plumbing; its metric values are not quality evidence.

## 10. Stage training order

Tasks 4.1, 4.2 and 4.3 are independent in the task graph (each requires 3.1, 3.4 and 2.5; the first two also 3.2). Inside one end-to-end run (smoke or B200) the proposed order is:

| Order | Stage | Needs from earlier stages | Produces |
|---|---|---|---|
| 0 | Baseline store | Dataset version | BM25 and untuned dense indexes, baseline candidates |
| 1 | Labels, negatives | Baseline candidates, teacher | Label-set version, hard negatives |
| 2 | Retriever | Labels, negatives, untuned checkpoint | Encoder with `encoder_version`; re-embedded index |
| 2a | Second labelling round | Trained retriever's candidates, teacher, the campaign cap | Label set over the candidates the later stages will serve |
| 3 | Selector and pointwise reranker | Labelled candidate sets from step 2a; retriever scores as features | Selector weights, thresholds `θ`, `θ_∅` |
| 4 | Assembler | Labels from step 2a; stage scores with presence masks | Utility head, ordering policy |
| 5 | Bundle and evaluation | All of the above | Bundle ([serving.md](serving.md)), report ([evaluation.md](evaluation.md)) |

Each stage's run record lists its `parents`. In the B200 run, steps 0 and 1 are the production dataset, label and negative versions of task 4.4, and step 2a is the second labelling round of task 6.3: after the retriever is trained, its candidates are labelled under the campaign cap so that the selector and assembler train on the candidates they will serve. Proposal: the smoke profile runs step 2a with the fake teacher, so the code path is the same. The stage tests of tasks 4.2 and 4.3 run on fixtures and do not depend on that round. Evaluation-only sets are refused by the shared training-loader entry point (clauses 4.4, 12.5).

## 11. Failure handling

| Failure | Behaviour |
|---|---|
| Kill or crash mid-step | Resume from the last indexed checkpoint; partial checkpoint directories are ignored and reported |
| Memory limit crossed | Stop, record `aborted_memory_limit`, keep the last checkpoint |
| Non-finite loss | Stop, record `failed` with the step; no automatic learning-rate retry |
| Configuration or dataset mismatch on resume | Refuse with a typed error |
| Stage below its baseline | Run completes; status `failing_baseline`; the result is still reported (clauses 8.2, 9.2, 10.2) |
| Disk full | Checkpoint write fails before rename; previous checkpoint remains valid |
