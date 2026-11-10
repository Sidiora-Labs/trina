# Trina memory retrieval and context assembly

Status: **specification authored; no implementation exists; all 28 qualification cases are UNRUN**.

Trina (Transformer Indexed Activation) is a training system for a memory-retrieval and context-assembly neural network. It produces three learned stages and the pipeline that serves them: a retriever that proposes candidate memories, a set-wise selector that chooses the memories that jointly answer a query (including none), and an assembler that orders them and fits them into a token budget. A non-learned baseline exists first and is reported beside every learned result.

Training data combines redacted coding-agent trajectories streamed from a source bucket, generated general-memory histories with known evidence, and public retrieval sets. A teacher model supplies utility labels: which memories the reader actually needed. The system is built and smoke-tested on a CPU host and given a test run on one NVIDIA B200. The starting environment is described in [baseline.md](baseline.md); nothing else has been measured.

## Read in this order

| Document | Purpose |
|---|---|
| [Specification](spec.kvx) | Normative requirements, acceptance clauses, tasks, dependencies and UNRUN qualification cases. |
| [Baseline](baseline.md) | The measured facts about the workspace, bucket, teacher access and GPU host. |
| [Design](design.body.md) | Boundaries, invariants, the contract of requirement 1, one query and one training example end to end. |
| [Decisions](decisions.md) | Fixed constraints, proposals, decisions deferred to measurement, open questions. |
| [Data](data.md) | Ingestion, episodes, generated histories, public sets, splits and manifests (requirements 2, 4, 5). |
| [Labelling](labeling.md) | Teacher client, utility labels, audit and hard negatives (requirement 6). |
| [Security](security.md) | Credentials, redaction, residual-secret audit and egress control (requirement 3). |
| [Models](models.md) | Memory store, retriever, selector and assembler (requirements 7 to 10). |
| [Training](training.md) | Configuration, run records, the training loop, resume and memory bounds (requirement 11). |
| [Evaluation](evaluation.md) | Metrics, baselines, reader and judge, ablations and reports (requirement 12). |
| [Serving](serving.md) | Pipeline interface, model bundle, command line and local HTTP (requirement 13). |
| [Remote execution](remote-execution.md) | Bootstrap, sync, preflight and the B200 run (requirement 14). |
| [Implementation plan](implementation-plan.md) | Generated task order and ownership. |
| [Traceability](traceability.md) | Generated links from clauses to tasks, cases and documents. |
| [Qualification](qualification.kvx) | Evidence log. Holds authoring-time observations only. |
| [Authoring validation](authoring-validation.json) | Result of the last validator run over this pack. |
| [Validator](tools/validate_spec.py) | The specification validator and renderer. |
| [Architecture diagram](diagrams/architecture.mmd) | Planes and stages as a Mermaid flowchart. |

## Authority and evidence

`spec.kvx` is the normative requirements and task catalogue. The contract documents refine it; a contradiction between them is a defect to resolve, not an invitation to choose the easier reading. `implementation-plan.md` and `traceability.md` are generated views and are never edited by hand. Model names, sizes and thresholds in any document are proposals: base models are fixed by measurement in task 2.5 and reviewed in task 6.4, and each tolerance or threshold named in a clause is declared in configuration by the task that first needs it and listed in [decisions.md](decisions.md) (decision d_8).

The specification holds 15 requirements, 72 acceptance clauses, 30 tasks in waves 0 to 7 and 28 qualification cases.

Run the authoring check from the repository root:

```sh
python3 spec/trina-memory/tools/validate_spec.py
```

Regenerate the two derived views after changing clauses, task mappings or cases:

```sh
python3 spec/trina-memory/tools/validate_spec.py --render
```

The check parses the specification, validates references and dependency order, checks that every clause is covered by a task and a case, checks local document links, and detects stale derived views (clause 15.3). It does not execute any product test. All 28 cases, TRM-001 to TRM-028, remain UNRUN.

## Workflow, evidence and documentation (requirement 15)

