# Trina design

Status: design draft. The [specification](spec.kvx) is normative; all product verification is UNRUN. Model names, sizes and numeric settings in this document are proposals recorded in [decisions.md](decisions.md), not results. The only measured facts are in [baseline.md](baseline.md).

## 1. Product and responsibility boundaries

Trina is a Python package that trains and serves three learned stages: a retriever that proposes candidate memories, a set-wise selector that chooses the memories that jointly answer a query, and an assembler that orders and fits them into a token budget. A non-learned baseline for each stage exists first and is reported beside every learned result (decision d_2): BM25, untuned dense and hybrid fusion for retrieval, an untuned off-the-shelf reranker for selection, top-k truncation for assembly. Trina does not answer questions: the reader that consumes the assembled context is outside the product and appears only as the teacher, reader and judge used for labelling and evaluation.

| Authority | Owns | Does not establish |
|---|---|---|
| Source bucket | Raw coding-agent call logs, read-only to Trina | A training record: nothing is usable until it is redacted and derived |
| Data plane ([data.md](data.md)) | Streaming ingestion, episodes, generated histories, public adapters, dataset versions, splits and manifests | Label quality or model quality |
| Security layer ([security.md](security.md)) | Secret loading, redaction, residual-secret audit, host allow-list, hygiene gate | That a dataset is safe to publish; that is an operator judgement |
| Labelling plane ([labeling.md](labeling.md)) | Teacher client, cache, spend cap, sufficiency and leave-one-out labels, label audit, hard negatives | Ground truth: labels are one teacher's judgement and are audited, not assumed |
| Memory store and stages ([models.md](models.md)) | Scoped storage, BM25, dense and hybrid candidates, retriever, selector, assembler | An answer; only the context handed to a reader |
| Training plane ([training.md](training.md)) | Resolved configuration, run records, the shared loop, checkpoints, resume, memory bounds | A quality claim: that comes only from evaluation |
| Evaluation ([evaluation.md](evaluation.md)) | Metrics, baselines, reader and judge protocol, judge validation, ablations, reports | Release: that needs the B200 report and operator acceptance (clause 15.5) |
| Pipeline and serving ([serving.md](serving.md)) | The one query-to-context interface, model bundle, command line and local HTTP | Multi-tenant hosting, authentication or remote exposure; the service binds to localhost |
| Remote execution ([remote-execution.md](remote-execution.md)) | Bootstrap, sync, preflight, detached launch and retrieval on the GPU host | Anything about the host beyond the recorded host facts |
| Hyperfide fleet ([workflow](../workflow.kvx)) | Task claims, worktrees, merges, gates and `done` | Product behaviour; a merged branch is not evidence |

The target is general memory (decision d_1). The bucket supplies the coding-agent domain only, so every dataset, label set and report carries a domain tag and results are reported per domain.

## 2. Governing invariants

1. **Budget.** An assembled context never exceeds the query's token budget under the declared tokenizer, on the learned path and on every fallback path. The check runs on the rendered context after assembly, not on a sum of per-item counts (clause 10.1).
2. **One tokenizer.** Every budget computation in training, evaluation and serving calls the single count function of section 4.4 (clause 1.4). A bundle names its tokenizer and the pipeline refuses a mismatch (clause 13.3).
3. **Scope isolation.** A query never returns an item outside its scope, in the store, in hard-negative mining and in every stage (clauses 7.1 and 6.6; task 3.2).
4. **Raw data is never persisted.** Bucket objects are read by streaming through a client that exposes list and streamed get only. No raw object reaches the repository or persistent local storage, and nothing is written or deleted under the source prefixes (clause 2.1, decision d_5).
5. **Redaction precedes persistence and egress.** A record is redacted before it is written, and only redacted content is sent to an external model, to an allow-listed host (clauses 3.2 and 3.4).
6. **Credentials stay in the environment and the two ignored files.** They do not appear in tracked files, logs, run records, configurations, datasets, error messages or command lines (clauses 3.1 and 14.3).
7. **Evaluation-only sets never train.** The shared training-loader entry point refuses them and the dataset manifest is checked for their absence (clauses 4.4 and 12.5, decision d_6).
8. **No group spans two splits.** Sessions, personas and source documents are split as groups; bucket sessions keep the `sha1(session_id) mod 100` rule (clause 5.1).
9. **One pipeline.** Evaluation calls the same pipeline interface that the command line and HTTP service call (clause 13.1). There is no evaluation-only retrieval path.
10. **No required domain-specific field.** A coding episode and a conversational session are built through the same constructor (clause 1.1).
11. **Baselines beside every result, and no aggregate alone.** A learned stage that does not beat its baseline in a domain is recorded as failing for that domain, not omitted (clauses 8.2, 12.3 and 12.4).
12. **One resolved configuration per run, one code path on both hosts.** Every run writes a run record; nothing depends on an external service (clauses 11.1, 11.3 and 11.6).
13. **Teacher spend passes through the teacher client and its hard cap.** Each request's worst-case cost is reserved against a bounded output length, at prices from the configured price table, so the cap is never crossed (clause 6.2; workflow principle `spend`).
14. **Skipped is not passed.** A test that could not reach the network, the bucket, the teacher or the GPU reports SKIPPED with a reason and is counted separately. A task whose acceptance needs that dependency names it with `--require` in its `verify_cmd`, and the unmet dependency is then a failure (clause 15.1, decision d_9).

