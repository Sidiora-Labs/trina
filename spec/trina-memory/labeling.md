# Teacher access and utility labels

Status: proposed contract, not implementation evidence. Requirements and tasks are in [spec.kvx](spec.kvx).

This chapter owns requirement 6. Supervision is utility for the reader, not lexical overlap (decision d_3): a teacher answers with and without candidate memories, and a minimal sufficient set is mined by leave-one-out pruning. The method follows the minimal-sufficient-set idea of Context-Picker (a generator–judge loop with greedy leave-one-out pruning); the labels feed set selection in the manner of SetR and Dynamic Passage Selector ([models.md](models.md)). No price, rate limit or label-quality figure exists yet; the only measured teacher facts are in [baseline.md](baseline.md) section 3.

## 1. Teacher client (clause 6.1, task 1.3)

| Aspect | Contract |
|---|---|
| Endpoint | The direct provider API, host taken from the allow-list in configuration ([security.md](security.md) section 6). Not a gateway: baseline.md measured 2,496 prompt tokens through the gateway against 13 direct for the same one-line prompt |
| No injected prompt | The request body contains exactly the messages rendered from a versioned prompt template. The live test of task 1.3 compares provider-reported prompt tokens with the local count |
| Model and effort | `teacher.model` and `teacher.reasoning_effort` in configuration; proposal `gpt-6.1-sol` (decision d_4). The `MODEL` variable holds the gateway spelling and is not read by this client |
| Roles | Teacher, reader and judge are three configured roles of this one client, and the identity of each is recorded (clauses 6.3 and 12.2); proposal: the same model for all three, each with its own prompt and effort. Whether the judge should be a different model is an open operator question ([decisions.md](decisions.md) Q6) |
| Credential | `OPENAI_API_KEY` through the secrets loader, as an opaque value |
| Output bound | Every request sets a maximum output length; the cap of clause 6.2 reserves against it (section 2.3) |

**Canonical request digest and cache.** The digest is sha256 over the canonical serialization of: API path, model, reasoning effort, messages, sampling parameters, output bound, response format, prompt version and a `replicate` index. It excludes credentials, headers, timestamps and request ids. The cache is content-addressed under the ignored `data/`, append-only, and holds redacted content only. Only complete, successful responses are stored; errors and truncated responses are not. A hit returns the stored response, costs nothing and is recorded with `cached = true`. The `replicate` index exists so that repeated labelling for votes and for the self-agreement audit is a new request instead of a cache hit.

**Rate limits, retries, concurrency.** Requests and tokens per minute are token buckets set in configuration; a provider retry-after is honoured. Retries are bounded (proposal: 5 attempts, exponential backoff with jitter) and apply to rate-limit responses, server errors and timeouts only; other client errors fail at once. Concurrency is a configured bound across queries; within one query the procedure is sequential, because each step depends on the last. A request to a host that is not on the allow-list, or with content that fails the redaction check, is refused before any network activity (clause 3.4).

## 2. Spend accounting, estimate and cap (clause 6.2)

Every call writes a spend record: digest, role, model, effort, prompt version, input, cached-input, output and reasoning tokens as reported by the provider, cost, latency, attempts, `cached`. Cost is tokens times the price table in configuration (clause 6.2). baseline.md contains no price, so the operator supplies the table and a campaign refuses to start without one. The cap itself is also the operator's: task 4.4 raises an Ask for it, and it is never defaulted ([decisions.md](decisions.md) Q5).

### 2.1 Cost formula

One **evaluation** is one reader call plus at most one judge call (the judge is skipped when the reader abstains or fails).

| Symbol | Meaning |
|---|---|
| `N` | Candidates for the query (section 3.1) |
| `w` | Size of the shortlist: gold evidence, weak labels and natural signals among the candidates |
| `m` | Size of the minimal sufficient set found |
| `v` | Votes per decision-critical evaluation (section 3.5) |
| `t_q`, `t_f` | Tokens of the query and of the fixed prompt |
| `t_i` | Tokens of item `i` after the declared truncation |
| `p_in`, `p_out` | Configured prices per input and output token |
| `o_r`, `o_j`, `t_j` | Output tokens of reader and judge; input tokens of a judge call |

Evaluations per query, `E`:

- Gate: 2 (closed-book and full set), plus 1 for a query with no gold answer (the full-history reproduction test of section 3.3).
- Shortlist-first pruning, when the shortlist alone is sufficient: 1 + `w` (one test of the shortlist, then one removal attempt per member).
- Sequential pruning, when it is not: `N` (one removal attempt per candidate).
- Confirmation pass over the result, only when an item was removed after another was kept: at most `m`.

