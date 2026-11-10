# Store, retriever, selector and assembler contract

Status: proposed contract, not implementation evidence. Requirements and tasks are in [spec.kvx](spec.kvx). This document owns requirements 7, 8, 9 and 10. Every model name, size, hyperparameter and threshold below is a **proposal**; none is a result. Measured facts exist only in [baseline.md](baseline.md). Cases TRM-011, TRM-018, TRM-019 and TRM-020 are UNRUN.

## 1. Boundary and shared types

The four components exchange only the requirement 1 types frozen by task 0.2: `MemoryItem`, `Query`, `Candidate`, `AssembledContext`, `Provenance`. No component reads a domain-specific field (clause 1.1, decision d_1); kind, creation time and source are the only structured signals a stage may use. Token counts for every budget come from the declared tokenizer interface (clause 1.4); the proposed default is the reader's tokenizer.

| Stage | Input | Output | Owner task | Non-learned baseline (d_2) |
|---|---|---|---|---|
| Store and candidate generation | `Query` | up to K `Candidate` with BM25, dense and fused scores, recency feature | 2.2 | is the baseline |
| Retriever | `Query`, store | up to K = 100 `Candidate` (proposal) | 4.1 | BM25; untuned same encoder; hybrid |
| Selector | `Query`, up to N_max = 50 `Candidate` (proposal) | per-item inclusion probability, no-evidence score, selected subset | 4.2 | untuned off-the-shelf reranker (baseline and fallback); a pointwise reranker trained on the same data is the comparison of clause 9.2 |
| Assembler | `Query` with budget, selected `Candidate` | `AssembledContext` | 4.3 | greedy top-k truncation |

Labels come from [labeling.md](labeling.md) (clauses 6.3, 6.6), data and splits from [data.md](data.md), the loop from [training.md](training.md), metrics and the meaning of "beats" from [evaluation.md](evaluation.md), runtime composition from [serving.md](serving.md).

## 2. Memory store and candidate generation (requirement 7)

### 2.1 Operations and scope isolation (clause 7.1)

Proposed layout: one SQLite database for items and index metadata, one BM25 index and one vector index (FAISS or hnswlib class) per scope partition.

| Operation | Semantics |
|---|---|
| `add(key, item)` | Stores the item under a stable logical key. Idempotent when the key already holds the same content-derived identity (clause 1.3). Schedules index insertion. |
| `supersede(key, item)` | Atomic replace of the item held under the key. The key stays the same; the content-derived identity changes with the content, and the store tombstones the old row and records which identity the new one supersedes. Task 2.2 calls this operation update. |
| `delete(key)` | Tombstone in SQLite, removal from BM25, mark-deleted in the vector index; the item is unreturnable from the moment the transaction commits. |
| `persist()` / `open(path)` | Durable on commit; `open` validates schema version, index manifest and encoder version (section 3.5). |
| `search(query, mode, k, filters)` | `mode` is `bm25`, `dense` or `hybrid`. Returns `Candidate` in descending score, ties broken by item identity. |

Clause 7.1 requires add, supersede and delete under a stable logical key. The key is unique within a scope and is not part of the item's identity. Proposal for its form: the caller supplies it, and when the caller supplies none the store uses the identity of the first content stored under it.

Scope isolation is structural, not a ranking preference: `scope` is a mandatory key, indexes are partitioned by scope, and a final post-check asserts `candidate.scope == query.scope` for every returned row and raises a typed error instead of returning. A query without a scope is rejected. Task 2.2 tests this with adversarially similar items in another scope.

### 2.2 Retrieval modes (clause 7.2)

- **BM25** over item text with a declared analyser (proposal: lowercase, Unicode word split, no stemming, code-identifier splitting as a configuration flag).
- **Dense** through an `Encoder` interface (`encode_queries`, `encode_items`, `version`, `dim`, `max_tokens`). The untuned pretrained encoder and the trained retriever are two implementations of the same interface; this is the "pluggable encoder".
- **Hybrid** by reciprocal-rank fusion over the two ranked lists, with rank starting at 1 and `k_rrf` proposed as 60:

