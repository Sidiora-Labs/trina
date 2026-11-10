# Data sources, ingestion, generation and dataset assembly

Status: proposed contract, not implementation evidence. Requirements and tasks are in [spec.kvx](spec.kvx).

This chapter owns requirements 2, 4 and 5. Every number below is either quoted from [baseline.md](baseline.md) or marked as an estimate or a proposal. Redaction and the external-call boundary are in [security.md](security.md); teacher calls and labels are in [labeling.md](labeling.md); metrics are in [evaluation.md](evaluation.md).

## 1. Sources and domains

| Source | Domain tag (proposal) | Trainable | Gold evidence | Built by |
|---|---|---|---|---|
| Bucket trajectories (`datasets/v2`, then raw call objects) | `coding_agent` | Yes | None; weak labels and natural signals only | Tasks 1.1, 1.2 |
| Generated general-memory histories | `general_memory` | Yes, plus a held-out split | Known by construction | Task 2.1 |
| Public retrieval sets | `public:<set>` | Only when the registry licence allows it | As published | Task 1.4 |
| Public memory benchmarks (LongMemEval, LoCoMo, ConvoMem, MemoryAgentBench) | `benchmark:<set>` | Never (decision d_6) | As published | Task 1.4 |

The target is general memory (decision d_1). The bucket is one domain of it, and by baseline.md it is dominated by one model through one agent, so nothing learned from it alone is reported as general. Which public retrieval sets are trainable is an open operator question ([decisions.md](decisions.md) Q7); task 1.4 registers each with its licence, and a set whose licence is unknown is registered evaluation-only.

## 2. Bucket contract

### 2.1 Access (clause 2.1)

- The only client is a wrapper exposing `list` and streamed `get` (task 1.1). It has no put, copy, delete, multipart or lifecycle method, so a write under a source prefix cannot be expressed through it. Endpoint, bucket and keys come from the `BUCKET_`-prefixed variables through the secrets loader.
- Source prefixes, all read-only: `datasets/`, `9router/calls/`, `openrouter-traces/`. `work/` holds another pipeline's intermediates and is not read. Trina writes nothing to the bucket in this specification; derived data stays under the local ignored `data/`.
- Read order: `datasets/v2/trajectories/` first, because it is already collapsed to segments and small; then `9router/calls/`; then `openrouter-traces/`. The order is also the precedence order for cross-source duplicates (section 2.4).
- `datasets/v2` is treated as untrusted input like a raw object. Its own README says the redaction is pattern-based and should be reviewed, so every row is re-redacted by Trina and its `.zst` files are never copied.

"No raw object persisted" means, operationally:

1. The GET body is consumed as a stream through decompression and parsing in process memory. No spooling to a temporary file, no on-disk HTTP cache, no `s5cmd cp`, no download-then-read.
2. Redaction runs before the first byte of a record reaches a writer. Client headers are dropped entirely, with no allow-list of kept fields (clause 3.2, [security.md](security.md) section 4). Writers accept only the redacted record type, so an unredacted record is a type error and not a convention.
3. Ledgers, logs, progress output and exception messages carry object key, ETag, size and reason codes only, never body content. Core dumps are disabled for workers.
4. An object whose decompressed size exceeds the configured per-worker bound is skipped with reason `oversize`; it is never spilled to disk.
5. Case TRM-005 plants canary strings in fixture objects and scans the working tree, `data/`, `runs/` and the temporary directory after the run. Host swap is outside this check and is stated as a limit.

### 2.2 Listing snapshot (clause 2.3)

A build starts by listing the source prefixes once and freezing the result. Objects that appear later belong to a later snapshot.

| Manifest field | Content |
|---|---|
| `snapshot_id` | sha256 of the canonical listing digest and prefix set |
| `taken_at`, `prefixes` | Listing time and the prefixes listed |
| `object_count`, `byte_total` | Per prefix and in total (clause 2.3) |
| `latest_object` | Key and last-modified time of the newest object (clause 2.3) |
| `listing_digest` | The digest of the listing that clause 2.3 requires. Proposed form: sha256 over sorted `(key, size, etag)` lines, so a replaced object changes it |
| `pipeline_code_hash` | Hash of the ingestion package and its resolved configuration (clause 2.3) |
| `listing_shards` | Checksummed shards of the listing itself (keys and sizes, no content) |