## 3. Planes

**Data plane.** The diagram source is [diagrams/architecture.mmd](diagrams/architecture.mmd). Three sources converge on one contract. Bucket ingestion (tasks 1.1 and 1.2) streams objects against a recorded listing snapshot, redacts, collapses each session to its segments, removes retries and cross-source duplicates, and emits ordered memory items and decision points. The generator (task 2.1) writes multi-session general-memory histories with known gold evidence in six categories. Public adapters (task 1.4) convert retrieval sets and memory benchmarks, marking the benchmarks evaluation-only. Assembly (task 2.4) produces an immutable dataset version with group splits, leakage checks and a manifest. The production dataset version is built from the full bucket snapshot, the generator and the public sets by task 4.4, after the operator has answered its Ask for the spend cap and the target size. Detail: [data.md](data.md).

**Labelling plane.** The teacher client (task 1.3) is the only route to a provider. Utility labelling (task 3.1) asks the teacher to answer with a candidate set, judges sufficiency, and prunes by leave-one-out to a minimal sufficient set; each item also receives a graded utility score derived from these outcomes (clause 6.3). Hard negatives (task 3.2) come from baseline rankings with evidence excluded. The audit (task 3.3) measures labels against gold evidence and against repeated labelling. Task 4.4 runs the production label campaign under the operator's cap, and task 6.3 runs a second labelling round on the trained retriever's candidates so that the selector and assembler train on the candidates they will serve. Detail: [labeling.md](labeling.md).

**Training plane.** One loop (task 3.4) serves all three stages: device selection, mixed precision, gradient accumulation, streaming loaders, atomic checkpoints, resume and a measured host-memory limit. Stage trainers (tasks 4.1 to 4.3) supply the model, loss and batch construction. Detail: [training.md](training.md) and [models.md](models.md).

**Inference pipeline.** Store, retriever, selector and assembler behind one interface, loaded from a versioned bundle (task 5.1), exposed by the command line and a local HTTP service (task 5.2), and called by the evaluation harness of task 2.5, which task 5.1 switches to this interface. Per-stage latency is measured inside the pipeline. Detail: [serving.md](serving.md) and [evaluation.md](evaluation.md).

## 4. The contract of requirement 1

This section is the working contract for task 0.2. Field names and encodings are proposals (P13 in [decisions.md](decisions.md)); the obligations are those of clauses 1.1 to 1.4. All types live in `src/trina/schema/` and carry `schema_version`, an integer that starts at 1.

### 4.1 Types

