# Inference pipeline and serving

Status: proposed contract, not implementation evidence. Requirements and tasks are in [spec.kvx](spec.kvx). This document owns requirement 13. Paths, command names, endpoint names and field names are **proposals** to be frozen by tasks 5.1 and 5.2; nothing here is a shipped interface. Case TRM-021 is UNRUN.

## 1. Boundary

The pipeline is the single place where a `Query` becomes an `AssembledContext`. It composes the store and the three stages of [models.md](models.md), loads a model bundle, and is called identically by the command line, the local HTTP service and the evaluation harness of [evaluation.md](evaluation.md). It does not train, label, call the teacher or read the bucket.

| Clause | Subject | Section | Task |
|---|---|---|---|
| 13.1 | One interface; evaluation uses it | 2, 8 | 5.1 |
| 13.2 | Command line and local HTTP; versioned contract | 6, 7 | 5.2 |
| 13.3 | Versioned bundle; refusal when inconsistent | 4 | 5.1 |
| 13.4 | Fallback to the declared baseline; degraded mark | 5 | 5.1 |

## 2. Pipeline interface (clause 13.1)

```text
Pipeline.open(bundle_path, store_path, config) -> Pipeline        raises on an inconsistent bundle (section 4)
Pipeline.run(query: Query) -> AssembledContext                    the only query entry point
Pipeline.run_batch(queries: list[Query]) -> list[AssembledContext]   same result per query as run()
Pipeline.info() -> bundle identity, stage availability, tokenizer identity, contract versions
```

Request and response are exactly the requirement 1 types (clauses 1.1–1.3): the `Query` carries text, scope, reference time, token budget and optional time and kind filters; the `AssembledContext` carries ordered items, token count, an abstention flag (`abstained`), a degraded flag naming any stage that fell back (`degraded`), and per-item provenance with the source item, each stage score and any truncation with its source span. Field names are those of [design.body.md](design.body.md) section 4.1. Canonical serialization and typed rejection of unknown versions or malformed records apply at this boundary. Memory mutation goes through the store operations of clause 7.1 (`add`, `supersede`, `delete`), which act on a `MemoryItem` under a stable logical key ([models.md](models.md) section 2.1).

`run` is a pure function of (query, store state, bundle, configuration): no wall-clock input other than `query.reference_time`, no randomness, so the three surfaces can be compared for equality (case TRM-021).

## 3. Stage sequence

| Step | Action | Contract effect |
|---|---|---|
| 1. Validate | Parse and version-check the `Query`, including its optional time and kind filters; reject a missing scope or a negative budget | Typed error, no partial work |
| 2. Candidates | Store search within `query.scope` with filters; trained retriever encodes the query; hybrid fusion; up to K = 100 (proposal) | `Candidate` list with BM25, dense, fused scores and recency |
| 3. Select | Top `N_max` = 50 (proposal) candidates to the selector; decode with bundle thresholds | Inclusion probabilities; no-evidence score; possibly empty set |
| 4. Assemble | Utility, budgeted greedy selection, ordering policy, rendering, hard budget post-check | `AssembledContext` with `token_count ≤ budget` |
| 5. Stamp | Provenance per item; `degraded` stage names; stage timings | Response complete |

Invariants that hold in every mode, including degraded ones: no item outside `query.scope` (clause 7.1); `token_count ≤ query.token_budget` under the declared tokenizer (clauses 1.4, 10.1); every item has provenance (clause 10.3). An abstaining selector yields zero items and `abstained = true`. Per-stage timings are collected inside the pipeline (task 5.1) so that clause 12.6 measures the served path, not a copy of it.

## 4. Model bundle (clause 13.3)

Proposed layout. A bundle is an immutable directory identified by the digest of its manifest.

```text
bundle/
  manifest.json            bundle format version, component table, identities
  config.resolved.json     pipeline configuration (K, N_max, thresholds, ordering policy, budget defaults)
  retriever/               weights, encoder configuration, long-item policy
  selector/                weights, thresholds θ and θ_∅
  assembler/               utility head, redundancy and ordering parameters
  baselines/               pinned identity and digest of the untuned reranker used as the selector fallback
  tokenizer/               the declared budget tokenizer, or its pinned identity
  SHA256SUMS               digest of every file
```