A rebuild reads only keys in the snapshot and fails an object whose ETag or size no longer matches (`changed_since_snapshot`). Record identities are content-derived under the requirement 1 contract and never include wall-clock time, worker id or shard position, so a rebuild over one snapshot with one code hash yields identical identities.

### 2.3 Collapse and segments (clause 2.2)

By baseline.md every call carries its full session history. Clause 2.2 therefore collapses each session to its segments and defines the term: a **segment** is a maximal run of calls in which each call's history extends the previous one. A history that is not an extension (compaction, rewrite, restart) starts a new segment. The remaining rules are proposals:

- "Extends" is tested as a prefix relation on the history. The record kept for a segment is its longest call; earlier calls are skipped as `superseded`.
- Collapse is two-pass. Pass A streams every call object and keeps only an index row: session key, history length, digest of the history prefix chain, key, ETag. Pass B streams only each segment's terminal call and extracts it. Pass A's index contains digests and identifiers, not content.
- Whether the session identifier is available without reading the body is not known; if it is, pass A becomes a listing operation. Task 1.1 measures this.
- `run`, `agent` and `task` identifiers are null in every object (baseline.md), so the session is the only grouping available. There is no repository or user grouping.

### 2.4 Dedupe (clause 2.2)

| Kind | Rule (proposal) | Kept |
|---|---|---|
| Exact retry | Same session and same history digest and same request digest | First by key order |
| Cross-source duplicate | Same session key and same segment anchor (digest of the first items after normalising redaction placeholders and whitespace) | By precedence: `datasets/v2`, `9router/calls/`, `openrouter-traces/` |

Placeholders are normalised before comparison because `datasets/v2` was redacted by a different pipeline, so byte equality with Trina's redaction cannot be assumed. Whether v2 rows carry a session identifier usable for this is read from its README only and must be confirmed in task 1.1.

### 2.5 Ledgers, resume and progress (clauses 2.2, 2.4)

Two append-only ledgers, both keyed by object key and ETag.

| Ledger | Reasons | Effect on the build |
|---|---|---|
| Skip ledger: deliberate, final | `superseded`, `exact_retry`, `cross_source_duplicate`, `malformed`, `decompress_error`, `no_session_id`, `empty_history`, `unsupported_schema`, `oversize`, `not_a_call_object` | Counted by reason in the build report |
| Failed-object ledger: not processed | `read_error`, `timeout`, `changed_since_snapshot`, `redaction_error`, `worker_crash`, with attempt count and error class | Build is marked incomplete; a resume retries them; never silently dropped |

Accounting invariant, checked at the end of every build: listed objects = objects that produced records + skipped + failed. A build that does not balance fails.

Resume: work is partitioned into deterministic units (a fixed slice of the sorted listing). A unit's output shard is written to a temporary name and renamed on completion with its checksum recorded; a resumed build skips completed units and redoes partial ones, so an interrupted build converges to the same shards. Each worker holds at most one decompressed object plus fixed buffers, bounded by configuration; worker count is configuration. Progress reports objects, bytes, records, skips and failures per interval in the run record's JSONL format.

Task 1.1 runs the residual-secret audit on the written shards before the build is reported complete ([security.md](security.md) section 5); a high-confidence match fails the build.

## 3. Episode extraction (clause 2.5)

Task 1.2 turns a segment into an episode: ordered memory items plus decision points.

| Item kind | Origin |
|---|---|
| `user_message` | User or system-injected turn |
| `assistant_message` | Assistant text |
| `tool_call` | Assistant tool invocation, arguments as text |
| `tool_output` | Result of a tool call, linked to its call by metadata |
| `file_content` | File body returned by a read-type tool, with the path in metadata; split from its `tool_output` wrapper |

- Items keep the segment's order as an integer position and carry creation time where the trace has one; where it has none, order is authoritative and the time is marked absent, never invented.
- Kind is a value of the contract's open `kind` field. No stage may require a kind or metadata key that only this domain has (clause 1.1); path and tool name live in free metadata.
- A **decision point** exists at each model step. The query is the step's situation: the standing user instruction plus the items that arrived since the previous assistant step, capped by a configured token limit. The memory is every earlier item in the session, including earlier segments. The recorded next action is stored as the `reference`, separately from the query; it is the reference that labelling uses for a query with no gold answer (clause 6.3). The scope is the session.
- Decision points per segment are capped and position-stratified by configuration (proposal), because the count per segment has not been measured.