```text
RRF(d) = sum over r in {bm25, dense} of  1 / (k_rrf + rank_r(d))      (a list that omits d contributes 0)
```

### 2.3 Index versioning, incremental builds, determinism (clause 7.3)

Each scope partition has an index manifest: store schema version, analyser version, `encoder_version`, embedding dimension, index type and parameters, build seed, item count and a digest of the ordered item identities. Adds, supersedes and deletes are applied incrementally from an operation log with a monotonically increasing sequence; a full rebuild replays the same log.

Determinism rule: for a fixed index (same manifest digest) and a fixed query, the ordered candidate list is identical across calls and processes. Proposed means: fixed build seed, single-threaded graph construction, a total tie-break order (score, then item identity), and no wall-clock input other than `query.reference_time`.

Exact versus approximate: BM25 and flat dense search are exact. Below a configured partition size `n_exact` (proposal: 50,000 vectors) dense search is flat; above it an approximate graph index is used. Task 2.2 reports, per partition-size bucket and per K in {10, 50, 100}, the recall of approximate search against exact search:

```text
ANN-recall@K = mean over queries of  |approx_topK(q) ∩ exact_topK(q)| / K
```

The report is mandatory whenever an approximate index is configured; an acceptance floor is deferred (see [evaluation.md](evaluation.md) section 11). Incremental and full builds are required to agree under exact search (task 2.2, case TRM-011): identical results on BM25 and flat dense search. Graph indexes depend on insertion order, so on the approximate path the two builds are compared only through the reported ANN-recall.

### 2.4 Filters and recency (clause 7.4)

Filters: the optional `created_at` range and `kind` set carried in `Query.filters` (clauses 1.2 and 7.4), applied before scoring on exact paths and as pre-filter or over-fetch-then-filter on approximate paths, with the same result contract. Default temporal rule (proposal): items with `created_at > query.reference_time` are excluded so a decision point never sees its own future. Recency is exposed on every `Candidate` as the feature `recency_seconds = max(0, reference_time − created_at)`, not folded into the score. A stage that wants a compressed scale applies its own transform (proposal: `log(1 + recency_seconds)`).

Failure modes: scope leak (blocked by the post-check), stale index after a crash between SQLite commit and index write (recovered by replaying the operation log from the manifest sequence), tombstone growth in graph indexes (compaction is a rebuild), encoder mismatch (section 3.5).

## 3. Trained retriever (requirement 8)

### 3.1 Architecture proposal (clause 8.1)

A bi-encoder: one shared encoder `f` for queries and items, pooled and L2-normalised, initialised from a configurable pretrained checkpoint. Candidates: Qwen3-Embedding-0.6B (proposed default), EmbeddingGemma, a ModernBERT-base encoder. The choice is fixed by the baseline report of task 2.5, which has an untuned-dense row for each candidate backbone, and is reviewed in task 6.4 (decision d_8). Queries and items may carry distinct instruction prefixes; the prefix is part of the encoder version.

### 3.2 Objective (clause 8.1)

For query `q` with positives `P(q)` (members of the minimal sufficient set or gold evidence), mined hard negatives `H(q)` (clause 6.6, task 3.2), in-batch items `B(q)`, similarity `s(q,d) = f(q)·f(d) / τ`:

```text
N(q)   = H(q) ∪ { d in B(q) : d not labelled evidence for q }            (false-negative mask)
L_nce  = − (1/|P(q)|) Σ_{p in P(q)}  log [ exp s(q,p) / ( exp s(q,p) + Σ_{n in N(q)} exp s(q,n) ) ]
p_T(d) = softmax_{d in C(q)}( u(q,d) / τ_T )        p_S(d) = softmax_{d in C(q)}( s(q,d) )
L_kd   = KL( p_T ‖ p_S )                             over the labelled candidate list C(q)
L_ret  = L_nce + λ · L_kd
```