`manifest.json` records per component: path, digest, model version, the training run identity, and `present: true|false`. It also records the contract schema version, `encoder_version`, tokenizer identity, dataset version, label-set version, code revision and the configuration digest.

Consistency checks at `open`; any failure is a **refusal** with a typed error naming the check, and the pipeline does not start:

| Check | Refused when |
|---|---|
| Format | Manifest missing, unparsable, or of an unknown bundle format version |
| Integrity | Any file digest differs from `SHA256SUMS` or the manifest; an unlisted file is present in a component directory |
| Contract | A component was trained against a contract schema version the runtime does not support |
| Tokenizer | Tokenizer absent, or its identity differs from the one the assembler and configuration declare (clause 1.4). There is no fallback tokenizer |
| Encoder and store | The retriever's `encoder_version` differs from the store's dense index manifest (clause 8.3); the dense path is refused, see section 5 for when this degrades rather than refuses |
| Selector shape | `N_max` exceeds the retriever K, or the selector's feature schema differs from what the retriever emits |
| Assembler | Declared ordering policy or budget tokenizer unknown to the runtime |
| Lineage | Components name different dataset versions or label-set versions without an explicit `mixed_lineage` acknowledgement in the manifest |

**Absent is not corrupt.** A component declared `present: false` is unavailable and triggers section 5. A component declared present whose files are missing or fail their digest is an inconsistent bundle and is refused; corruption is never converted into silent degradation. Task 5.1 tests both with a stage removed in turn and with a corrupted bundle.

## 5. Degraded mode (clause 13.4)

When a learned stage is unavailable the pipeline substitutes the declared baseline for that stage and marks the response.

| Unavailable stage | Declared fallback (proposal) | Notes |
|---|---|---|
| Retriever | Hybrid with the untuned encoder when the store holds an index for it; otherwise BM25 only | An index built by the trained encoder cannot be queried without it (clause 8.3) |
| Selector | The untuned off-the-shelf reranker (decision d_2, clause 12.3): the top `n_fallback` candidates by its score | Fixed by the specification; `n_fallback` and the checkpoint are proposals. Cannot abstain: `abstained = false`; the no-evidence score is absent |
| Assembler | Greedy top-k truncation by the previous stage's score | Same rendering, same hard budget post-check |
| Tokenizer, store | none | Refusal, not degradation |

Marking: the response carries the degraded flag of clause 1.2, the `degraded` list naming each stage that fell back, for example `["selector"]`; an empty list means fully learned. Which fallback a stage uses is declared in the bundle configuration, so the stage name identifies it. The learned score of a substituted stage is absent from provenance rather than fabricated; the fallback's own score is recorded under its own name. The mark is in the response body so that the three surfaces agree byte for byte; the HTTP status stays 200. Proposal: the bundle pins the untuned reranker's identity and digest, and a bundle whose selector is absent and whose pinned reranker cannot be loaded is refused under section 4.

"Unavailable" is proposed to mean: declared absent in the bundle, or disabled by configuration (which is how ablations run, clause 12.7). A stage that raises at run time is an error by default (`on_stage_error = "fail"`); `"degrade"` is an explicit option, logged and marked (open question S3). Evaluation reports state the `degraded` list of every configuration so a degraded run cannot be mistaken for the full system.

## 6. Command-line surface (clause 13.2)

Proposed commands under the `trina` entry point of task 0.1. Input and output are the canonical JSON of the contract; `--pretty` only affects whitespace.

| Command | Purpose |
|---|---|
| `trina store add|supersede|delete --store PATH [--file items.jsonl]` | Mutate the store with `MemoryItem` records under their logical keys |
| `trina store reindex --store PATH --bundle PATH` | Explicit re-embedding after an encoder change |
| `trina query --bundle PATH --store PATH [--file query.json]` | One `Query` in, one `AssembledContext` out |
| `trina bundle verify PATH` / `trina bundle inspect PATH` | Run the section 4 checks; print identities and stage availability |
| `trina serve --bundle PATH --store PATH [--host H] [--port P]` | Start the local HTTP service |