Labels attached at extraction are never gold:

| Field | Meaning | Status |
|---|---|---|
| `weak.lexical` | The bucket's `ctx_ref_idx` and `ctx_lookback`, or the same heuristic recomputed, with its origin | Weak. baseline.md records 76% agreement between its own versions |
| `signal.reread` | A later step reads again content that an earlier item already held | Natural signal that the earlier item was needed |
| `signal.post_compaction_ref` | After a segment boundary, an item refers by path, identifier or quoted span to an item before it | Natural signal that the earlier item was needed after it left context |
| `gold` | Absent for this domain; `gold_available = false` | Loaders asking for gold on bucket data get none, by type |

Weak labels and signals are a pre-filter and an ordering hint for labelling (decision d_3, [labeling.md](labeling.md) section 3). They are distinct types from gold evidence, and no accessor returns them under a gold name (case TRM-006).

## 4. General-memory generator

### 4.1 Sampling and seeds (clauses 4.1, 4.2)

A history is produced from a plan, then written. Persona, topic and style are drawn from versioned pools in configuration; each draw uses a seed derived as a hash of the campaign seed, the axis name and the history index, and all three seeds are stored on the record. The plan fixes the session count, session times, the facts to plant, which session holds each fact and which later session updates it. Sessions are then written by the teacher model through the teacher client, so every generation call is cached, counted against the cap and subject to the host allow-list.

### 4.2 Question categories (clause 4.1)

| Category | Definition | Example shape |
|---|---|---|
| `single_fact` | One fact stated once in one session | "What breed is my dog?" with one evidence item |
| `multi_session` | The answer needs facts from two or more sessions combined | "How many of the cities I visited this year have I also worked in?" |
| `temporal` | The answer depends on session times or the order of events | "What did I start first, the course or the new job?" |
| `knowledge_update` | A later session supersedes an earlier fact; the latest value is correct | "Where do I live now?" after a move; evidence is the update, the old value is a distractor |
| `preference` | The answer applies a stated preference rather than restating a fact | "Suggest a restaurant for tonight" given a stated diet |
| `unanswerable` | The history never contains the answer; the correct response is abstention | "What is my sister's phone number?" with related but insufficient sessions; gold evidence is empty |

### 4.3 Evidence planting and same-process filler (clause 4.3)

- Every session is written from the same template, model, settings and persona, and every session has planted facts. No session is written as filler. For a given question, "filler" means the sessions whose facts that question does not ask about.
- The position of evidence sessions in the history is sampled uniformly, and session length is drawn from one distribution regardless of role.
- Gold evidence is the identity of the items that state the planted fact, resolved after writing by locating the planted statement; a history whose planted fact cannot be located is dropped and counted.

### 4.4 Near-duplicate filter and distribution report (clause 4.2)

Questions and sessions are fingerprinted on normalised text (word-shingle MinHash; the similarity threshold is configuration, and its value is a proposal to be tuned in task 2.1). Later near-duplicates are dropped and counted. The generation report gives counts by category, by session count, by history tokens and by evidence-set size, with dropped counts by reason.

### 4.5 Teacher validation (clause 4.5)

Clause 4.5 sets one rule for answerable questions and another for unanswerable ones.

| Question | Check | Passes when |
|---|---|---|
| Answerable | (a) gold evidence alone | The teacher answers correctly, judged against the gold answer |
| Answerable | (b) the history with that evidence removed | The teacher fails. "Without it" means the full history minus the gold evidence items, not a closed-book call |
| `unanswerable` | The full history | The teacher abstains |

A question that fails a check is dropped and counted by check.

### 4.6 Discriminator check (clause 4.3)

A classifier is trained to tell evidence sessions from non-evidence sessions and must score a held-out AUC of at most 0.60, or task 2.1 fails.