So `E_best = 3 + w` and `E_worst = 3 + N + m` for a query with a gold answer, one more of each for a query without, and calls per query lie between `E` and `2E`. With votes, each evaluation that keeps an item or decides the gate is repeated up to `v` times, which adds at most `(v − 1)(2 + m)` evaluations. Full leave-one-out over all candidates (section 3.4, optional) adds `N`.

Cost of one evaluation over context set `S`: `p_in · (t_f + t_q + Σ_{i∈S} t_i) + p_out · o_r + p_in · t_j + p_out · o_j`. Cost of a query is the sum over its evaluations; cost of a campaign is the sum over queries, less cache hits. The term `Σ t_i` dominates: the gate's full-set evaluation costs about `N` items, and each pruning step costs the items still kept. Shortlist-first pruning therefore saves both calls and tokens.

### 2.2 Dry-run estimate

Before a campaign starts it prints an estimate and writes it to the run record. Input tokens of the gate evaluations are counted exactly by the declared tokenizer on the prompts that would be sent. `w` is known per query; `m`, output tokens and the share of queries that fall back to sequential pruning are priors in configuration. The estimate is a low, expected and high figure, and the high figure uses `E_worst`. Proposal: a campaign begins with a small pilot, after which the priors are replaced by the pilot's observed ratios and the estimate is printed again. Task 3.1 compares estimate with actual spend on the fake teacher.

### 2.3 Hard cap

The cap is enforced by reserving each request's worst-case cost against its bounded output length, as clause 6.2 requires. Before a request is sent, the client reserves its worst case: counted input tokens with a configured margin, plus the output bound, at the configured prices. If spent plus all in-flight reservations plus the new one would exceed the cap, the request is refused, in-flight requests finish, and the campaign stops with status `cap_reached` and a valid partial label set (section 6). On response the reservation is replaced by actual cost. Spend therefore stops before the cap and never crosses it, at the price of stopping slightly early. Whether the provider's output bound also bounds reasoning tokens is checked by the live test of task 1.3; until then the margin must cover it.

## 3. Labelling procedure (clause 6.3, task 3.1)

### 3.1 Candidate set

For each query: the top candidates of baseline retrieval in the query's scope (BM25, untuned dense and their fusion from task 2.2), plus every known gold evidence item, plus weak-labelled and natural-signal items ([data.md](data.md) section 3), deduplicated by item identity. Proposal: `N` is at most 50, the selector's maximum; the retriever's 100 candidates are used for negatives (section 7), not for pruning. Items are truncated by the declared long-item policy and presented in a seeded shuffled order so position carries no information. The candidate set, its order and its sources are recorded.

### 3.2 Correctness

The reader is told to answer from the given context only and to abstain when the context does not hold the answer. The judge compares the reader's output with the expected output and returns `correct`, `incorrect` or `abstained`.

| Data | Expected output |
|---|---|
| Generated and public | The gold answer; for unanswerable questions, abstention |
| Bucket decision point, or any query with no gold answer | The recorded next action (`reference`), as clause 6.3 requires. The query counts only if the teacher reproduces it from the full history (section 3.3) |

### 3.3 Sufficiency gate

1. Reproduction test, only for a query with no gold answer: the teacher is given the full history (every earlier item in the scope) and must reproduce the recorded next action. If it cannot, the query is recorded as `not_reproducible`, excluded from the label set and counted (clause 6.3). Proposal: a history too long for the reader's context window cannot be tested and is excluded and counted under its own reason.
2. Closed-book evaluation with no memory. If correct on an answerable query, record `memory_not_needed = true`, minimal set empty, and stop; proposal: such queries are excluded from selector positives.
3. Full-set evaluation over the candidate set. If incorrect, record `sufficient = false` and stop: no pruning is possible. On generated data this means a reader failure or a bad question; on bucket data it means the candidate set missed something the history held. Both are counted.
4. Otherwise `sufficient = true` and pruning begins.

### 3.4 Pruning to a minimal sufficient set

1. Order candidates by prior likelihood of being needed, least likely first: items with no weak label or signal, then weak-labelled items, then natural-signal items, then gold evidence. This ordering only saves calls; it never decides a label.
2. Shortlist test: evaluate the shortlist alone. If sufficient, every other candidate is recorded `necessary = false` in one evaluation.
3. Greedy leave-one-out: for each remaining item in order, evaluate the kept set without it. Still correct: drop it, `necessary = false`. No longer correct: keep it, `necessary = true`. Each result records the context set it was measured against.
4. Confirmation: if any item was dropped after another was kept, re-test each kept item against the final set, because necessity can change as the set shrinks.
5. The kept set is the minimal sufficient set: sufficient, and no single item can be removed. It is minimal in that sense, not the smallest possible set, and it depends on the order; the order is recorded.