| Type | Field | Meaning |
|---|---|---|
| `MemoryItem` | `id` | Content-derived identity, section 4.2 |
| | `scope` | Opaque non-empty string compared by exact equality; no prefix semantics in version 1 |
| | `kind` | Open vocabulary string. Core values proposed: `user_message`, `assistant_message`, `tool_call`, `tool_output`, `file_content`, `note`, `fact`, `preference`, `event`, `document`. A stage may use `kind` as a feature and must accept an unknown value |
| | `text` | The memory content, Unicode, already redacted when persisted |
| | `created_at` | UTC instant |
| | `source` | `origin` (for example `bucket`, `synth`, a public set name) and `ref` (an opaque reference inside that origin: session and position, persona and session, document identifier) |
| | `metadata` | Free string-keyed map. No stage may require a key. Excluded from `id` |
| `Query` | `id`, `text`, `scope`, `reference_time`, `token_budget` | Clause 1.2. `reference_time` is the instant the query is asked; items created after it are never candidates |
| | `filters` | The optional time and kind filters of clauses 1.2 and 7.4: `created_after`, `created_before`, `kinds` |
| | `metadata` | A free map that carries the domain and category tags in datasets |
| `Candidate` | `item` | The `MemoryItem` |
| | `scores` | Optional per-stage scores: `bm25`, `dense`, `fused`, `retriever`, `reranker` (the untuned reranker, when it stands in for the selector), `selector`, `utility` |
| | `features` | `token_count`, `recency_seconds` (reference time minus creation time, never negative), `rank` per producing stage, and an optional `matched_span` when a chunk of a long item produced the score |
| `Provenance` | `item_id`, `source` | Names the source item (clause 1.2) |
| | `scores`, `position`, `token_count` | Each stage score that led to inclusion, the place in the context and the cost under the declared tokenizer |
| | `truncated`, `source_span` | Whether the item text was shortened, and the start and end offsets in the source text that the emitted text maps to (clauses 1.2 and 10.3) |
| `AssembledContext` | `query_id`, `items` | Ordered entries, each the emitted text with its `Provenance` |
| | `token_count`, `token_budget`, `tokenizer` | Count of the rendered context, the budget it was held to, and the tokenizer that counted it |
| | `abstained` | The abstention flag of clause 1.2: true when the selector returned no evidence; `items` is then empty |
| | `degraded` | The degraded flag of clause 1.2: the list of stage names (`retriever`, `selector`, `assembler`) that fell back to their baseline; empty on the full learned path (clause 13.4) |
| | `bundle_version`, `ordering_policy` | What produced the context |

The store's stable logical key (clause 7.1) is not a `MemoryItem` field: it is the key under which the store adds, supersedes and deletes an item, and it stays the same while the content-derived `id` changes with the content. See [models.md](models.md) section 2.1.

### 4.2 Canonical serialization and identity

- The canonical form is UTF-8 JSON with object keys sorted by code point, no insignificant whitespace, no duplicate keys, text normalised to NFC, and instants written as UTC RFC 3339 with exactly six fractional digits and a `Z` suffix.
- `MemoryItem.id` is `sha256:` followed by the lowercase hexadecimal digest of `trina\0memory-item\0v<schema_version>\0` plus the canonical form of `scope`, `kind`, `text`, `created_at` and `source`. `Query.id` is derived the same way from `text`, `scope`, `reference_time`, `token_budget` and `filters`. Identical content therefore gives an identical identity across runs and hosts (clause 1.3).
- Scores are floating point and never enter an identity. They are serialized in shortest round-trip form and covered by golden vectors.
- Decoding is strict. An unknown `schema_version` raises `SchemaVersionError`; a missing required field, wrong type, unknown top-level field, invalid instant or an `id` that does not match its content raises `MalformedRecordError`. Both are subclasses of one `ContractError`.
- Golden vectors under `tests/fixtures/schema/` fix the bytes for each type. Case TRM-001 reproduces them byte for byte.

### 4.3 Dataset records

Training and evaluation examples are compositions of the contract types, not new shapes: a `Query`, the scope's `MemoryItem` list (or a reference to it), and labels keyed by item identity. Gold evidence, teacher labels and weak lexical labels are separate named fields, so a weak label can never be read as gold (task 1.2). The record formats are owned by [data.md](data.md) and [labeling.md](labeling.md).

### 4.4 Tokenizer interface

```text
Tokenizer.id                      -> name and version, recorded in runs and bundles
Tokenizer.count(text)             -> int
Tokenizer.truncate(text, limit)   -> (text, source_span)
count_tokens(text, tokenizer)     -> int      # the single function every budget computation calls
```

The context budget is checked with `count_tokens` on the rendered context, including any separators and per-item framing. The default implementation is a configuration choice (P12). Encoder tokenizers used inside a model for its own input limit are not budget tokenizers and are never used for clause 10.1.

## 5. One query from memory store to assembled context