`u(q,d) ∈ [0,1]` is the graded utility score per item that clause 6.3 requires, derived from the sufficiency and leave-one-out outcomes as defined in [labeling.md](labeling.md) section 3.6; only candidates that carry a score enter `L_kd`. Proposed starting values: τ = 0.05, τ_T = 1.0, λ = 1.0, 7 hard negatives per query. Gradient accumulation does not enlarge the in-batch negative pool; gradient caching is a proposed option when the pool is too small.

### 3.3 Labels consumed and reporting rule (clause 8.2)

Consumes: minimal sufficient sets, per-item necessity and graded utility scores (6.3), hard negatives (6.6), gold evidence on generated data (4.1). Weak lexical labels are never positives (decision d_3).

Every retriever result is reported per domain on held-out Recall@K against BM25 and the **untuned same encoder**, at a declared K and by a declared confidence-interval rule (clause 8.2). Hybrid is shown beside them (clause 12.3) but is not part of the failing rule. **Failing rule:** failing is per domain. A retriever that does not beat both BM25 and the untuned same encoder in a domain is recorded as failing for that domain in the run record and the report; it is never omitted, re-labelled or averaged away. The values of K and of the interval rule are declared in configuration (decision d_8); the proposals (K = 10 for the decision, with 50 and 100 also reported, and a paired bootstrap interval) are in [evaluation.md](evaluation.md) section 11.

### 3.4 Long-item policy (clause 8.4)

Proposal: `policy = "chunk"`. An item longer than `max_tokens` under the encoder's tokenizer is split into windows of `max_tokens` with a declared stride; each chunk is indexed with a pointer to its parent identity and character span; the item score is the maximum chunk score and the winning span is carried on the `Candidate` as `matched_span`. Alternative declared policies: `truncate_head`, `truncate_head_tail`. The run record stores policy name, `max_tokens`, stride, the encoder tokenizer identity and the fraction of items affected. The policy is part of `encoder_version`, so changing it invalidates indexes. Encoder-limit counting uses the encoder's own tokenizer and is read here as not being a budget computation under clause 1.4 (open question Q6).

### 3.5 Encoder-version refusal (clause 8.3)

```text
encoder_version = sha256( backbone id and revision ‖ weights digest ‖ pooling ‖ normalisation ‖
                          max_tokens ‖ long-item policy ‖ query and item prefixes ‖ dim )
```

The refusal is implemented by the store in task 2.2, which records the encoder version in the index, and is proven again with trained weights in task 4.1 (case TRM-018). Exported embeddings and every index manifest carry `encoder_version` and the model version. `store.open` and `store.search(mode in {dense, hybrid})` compare the manifest value with the loaded encoder and raise a typed `EncoderVersionMismatch` on any difference. There is no silent re-embedding and no best-effort search; BM25 remains available. Re-indexing is an explicit command.

### 3.6 Configurations and failure modes

| Item | Tiny smoke (CPU, task 5.3) | B200 sketch (task 6.3) — all proposals |
|---|---|---|
| Backbone | a ~20–30M-parameter encoder, checkpoint fixed in task 4.1 | the backbone fixed in task 2.5 (proposal: Qwen3-Embedding-0.6B) |
| `max_tokens` | 128 | 512 |
| Micro-batch × accumulation | 16 × 1 | 256 × 4, bf16 autocast |
| Hard negatives per query | 2 | 7 |
| Steps | a few hundred on the fixture slice | bounded by the test-run wall-clock budget ([remote-execution.md](remote-execution.md)) |

Failure modes: false negatives in batch (masked by labels, residual risk for unlabelled items), teacher-label noise (audited under clause 6.5), collapse onto lexical overlap (watched through the BM25 comparison), domain imbalance (per-domain sampling weights, clause 5.4), representation drift that invalidates an index (blocked by 3.5).

## 4. Set-wise selector (requirement 9)

### 4.1 Architecture proposal (clauses 9.1, 9.3)

1. Each pair `(q, c_i)` is encoded by a shared cross-encoder (ModernBERT-large or Qwen3-Reranker-0.6B class) into a vector `h_i`; per-item features (retrieval scores, recency, kind, token length) are projected and added.
2. A small set transformer (proposal: 2–4 layers) attends over `{h_∅, h_1, …, h_n}` with **no positional encoding**; `h_∅` is a learned no-evidence token. Self-attention across candidates is what lets one candidate's score depend on the others (clause 9.1).
3. Heads: `p_i = σ(w·z_i)` per item, `p_∅ = σ(w_∅·z_∅)` for no evidence.