Exit codes (proposal): 0 success, including a degraded or abstaining response; 2 invalid input or unknown contract version; 3 inconsistent bundle or store refusal; 4 internal error. Errors are printed to standard error in the error shape of section 7. Credentials are never arguments; the pipeline needs none.

## 7. Local HTTP surface (clause 13.2)

Proposed namespace `/v1`. The path version names the HTTP surface; every body also carries the contract `schema_version`, and the two are checked independently.

| Method and path | Request body | Response body |
|---|---|---|
| `POST /v1/context` | `Query` | `AssembledContext` |
| `POST /v1/memories` | one or more `MemoryItem`, each with its logical key | keys and identities added, counts |
| `PUT /v1/memories/{key}` | `MemoryItem` | the key, the new identity and the superseded one |
| `DELETE /v1/memories/{key}?scope=…` | none | confirmation |
| `GET /v1/info` | none | bundle identity, stage availability, tokenizer, versions |
| `GET /v1/health` | none | liveness and readiness (bundle and store loaded) |

**Binding.** The service binds to the loopback interface by default. Binding elsewhere requires an explicit flag and prints a warning, because this version has no authentication or transport security; it is a local interface, not a network service. Scope is a required field on every request and is the only isolation between tenants of one store.

**Limits.** Request size, batch size and budget have configured maxima; exceeding one is a typed error, not truncation.

**Error shape.** Every non-2xx response is:

```text
{ "schema_version": "<contract version>", "error": { "code": "<TYPED_CODE>", "message": "<redacted text>", "details": { … } } }
```

| Status | Codes |
|---|---|
| 400 | `MALFORMED_RECORD`, `MISSING_SCOPE`, `INVALID_BUDGET`, `LIMIT_EXCEEDED` |
| 404 | `UNKNOWN_ITEM` (never reveals whether the key exists in another scope) |
| 409 | `ENCODER_VERSION_MISMATCH`, `STORE_BUSY` |
| 422 | `UNSUPPORTED_SCHEMA_VERSION` |
| 500 | `INTERNAL` |
| 503 | `NOT_READY`, `BUNDLE_INCONSISTENT` |

Messages pass through the redacting logger of task 0.4 and never echo memory text. Task 5.2 starts the real service, ingests memories, queries over HTTP and the command line, and checks both against the in-process pipeline.

## 8. Evaluation uses the same interface (clause 13.1)

- The harness accepts a `Pipeline` and calls `run` or `run_batch`; it has no other route to an assembled context. Task 5.1 switches the harness of task 2.5 to this interface.
- Baselines and ablations are pipeline configurations with stages substituted through the same mechanism as section 5, not separate scripts. The full-context and oracle-evidence rows are harness-supplied stage implementations behind the same interface, since they need information a served pipeline does not have.
- A reported number therefore describes code that serving runs. A metric computed from a stage called outside the pipeline is a stage-level training metric and is labelled as such.
- The report records the bundle digest, store manifest digest, configuration digest and `degraded` list for every row.

## 9. Operational notes and failure modes

| Situation | Behaviour |
|---|---|
| Store written while serving | Single writer; readers see committed state; a mutation is visible to the next query after commit |
| Encoder changed without re-indexing | Dense path refused with `ENCODER_VERSION_MISMATCH`; BM25 fallback only if the retriever is declared unavailable |
| Budget smaller than any single item | Truncation with marks if the policy allows, otherwise an empty context with `abstained = false` and zero items |
| Query scope with no items | Empty context; not an error |
| Device missing for a bundle trained on GPU | Same code path on CPU ([training.md](training.md) section 6); slower, not degraded |

## 10. Open questions raised against spec.kvx

| Id | Clause | Question |
|---|---|---|
| S3 | 13.4 | "Unavailable" does not say whether a run-time stage failure degrades or errors. |
| S4 | 13.2, task 5.2 | Task 5.2 ingests memories over HTTP, but no clause requires mutation endpoints or defines their request shape beyond the store operations of clause 7.1. |