| Clause | How it is met |
|---|---|
| 15.1 | Each task's `verify_cmd` is `uv run pytest tests/cases/test_<wave>_<n>.py -q` and exercises real code paths: real pipelines against local fixtures or local fake endpoints, never mocked-out logic. Tests that need an external dependency carry one of four markers, `network`, `bucket`, `teacher`, `gpu` (task 0.1). An unmet marker reports SKIPPED with a reason. `make test` prints the skipped count separately and never adds it to the pass count. A task whose acceptance needs a live dependency adds `--require <dependency>` to its `verify_cmd`: with it, an unmet named dependency is a failure, so the task cannot be done on skips. Eleven tasks do so: 1.1 (`bucket`), 1.3, 2.1, 2.5 and 3.3 (`teacher`), 1.4 (`network`), 4.4 (`bucket,teacher`) and 6.1 to 6.4 (`gpu`). A case whose oracle depends on a skipped test stays UNRUN or is recorded SKIPPED. |
| 15.2 | Evidence goes into [qualification.kvx](qualification.kvx), one entry per execution, with five fields: code revision, command, exit code, log path and observation. Entries are appended by `scripts/record_evidence.py` (task 0.1), which the main supervisor runs when a wave lands; task workers do not edit the file, except that release closure (task 7.2) collects the final reports into it. An entry without a log cannot mark a case passed (task 7.2, case TRM-026). Authoring-time observations are kept apart as `[observation.*]` entries and prove nothing about the product. |
| 15.3 | The validator above. Task 0.1 runs it inside `tests/cases/test_0_1.py`, including the stale-view check, so a drifted plan fails the first gate. |
| 15.4 | Task 7.1 writes the project README, a usage guide, a data card and a model card under `docs/`. Each states three limits: labels come from a single teacher and carry its bias; the source data is coding-heavy (one model through one agent accounts for most of the bucket); and the licence position on provider-derived data is the operator's decision. Every figure in the cards must match a run record, and every result that failed its baseline is listed. |
| 15.5 | Release closes only with the end-to-end report retrieved from the B200 run (task 6.3), the full evaluation and ablations run on the GPU host (task 6.4), and the operator's acceptance of the running system (task 7.2). Passing component tests does not close the release. |

## How the fleet runs

Work follows [spec/workflow.kvx](../workflow.kvx) in parallel mode. The main agent owns the task list, supervises and merges, and does not implement tasks. A wave manager owns the branch `wave/trina-memory/<wave>`; a task worker owns one task on `task/trina-memory/<task>` in an isolated worktree under `.codegraph/worktrees`.

- Start a run with `hyperfide fleet up -f trina-memory`; observe it with `hyperfide fleet status` and `hyperfide fleet tree`.
- A worker follows the loop: `hyperfide brief`, `hyperfide spec next`, `hyperfide spec start <id>`, implement inside the task's `touches`, `hyperfide review` and `hyperfide guard`, commit explicit paths tagged `[spec:trina-memory/<id>]`, then `hyperfide spec done <id>`, which reruns `verify_cmd`.
- Qualified tasks merge into their wave with `hyperfide fleet merge-up <id>`; a wave lands on `main` with `hyperfide fleet land <wave>` behind `make test` and `make lint` (pytest, ruff, mypy, the hygiene check and the artifact scanner). At each landing the main supervisor records the wave's evidence in `qualification.kvx`.
- Limits: at most 4 tasks in progress and 6 agents, each with a one-hour lease. The agent driver is `claude`. Pull requests are off because no git remote exists; checkpoints are manual.
- Waves run in order: 0 foundation, 1 sources and teacher access, 2 data, baseline retrieval and evaluation, 3 labels and training core, 4 the three stages and the production dataset and label campaign, 5 pipeline and CPU smoke run, 6 the GPU host, 7 documentation and release.
- A worker never defaults a secret, variable, permission or decision it was not given. It raises an Ask: secrets, variables and permissions block; decisions and questions proceed and are flagged. Task 4.4 raises an Ask for the campaign spend cap and the target dataset size before any paid call; neither is defaulted.
- Credentials stay in the two ignored files, raw bucket data is never persisted, and all teacher spend goes through the teacher client and its cap.