Variable candidate count `n ≤ N_max` (proposal 50) is handled by padding with an attention mask. When more than `N_max` candidates arrive, the cut is by retrieval score then item identity, never by arrival position.

Decoding: if `p_∅ ≥ θ_∅` return the empty set with the abstention flag; otherwise return `{ i : p_i ≥ θ }`. `θ` and `θ_∅` are tuned on the validation split and recorded in the bundle.

### 4.2 Objective and comparison (clause 9.2)

Labels: the minimal sufficient set `S*(q) ⊆ C(q)` with `y_i = 1[c_i ∈ S*]`; `S* = ∅` for unanswerable queries (clause 4.1) and for candidate sets the teacher judged insufficient.

```text
L_set  = − Σ_i [ y_i log p_i + (1 − y_i) log(1 − p_i) ]                    (likelihood of exactly S*)
L_f1   = 1 − ( 2 Σ_i p_i y_i ) / ( Σ_i p_i + Σ_i y_i + ε )                  (soft set F1; skipped when S* = ∅)
L_∅    = − [ a log p_∅ + (1 − a) log(1 − p_∅) ],     a = 1[S* = ∅]
L_sel  = L_set + β · L_f1 + α · L_∅
```

`L_f1` does not decompose over items and is the set-level term; `L_set` under joint encoding is the likelihood of the labelled set. Candidate order is shuffled every step (task 4.2).

Two references stand beside the selector. The **untuned off-the-shelf reranker** is the stage's non-learned baseline and its serving fallback (decision d_2, clauses 12.3 and 13.4): a pretrained pointwise reranker with no training on Trina data, keeping the top candidates by its score; it cannot abstain. Proposal: a Qwen3-Reranker-0.6B class checkpoint, chosen from the candidate rerankers that task 2.5 reports. The trained comparison model is a **pointwise reranker** — the same cross-encoder, no set transformer, per-pair binary cross-entropy on the same examples, same thresholds procedure, abstaining when `max_i p_i < θ`. It is reported beside the selector on evidence precision, recall, F1 and exact sufficient-set match in every selector report (clauses 9.2, 12.1). **Failing rule** (task 6.4): a selector that does not beat the pointwise reranker, or does not beat the untuned reranker when the stage is replaced by its baseline, is recorded as failing.

### 4.3 Order invariance and its tolerance (clause 9.3)

The architecture is permutation-equivariant in exact arithmetic; residual differences come from floating-point summation order, padding and mixed precision. The check, for random permutations π of each candidate set:

```text
δ_p   = max over queries, items, π of  | p_i(C) − p_π(i)(π(C)) |
δ_set = fraction of (query, π) pairs whose decoded selection differs
```

The declared tolerances on `δ_p` and `δ_set` are **deferred numbers**: task 4.2 measures them on the tiny configuration and declares them in configuration; the B200 run re-measures under bf16. No value is asserted here.

### 4.4 Abstention (clause 9.4)

The selector can return an empty set through the no-evidence head. Abstention precision and recall are measured on every evaluation set that contains unanswerable queries, with the definitions in [evaluation.md](evaluation.md) section 2. An abstaining selector produces an `AssembledContext` with zero items and `abstained = true`; the assembler does not override it.

### 4.5 Configurations and failure modes

| Item | Tiny smoke | B200 sketch — proposals |
|---|---|---|
| Cross-encoder | the tiny encoder used as a cross-encoder | the base model fixed in task 2.5 (proposal: ModernBERT-large or Qwen3-Reranker-0.6B class) |
| Candidates per query | 8 | 50 |
| Pair length | 128 tokens | 512 tokens |
| Set transformer | 1 layer | 2–4 layers |
| Batch | 4 queries | 16–32 queries × 50 pairs, bf16, accumulation as needed |