Necessity is therefore leave-one-out relative to a recorded set. Leave-one-out of every candidate against the full set is an optional mode (`loo_scope = "full"`), off by default because it costs `N` evaluations and mostly measures redundancy.

### 3.5 Unanswerable queries, disagreement and judge failure

- **Unanswerable.** Gold says there is no answer, or both gate evaluations abstain. The label is `answerable = false` with an empty minimal set and no pruning. An abstention with the full set on a query whose gold says answerable is `sufficient = false`, not unanswerable.
- **Disagreement.** The teacher is not deterministic. Gate evaluations and any evaluation that would keep an item are repeated up to `v` times with distinct `replicate` indices and decided by majority (proposal: `v` = 3, starting with one and adding votes only when the outcome keeps an item). The tally is recorded; a split vote sets `unstable = true` on the item, and a split gate sets the query's status to `unstable`. Proposal: unstable queries are kept in the label set and excluded from training by default.
- **Judge failure.** An unparseable judge output is re-asked once with a new replicate. If it fails again, `judge_outcome = "error"`, the evaluation counts as neither correct nor incorrect, the item under test is kept with `necessary = "unknown"`, and the query's status is `partial`.
- **Reader failure.** A refusal, empty output or truncated output is recorded as `reader_outcome` and treated like a judge failure. No failure is ever converted into a label.

### 3.6 Graded utility score

Clause 6.3 requires a graded utility score per item, derived from the sufficiency and leave-one-out outcomes. It is the `u(q, d)` that the retriever distils and the assembler regresses ([models.md](models.md) sections 3.2 and 5.1). The field is required; the mapping below is a proposal.

| Outcome for the item | `u` |
|---|---|
| Kept in the minimal sufficient set | The share of its removal evaluations in which the answer was no longer correct: 1 when every vote failed without it, lower when votes were split |
| Removed by leave-one-out with the answer still correct | The same share, which is 0 when no vote failed without it |
| Discarded together by the shortlist test | 0 |
| `necessary = "unknown"`, or the candidate set was not sufficient | No score; the item is masked out of every loss that uses `u` |

A query judged unanswerable gives every candidate a score of 0.

## 4. Prompt versioning

Reader, judge and generator prompts are template files under `configs/teacher/`. A prompt version is the template's name, declared version and the sha256 of its bytes; changing the bytes without changing the declared version fails a check. The version is part of the request digest, so a changed prompt never reuses old cache entries, and it is recorded on every label (decision d_4). A label set is produced under one labelling configuration digest (prompts, model, effort, `N`, votes, ordering); labels under different digests are separate label sets and are not merged without a declared mapping.

## 5. Label record

One record per query, self-contained, redacted, in zstd-compressed JSONL shards under the ignored `data/` (proposal).

| Field | Content |
|---|---|
| `label_id`, `schema_version` | Content-derived identity |
| `query_id`, `dataset_version`, `domain`, `category` | The example labelled |
| `label_config_digest` | Section 4 |
| `teacher`, `reader`, `judge` | Model and reasoning effort of each role, and the endpoint host class (clauses 6.3 and 12.2) |
| `prompt_versions` | Reader and judge |
| `candidates[]` | Item identity, source (`bm25`, `dense`, `hybrid`, `gold`, `weak`, `signal`), baseline rank and score, presented position |
| `closed_book`, `full_set` | Outcome and votes of each gate evaluation |
| `sufficient`, `answerable`, `memory_not_needed` | Section 3.3 and 3.5 |
| `necessity[]` | Per item: `true`, `false` or `unknown`; the context set it was measured against; votes; `unstable` |
| `utility[]` | Per item: the graded utility score of section 3.6, or absent when masked (clause 6.3) |
| `minimal_set[]`, `prune_order[]` | Result of section 3.4 and the order used |
| `judge_outcomes[]` | Per evaluation: `correct`, `incorrect`, `abstained`, `error` (clause 6.3) |
| `status` | `complete`, `partial`, `unstable`. A `not_reproducible` query has no label record; it is listed and counted in the label-set manifest |
| `spend` | Calls, cached calls, tokens and cost for this query |
| `request_digests[]` | Links to cache entries and spend records |

## 6. Resumability and idempotence (clause 6.4)