1. **Request.** A caller submits a `Query` in process, by command line or over HTTP. Strict decoding rejects a malformed or unknown-version request with a typed error.
2. **Bundle.** The pipeline holds a loaded bundle of retriever, selector, assembler, tokenizer and configuration. An inconsistent bundle was refused at load (clause 13.3). A missing learned stage is replaced by its declared baseline (hybrid fusion, the untuned off-the-shelf reranker, or top-k truncation) and its name is added to `degraded` (clause 13.4).
3. **Scope and filters.** The store restricts the search to `Query.scope`, to items created at or before `reference_time`, and to the optional time and kind filters. The scope restriction is applied inside each index lookup, not as a filter over a global result.
4. **Candidate generation.** BM25 and dense search run with the bundle's retriever encoder; the store refuses an index built by a different encoder version (clause 8.3, implemented in task 2.2). The two rankings are fused by reciprocal-rank fusion into a candidate list (proposed 100). Each `Candidate` carries its scores, rank, token count and recency.
5. **Set-wise selection.** The top candidates (proposed up to 50) and the query are scored jointly. The selector returns an inclusion probability per item and a no-evidence score. If no evidence is indicated, the pipeline returns an `AssembledContext` with no items and `abstained` true (clause 9.4).
6. **Budgeted assembly.** The assembler scores utility, then adds items greedily by utility per token with a redundancy penalty until the next item would exceed the budget. An item may be truncated only under a declared policy, marked and mapped to its source span.
7. **Ordering.** The declared policy (chronological, by relevance, or learned) fixes the final order; `ordering_policy` records it.
8. **Budget check.** The rendered context is counted with `count_tokens`. If it exceeds the budget, items are removed from the lowest utility upward until it fits. This step is unconditional and also guards the fallback paths.
9. **Response.** The `AssembledContext` carries ordered items, token count, abstention flag, degraded stages and per-item provenance with the source item, each stage score and any truncation with its source span. Per-stage latency is recorded for the evaluation report (clause 12.6).

## 6. One training example from source to gradient step

From a bucket object:

1. Task 1.1 records a listing snapshot, then streams one call object. Client headers are dropped entirely, IP addresses and installation identifiers are dropped and secret patterns replaced before anything is written; redaction counts are recorded by kind. The residual-secret audit runs on the written shards. A failed object goes to the ledger with a reason.
2. Because every call carries the full session history, the session collapses to its segments, a segment being a maximal run of calls in which each call's history extends the previous one (clause 2.2); exact retries and cross-source duplicates are removed and counted.
3. Task 1.2 turns a segment into ordered `MemoryItem`s by kind and time, all in one scope for the session, and into decision points: at a model step the `Query` is the step's situation and the memory is every earlier item. The bucket's lexical reference labels travel as weak labels.
4. Task 2.4 assigns the session to a split by `sha1(session_id) mod 100`, with the residue-to-split mapping taken from the existing v2 session index and verified against it in task 1.1, tags the example `coding_agent`, checks leakage of queries and whole episodes, and writes it into a dataset version.

From a generated history, task 2.1 samples persona, topic and style with recorded seeds, writes evidence and filler sessions through the same process, plants evidence and writes a question with gold evidence identities. For an answerable question the teacher must answer correctly from the gold evidence alone and fail on the history with that evidence removed; for an unanswerable question it must abstain on the full history. A question that fails is dropped and counted (clause 4.5). The persona is the split group. Both paths then continue:

5. Task 2.2 builds a scoped store over the example's memory and the baseline retrieves candidates for the query.
6. Task 3.1 adds known evidence to the baseline candidates, asks the teacher to answer with the set, judges sufficiency, and prunes by leave-one-out to a minimal sufficient set. Each item receives a necessity label and a graded utility score; the label records teacher identity, reasoning effort, prompt version and judge outcome. A bucket decision point has no gold answer, so its reference is the recorded next action; a query whose reference the teacher cannot reproduce from the full history is excluded and counted. Weak labels and gold evidence order the pruning so calls are spent where the outcome is uncertain.
7. Task 3.2 mines hard negatives from a declared rank band of the baseline rankings in the same scope. Evidence is excluded at mining time and again when training batches are built, so labels produced after mining still take effect.
8. The streaming loader of task 3.4 reads shards within the configured host-memory limit and builds stage batches. The retriever sees the query, positives from the minimal set, in-batch and hard negatives, and teacher scores for distillation. The selector sees the query with a shuffled candidate set, inclusion targets and the no-evidence target. The assembler sees candidates, utility targets and a sampled budget. In the B200 run the selector and assembler examples come from the second labelling round over the trained retriever's candidates (task 6.3).
9. One gradient step runs under mixed precision with gradient accumulation; metrics are appended as JSONL to the run record and checkpoints are written atomically.