- It sees the session only, never the question: overlap with the question is the legitimate retrieval signal, not a surface artefact.
- Proposed feature set, versioned in configuration: length in characters, tokens and turns; position in the history and gap to neighbouring sessions; speaker balance; ratios of digits, capitals and punctuation; counts of dates, numbers and capitalised tokens; type-token ratio; hashed word and character n-gram frequencies.
- How features are chosen: the set must cover every class of cue by which benchmark filler is said to be distinguishable from evidence (length, position, vocabulary). Features may be added; removing one needs an entry in [decisions.md](decisions.md), because a weak feature set passes trivially.
- Proposal: both a linear model and a gradient-boosted tree model are trained, the larger AUC counts, an AUC below 0.5 is reflected, the held-out split is by persona, and the report includes a bootstrap interval and the sample size.

## 5. Public sets and the registry (clause 4.4)

Each adapter converts one published format to the contract and writes one registry entry.

| Registry field | Content |
|---|---|
| `name`, `version` | Set and release |
| `licence` | Identifier and the URL or file it was read from; `unknown` is allowed and forces `evaluation_only = true` |
| `provenance` | Origin, retrieval date, citation |
| `checksum` | sha256 of every downloaded file |
| `evaluation_only` | True for all four memory benchmarks; true by default |
| `adapter_version`, `known_flaws` | Converter revision and published caveats |

The shared training-loader entry point consults the registry and raises on any set marked evaluation-only (case TRM-008); there is no override flag. Known flaws recorded as published claims, not Trina measurements: LoCoMo has answer-key errors and a lenient judge; LongMemEval-S fits in a context window and its filler may be distinguishable from evidence, which is why clause 4.3 exists. [evaluation.md](evaluation.md) handles them through judge validation and the held-out generated and bucket sets (decision d_6).

## 6. Assembly, splits and leakage

### 6.1 Splits (clause 5.1)

| Source | Group | Rule |
|---|---|---|
| Bucket | Session | `sha1(session_id) mod 100`: 90 train, 5 validation, 5 test. Which residues are train, validation and test is not assumed: task 1.1 takes split assignments from the existing `datasets/v2` session index and verifies the residue mapping against it |
| Generated | Persona | Hash of the persona identity, same proportions (proposal); the test part is the held-out generated set of decision d_6 |
| Public trainable | Source document or conversation | The published split when one exists, else a hash of the group |
| Benchmarks | Whole set | Evaluation-only; never assigned to train or validation |

Every segment, decision point and item inherits its group's split. Assembly asserts that no group identity occurs in two splits.

### 6.2 Leakage checks (clause 5.2)

- Exact: digests of normalised query text, reference or answer text and whole-episode content, compared across splits.
- Near-duplicate: the same fingerprints as section 4.4, compared across splits and against a fingerprint index of every evaluation-only set. The index holds fingerprints only.
- Any hit fails the build with a report naming both sides. There is no silent drop; an exclusion list is configuration and is recorded in the manifest.
- The unit of the check is the query and the whole episode (clause 5.2). Individual memory items in the coding domain (the same file read in sessions of different splits, shared system prompts) will repeat across splits, and no repository grouping exists; such item-level overlap does not fail the build. Proposal: it is measured and reported in the manifest.
- Task 2.4 also runs the residual-secret audit on the output ([security.md](security.md) section 5).

### 6.3 Version identity and manifest (clause 5.3)

The version identity is `sha256` over the canonical manifest core: sorted shard checksums, schema version, source hashes, code revision and the digest of the assembly configuration. The domain mix and sampling weights are not part of that configuration or of the identity (section 6.4). A version directory is built under a temporary name, renamed once, and never rewritten; a loader verifies shard checksums against the manifest and refuses a mismatch. Manifest: identity; counts by domain, category and split; source hashes (bucket `snapshot_id`, generator campaign and seed, registry checksums); code revision; resolved configuration and its digest; leakage report; residual-audit result; excluded-item counts.

### 6.4 Domain tags and sampling weights (clause 5.4)

Every example carries exactly one domain tag from section 1. The domain mix and the per-domain sampling weights (how often a loader draws from each domain) are training configuration, outside the dataset version identity (clause 5.4): they are declared in `configs/data/mix.toml` (task 2.4), resolved into each training run's configuration and recorded in its run record. Changing them does not mint a new dataset version or invalidate labels. The values are a proposal: equal weight per trainable domain unless [decisions.md](decisions.md) says otherwise, fixed by measurement in task 2.5 and reviewed in task 6.4 (decision d_8).

