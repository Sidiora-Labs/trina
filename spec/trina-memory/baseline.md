# Measured baseline

Status: observations taken on 2026-11-10 while authoring the specification. These are facts about the starting environment, not product evidence. Requirements and tasks are in [spec.kvx](spec.kvx).

## 1. Workspace

| Item | Observation |
|---|---|
| Project root | `/root/trina`, git initialised on `main`, no commits, no remote |
| Hyperfide | 2.0.0-dev, `.codegraph/` initialised, active feature `trina-memory` |
| Build host | 64 CPUs, 251 GB RAM, 499 GB free disk, no GPU, Python 3.12.3, `uv` absent |
| Agent drivers present | `claude`, `codex`; `gh` present |
| Credential files | `.env` and `.ssh.creds`, both mode 600, ignored by git; `.env` ignored by Hyperfide |

## 2. Source bucket

Listed read-only with `s5cmd` (642,028 objects, 171.8 GB). Variable names are in `.env` under the `BUCKET_` prefix.

| Prefix | Size | Objects | Content |
|---|---|---|---|
| `9router/calls/` | 167.08 GB | 629,578 | One gzipped JSON per model call, partitioned `dt=/provider=/model=/run=` |
| `openrouter-traces/` | 3.71 GB | 10,400 | One JSON per call, partitioned by date |
| `datasets/` | 0.71 GB | 68 | Built `v1` and `v2` datasets, pipeline code and READMEs |
| `work/` | 0.35 GB | 1,982 | Intermediate shards of that pipeline |

- Date range 2026-10-24 to 2026-11-10 (18 days).
- 162.91 GB of `9router/calls/` is `gpt-6.1-sol` through Codex; all other models total under 5 GB.
- Median call object is 226 KB compressed. One sampled object expanded 3.6 times to 820 KB, with 82,176 prompt tokens over 57 history items.
- Every call carries the full session history, so a session collapses to its last call. `run`, `agent` and `task` identifiers are null in every object; only session identifiers exist.
- Raw call objects contain client headers including IP addresses and installation identifiers.

### Existing `datasets/v2`

Read from its README; not independently verified.

- Built for a "decisions" model (context, routing, retrieval), not for this feature.
- `trajectories/{split}-NNNN.jsonl.zst`: one redacted session segment per row, 400 per file, 19 files, so at most about 7,600 segments.
- Splits by session: `sha1(session_id) mod 100`, 90 train, 5 validation, 5 test.
- Context labels (`ctx_ref_idx`, `ctx_lookback`) are a lexical heuristic that reproduced at 76% agreement between versions.
- Redaction is pattern-based; its README says to review before sharing.
- Its README notes that OpenAI's terms restrict using its outputs to develop competing models.

## 3. Teacher model access

| Route | Observation |
|---|---|
| Direct OpenAI, `gpt-6.1-sol`, key `OPENAI_API_KEY` | HTTP 200 in 2.71 s; 13 prompt tokens for a one-line prompt |
| Gateway 1, `cx/gpt-6.1-sol(high)`, key `CLUSTER_1_GATEWAY_API_KEY` | HTTP 200 in 1.84 s; 2,496 prompt tokens for the same prompt |
| Gateways 2 and 3 model lists | HTTP 200; 839 and 838 models |

- The gateway `cx/` route injects about 2,490 tokens into every call, so the direct route is the teacher route.
- `MODEL` in `.env` holds the gateway spelling. The direct API takes `gpt-6.1-sol` with reasoning effort as a separate parameter.
- Cluster keys 4 and 5 and the Messages endpoints were not exercised.

## 4. GPU host

Reached over SSH with password authentication from `.ssh.creds`.

| Item | Observation |
|---|---|
| GPU | 1 × NVIDIA B200, 183,359 MiB, driver 580.178.04, idle |
| CPUs | 224 |
| RAM | 59 GB |
| Disk | 446 GB free on an overlay root |
| Software | Python 3.12.3 only; no `git`, `uv`, `docker`, `conda` or `s5cmd` |

| Operating system | Ubuntu 24.04.1 LTS, x86_64, running in a container on an overlay root |
| Present | `rsync`, `curl`, `wget`, `pip3`, `nohup`, `systemctl`, passwordless `sudo` |
| Absent | `tmux`, `screen` |
| Outbound network | Package index, model hub and the `uv` installer site returned HTTP 200; the teacher API host was reachable (HTTP 401 without a key) |

Host RAM is under one third of GPU memory, so data loading on this host must stream and stay bounded. Whether the overlay disk survives a host restart was not measured.

## 5. Operator decisions recorded before authoring

- Target is general memory; the bucket supplies a coding-agent slice of it.
- Teacher is `gpt-6.1-sol` through the direct API key.
- Work runs in fleet mode: the main agent supervises and manages and does not implement tasks.
- The system is built and smoke-tested on the CPU host, then given a test run on the B200.