Failure modes: over-abstention or never abstaining (threshold reported with both precision and recall), train/serve candidate shift (first-round labels cover baseline candidates, so task 6.3 runs a second labelling round on the trained retriever's candidates before the selector and assembler are trained), more than one valid minimal set while the label records one (exact match under-counts; F1 reported beside it), cost linear in candidates (reported under clause 12.6).

## 5. Budgeted context assembler (requirement 10)

### 5.1 Learned utility and selection (clause 10.2)

A learned utility head `û_i = g(x_i) ∈ [0,1]`, proposed as a small MLP over the candidate's stage scores (each with a presence mask so the head works when an earlier stage is degraded), recency, kind and token length `t_i`. Trained on teacher utility:

```text
L_asm = Σ_i BCE( û_i , u(q,c_i) )  +  η Σ_{(i,j): u_i > u_j} max( 0, m − (û_i − û_j) )
```

Selection is budgeted greedy by utility per token with a redundancy penalty, for budget `B` and chosen set `A`:

```text
gain(i | A) = û_i − γ · max_{j in A} sim(c_i, c_j)            (sim: embedding cosine; lexical overlap when no encoder)
pick  i* = argmax over i not in A with cost(A ∪ {i}) ≤ B of  gain(i | A) / t_i ;   stop when no gain is positive
```

followed by the standard guard that compares the greedy set with the single best item that fits. It is reported against **greedy top-k truncation** (items by previous-stage score until the budget is reached) at every budget; an assembler that does not beat it is recorded as failing (task 6.4).

### 5.2 Hard budget invariant (clause 10.1)

The budget is not a training target; it is a post-check on the rendered output. After ordering and rendering (separators and truncation marks included), the declared tokenizer counts the whole context. While `count > B`, the lowest-gain item is removed or truncated and the context is re-rendered. The loop terminates because the empty context satisfies any `B ≥ 0`. `AssembledContext.token_count` is the value from this final count, and the invariant `token_count ≤ query.token_budget` holds in every mode, including degraded modes. Task 4.3 property-tests it over random candidates and budgets.

### 5.3 Ordering, provenance, truncation (clause 10.3)

Declared ordering policies, each evaluated: `chronological` (by `created_at`, proposed default), `relevance` (by utility), `learned`. The learned policy needs order-sensitive supervision that clause 6.3 does not produce (open question Q8); until that exists it is reported as not trained.

Every item in the context carries `Provenance`: source item identity, each stage score and any truncation with its source span (clause 1.2). Truncation is extractive only in this version: a contiguous span of the source text is kept, a visible marker is inserted (its tokens count toward the budget), and provenance records `truncated = true` and the source span `[start, end)` in Unicode code points of the canonical item text. Compression (rewriting) is not proposed; adding it would need a contract revision that marks it and lists the source spans it draws on (clause 10.3).

### 5.4 Budget sweep, configurations, failure modes (clause 10.4)

Declared sweep (proposal): 512, 1,024, 2,048, 4,096 and 8,192 tokens. Quality is reported at every point for the learned assembler and for top-k truncation, per domain.

| Item | Tiny smoke | B200 sketch — proposals |
|---|---|---|
| Utility head | 2-layer MLP, hidden 64 | 2–3 layers, hidden 256–512 |
| Budgets exercised | 512 and 1,024 | full sweep |
| Ordering policies | chronological, relevance | all declared |

Failure modes: tokenizer mismatch between assembler and reader (blocked by the bundle check in [serving.md](serving.md)), greedy knapsack pathologies (guard above), redundancy penalty dropping complementary near-duplicates such as a fact and its later update (knowledge-update category reported separately), truncation cutting the evidence span (truncated-evidence rate reported).

## 6. Open questions raised against spec.kvx

| Id | Clause or task | Question |
|---|---|---|
| Q6 | 1.4, 8.4 | Encoder-limit counts necessarily use the encoder tokenizer, not the declared budget tokenizer. This document reads clause 1.4 as covering budget computations only. |
| Q8 | 6.3, 10.3 | A learned ordering policy has no label source. |