- The unit of work is `(query_id, label_config_digest)`. Queries are processed in a deterministic order.
- A record is appended only when its query has finished, followed by a flush; a torn final line is detected by its checksum and discarded on load. Every prefix of the output is therefore a valid label set, and its manifest says `status = partial` with counts by status.
- On resume, queries with a record are skipped. A query interrupted part-way is replayed from its start: its earlier calls are cache hits, so replay costs nothing and reaches the same state. Running a finished campaign again produces no calls and no new records (case TRM-014).
- Spend records are keyed by request digest and replicate, so a replayed call is not counted twice.
- A finished label set has an identity over its sorted `label_id` values and its configuration digest, and names its dataset version. The label audit report names that identity.

## 7. Hard-negative mining (clause 6.6, task 3.2)

- Source: BM25 and dense baseline rankings within the query's scope. Negatives are sampled from a declared rank band (the band is configuration; its value is a proposal to be tuned against false-negative rate) with a fixed seed, so mining is deterministic.
- Excluded: gold evidence; members of the teacher's minimal set; items with `necessary = true` or `unknown`; exact and near-duplicates of any of those. Proposal: weak-labelled and natural-signal items are also excluded unless a teacher label marks them unnecessary, because their status is unknown.
- Items the teacher dropped during pruning are recorded as verified negatives; rank-band items the teacher never saw are unverified negatives. The record keeps the distinction, with the source ranker, rank and score.
- Task 3.2 does not depend on task 3.1, so teacher labels may not exist when negatives are mined. Task 3.2 therefore applies the exclusion twice: at mining time against whatever labels exist, and again when training batches are built, against the label set named in the run configuration. Labels produced after mining, including those of the second round of section 10, still take effect, and a negative that a later label marks as evidence is never served.

## 8. Label audit (clause 6.5, task 3.3)

| Measurement | Data | Reported |
|---|---|---|
| Against gold evidence | Generated data, where evidence is known | Per-item precision and recall of `necessary = true` against gold; exact match of the minimal set; rates of subset and superset |
| Self-agreement | A sample labelled again with new replicate indices | Per-item agreement, raw and chance-corrected; overlap of minimal sets; agreement on `sufficient` |
| Descriptive only | Bucket data, which has no gold | Self-agreement, and overlap with weak labels. Overlap with weak labels is not accuracy: baseline.md records that those labels agreed with themselves at 76% across versions |

All figures are reported per domain and category with bootstrap intervals and sample sizes, in a report bound to the label set identity. A teacher set that differs from gold is not always wrong: gold may contain a redundant item, or another sufficient set may exist; the report samples such cases for inspection. Thresholds for accepting a label set are not fixed here. They are proposed in [decisions.md](decisions.md) and fixed by measurement (decision d_8); until then the audit reports and does not gate.

## 9. Known biases

- **Single teacher.** One model produces the labels, reads and judges (proposal). Its blind spots become the labels, and a judge of the same family may favour its own answers. Judge validation with intentionally wrong answers is in [evaluation.md](evaluation.md) (clause 12.2).
- **Labels show what this reader needed.** A minimal sufficient set is minimal for this reader at this reasoning effort and prompt. A weaker reader may need more, a stronger one less. Changing the reader means a new label set.
- **Parametric knowledge.** Items the teacher can do without because it already knows the answer are labelled unnecessary. The closed-book gate catches whole queries, not single items.
- **Same model on both sides of the bucket.** By baseline.md, 162.91 GB of the call data is the teacher model's own output, so on that domain the teacher is predicting its own recorded behaviour.
- **Candidate ceiling.** An item outside the candidate set can never be labelled necessary, so first-round labels inherit the recall of baseline retrieval. The second round of section 10 relabels on the trained retriever's candidates.
- **Order dependence.** Greedy pruning returns one minimal set of possibly several.

These are stated in the data card and model card (clause 15.4), with the licence position recorded in [data.md](data.md) section 9.

## 10. Production campaign and second labelling round (tasks 4.4 and 6.3)

Task 3.1 qualifies the labeller on a fake teacher. Two later tasks spend real money with it, both through the teacher client and one cap.

| Round | Task | Candidates | What it produces |
|---|---|---|---|
| Production campaign | 4.4 | Baseline retrieval plus known evidence (section 3.1) over the production dataset version | The label-set version and hard negatives that the B200 run trains the retriever on, with the label audit of section 8, the residual-secret audit and a spend record |
| Second round | 6.3 | The trained retriever's candidates | Labels for the selector and assembler, so they train on the candidates they will serve |

Task 4.4 starts by raising an Ask for the spend cap and the target dataset size and prints the dry-run estimate of section 2.2 before any paid call. The second round runs under the same campaign cap. Proposal: it uses the same procedure and prompts and is a separate label set with its own identity, because its candidate source differs. A rerun of either round resumes from the cache without new spend (section 6, case TRM-028).