Evaluation-only benchmark items cannot enter at step 4 or step 8: assembly fails the build and the loader raises.

## 7. Scale and failure handling

The measured starting point is in [baseline.md](baseline.md): 642,028 objects and 171.8 GB in the bucket, a 226 KB median call object, a 64-CPU build host with no GPU, and a GPU host with 183,359 MiB of GPU memory against 59 GB of RAM. Nothing about Trina's own throughput, cost or quality has been measured. Two consequences are designed for now: ingestion parallelises over objects with bounded memory per worker and resumable shards, and data loading on the GPU host streams and stays under a measured limit (decision d_7).

| Failure | Behaviour |
|---|---|
| Object read or parse error | Recorded in the failed-object ledger with a reason; the build continues and reports the count (clause 2.4) |
| Interrupted ingestion or labelling | Resumes from completed shards or cached responses to the same result; a partial label set is valid and usable (clauses 2.4 and 6.4) |
| Teacher rate limit or transient error | Bounded retries with backoff; a repeated request is served from the cache at no cost (clause 6.1) |
| Spend reaches the cap | The campaign stops before the cap is exceeded and leaves resumable state (clause 6.2) |
| Unlisted host or unredacted content | The call is refused before any bytes leave (clause 3.4) |
| Residual secret, cross-split group, near-duplicate or benchmark item in a build | The build fails; no dataset version is published (clauses 3.3 and 5.2) |
| Training process killed | Resume from the last atomic checkpoint; metrics agree with an uninterrupted run within the declared tolerance (clause 11.2) |
| Host memory limit exceeded | The run stops with a typed error rather than swapping or being killed by the host (clause 11.4) |
| Encoder and index versions differ, or inconsistent bundle | The store refuses the index; the pipeline refuses the bundle at load (clauses 8.3 and 13.3) |
| Learned stage unavailable | Baseline for that stage, response marked degraded, budget invariant still enforced (clause 13.4) |
| Preflight failure or dropped connection on the GPU host | Launch is refused; a detached run continues and its status, logs and artifacts remain retrievable (clauses 14.4 and 14.2) |
| External dependency unreachable in a test | SKIPPED with a reason, never a pass (clause 15.1) |

## 8. Proposed repository layout

The layout follows the `touches` of the tasks in [spec.kvx](spec.kvx). `data/` and `runs/` are ignored by git and by Hyperfide; task 0.1 adds them to `.gitignore` and `.hyperfideignore`.

```text
pyproject.toml  uv.lock  .python-version  Makefile        task 0.1
src/trina/
  cli.py                                                  0.1, entry point
  schema/      contract types, canonical codec, tokenizer 0.2
  config/      layered resolution, digest                 0.3
  runs/        run records, JSONL metrics                 0.3
  security/    secrets, redaction, audit, scanner         0.4
  data/        bucket/ episodes/ public/ synth/ assemble/ negatives/    1.1 1.2 1.4 2.1 2.4 3.2
  teacher/     client/ labels/ audit/                     1.3 3.1 3.3
  store/       items, BM25, dense, fusion                 2.2
  eval/        metrics/ harness/ ablation/                2.3 2.5 5.1 6.4
  train/       core/ retriever/ selector/ assembler/      3.4 4.1 4.2 4.3
  models/      retriever/ selector/ assembler/            4.1 4.2 4.3
  pipeline/    query-to-context interface, bundle         5.1
  serve/       command line and local HTTP                5.2
  remote/      bootstrap/ run/                            6.1 6.2
configs/       base/ data/ teacher/ store/ eval/ labels/ train/ campaign/ smoke/ b200/
tests/         conftest.py, cases/test_<wave>_<n>.py, fixtures/
scripts/       check_hygiene.py, record_evidence.py, campaign.py, smoke.py, remote_bootstrap.sh
docs/          usage guide, data card, model card         7.1
data/  runs/   ignored: derived datasets, caches, run records, checkpoints
```

Each task pairs its code with `tests/cases/test_<wave>_<n>.py`, which is its `verify_cmd`. No implementation status is inferred from this document.