### 6.5 Production dataset version (task 4.4)

Tasks 1.1 to 2.4 are qualified on fixtures and small live slices. The dataset version that the B200 run trains on is built by task 4.4 from the full bucket snapshot, the generator and the public sets, with the same code. Before any paid call the task raises an Ask for the campaign spend cap and the target dataset size, neither of which is defaulted, and prints the dry-run cost estimate for the operator. It then runs utility labelling and hard-negative mining under the cap, runs the residual-secret audit and the label audit on the result, and writes the manifests, the audit reports and the spend record. A rerun resumes without new spend (case TRM-028).

## 7. Persisted artefacts

All live under the ignored `data/` as zstd-compressed JSONL or Parquet shards with a manifest (proposal). Every record is redacted and carries a schema version.

| Artefact | Key fields |
|---|---|
| Snapshot manifest | Section 2.2 |
| Session index (pass A) | `session_key`, `segment_ordinal`, `history_len`, `prefix_digest`, `object_key`, `etag`, `source` |
| Skip and failed ledgers | `object_key`, `etag`, `size`, `reason`, `attempts`, `error_class` |
| Episode | `episode_id`, `domain`, `group`, `source`, `items[]` (contract memory items with `position`, `kind`, `time`), `decision_points[]` (`query`, `position`, `reference`, `weak`, `signals`), `redaction_counts` |
| Generated history | `history_id`, `persona_seed`, `topic_seed`, `style_seed`, `plan_digest`, `sessions[]`, `questions[]` (`category`, `text`, `answer`, `gold_evidence[]`, `validation`) |
| Registry entry | Section 5 |
| Dataset example | `example_id`, `dataset_version`, `domain`, `category`, `split`, `group`, `query`, `scope`, `answer` or `reference`, `gold_evidence[]` or none, `weak`, `signals` |
| Dataset manifest | Section 6.3 |

## 8. Volume and cost drivers

Measured, from baseline.md only:

| Quantity | Value |
|---|---|
| Bucket | 642,028 objects, 171.8 GB, 18 days |
| `9router/calls/` | 629,578 objects, 167.08 GB, of which 162.91 GB is one model through one agent |
| `openrouter-traces/` | 10,400 objects, 3.71 GB |
| `datasets/v2` trajectories | 19 files of 400 rows, at most about 7,600 segments (from its README, unverified) |
| Median call object | 226 KB compressed |
| One sampled object | 3.6 times expansion to 820 KB; 82,176 prompt tokens over 57 history items |
| Build host | 64 CPUs, 251 GB RAM, 499 GB free disk |

Estimates, not measurements:

- If the single 3.6 times sample were typical, a full pass over `9router/calls/` would decompress about 600 GB in flight. One sample does not establish this.
- The same sample gives about 1,440 tokens per history item. Item length drives every downstream cost: retriever truncation, selector input and teacher tokens per labelling call.

Unknown until task 1.1 reports them: sessions, segments per session, the fraction of objects that are `superseded`, decision points per segment, and how much of the raw prefixes `datasets/v2` already covers. Cost drivers in order: pass A reads every raw byte once, so bucket egress and decompression dominate ingestion; teacher spend dominates everything after it and scales with decision points sampled, candidates per query and item length ([labeling.md](labeling.md) section 2); generation spend scales with histories times sessions. The volume to label is a budget decision, not a property of the bucket: the operator sets the target dataset size and the spend cap in answer to the Ask of task 4.4.

## 9. Licence and provenance

- baseline.md records that the `datasets/v2` README notes OpenAI's terms restrict using its outputs to develop competing models. The bucket trajectories are predominantly outputs of that provider's model, and the teacher is that provider's model.
- Whether to use provider-derived trajectories as training data and a provider model as teacher is the operator's decision (decision d_10). This document records the position and does not judge it; the model card repeats it (clause 15.4).
- Every persisted record names its source: snapshot and object key for bucket data, campaign and seeds for generated data, registry entry for public data. Public sets are used only within their recorded licence.
- Derived bucket data is redacted but not anonymised: it remains the content of real agent sessions and stays in the ignored `data/` directory and on the GPU host for the duration of a run ([remote-execution.md](remote-execution.md)).
