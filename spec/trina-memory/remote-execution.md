# Remote GPU execution

Status: proposed contract, not implementation evidence. Requirements and tasks are in [spec.kvx](spec.kvx). This document owns requirement 14. Command names, directory layouts and thresholds are **proposals**. The only measured facts are in [baseline.md](baseline.md). Cases TRM-023 and TRM-024 are UNRUN. This document never contains a hostname, address, port, username or password; those live only in the ignored credential file governed by [security.md](security.md).

## 1. Host facts

From [baseline.md](baseline.md) section 4, observed on 2026-11-10:

| Item | Build host | GPU host |
|---|---|---|
| GPU | none | 1 × NVIDIA B200, 183,359 MiB, driver 580.178.04, idle when observed |
| CPUs | 64 | 224 |
| RAM | 251 GB | 59 GB |
| Free disk | 499 GB | 446 GB on an overlay root |
| Software | Python 3.12.3, `uv` absent | Python 3.12.3 only; no `git`, `uv`, `docker`, `conda` or `s5cmd` |
| Operating system | not recorded | Ubuntu 24.04.1 LTS, x86_64, running in a container on an overlay root |
| Tools present | not recorded | `rsync`, `curl`, `wget`, `pip3`, `nohup`, `systemctl`, passwordless `sudo` |
| Tools absent | not recorded | `tmux`, `screen` |
| Outbound network | not recorded | Package index, model hub and the `uv` installer site returned HTTP 200; the teacher API host was reachable (HTTP 401 without a key) |
| Access | local | SSH with password authentication from the ignored credential file |

Consequences for the design: the host is bare, so bootstrap installs the toolchain itself and can rely on Python 3.12, `curl` and the reachable installer and package index; there is no `git`, so the code revision travels in the sync manifest; `rsync` is present, so it can carry the transfer; `tmux` and `screen` are absent, so a detached launch must not depend on either; the teacher API is reachable from the host, so the reader and judge can run there, and by proposal the second labelling round too; host RAM is under one third of GPU memory, so loading is streamed and bounded ([training.md](training.md) section 8); the overlay root may not survive a host restart, so nothing on the host is the only copy of anything.

**Not measured** and therefore not assumed: whether the overlay disk survives a host restart. Section 7 and section 10 state what happens when it does not. Bootstrap records the host facts again on every run, so a host that no longer matches the table above is reported.

## 2. Command surface

Proposed commands, run from the build host (tasks 6.1, 6.2):

| Command | Clause | Purpose |
|---|---|---|
| `trina remote bootstrap` | 14.1 | Provision the host from the lockfile; record host facts |
| `trina remote sync` | 14.2 | Send code and datasets by content hash |
| `trina remote preflight` | 14.4 | Run the checks; non-zero exit refuses launch |
| `trina remote launch --config …` | 14.2 | Preflight, then start a detached run; prints the run identity |
| `trina remote status` / `logs --follow` | 14.2 | State and heartbeat; tail the redacted log |
| `trina remote fetch <run_id>` | 14.2 | Retrieve artefacts with checksum verification |
| `trina remote teardown` | 14.3 | Remove credentials; optionally remove the workspace |

Every command opens its connection through the mechanism of section 6 and is safe to repeat.

## 3. Bootstrap (clause 14.1)

Bootstrap turns a bare host into a host that can run the pinned environment, and may be run any number of times with the same outcome.

1. **Probe.** Record host facts to `host-facts.json` on the host and in the build-host run directory: GPU model and count, GPU memory, driver version, CPU count, RAM, free disk on the workspace filesystem, operating system, architecture, Python version, the presence of the tools listed in section 1, and outbound reachability of the package index, the model hub and the allow-listed teacher host. GPU, driver, memory and disk are the facts clause 14.1 names; the rest are proposals.
2. **Toolchain.** Install `uv` into the workspace under the remote user's home, at the version pinned in the repository, from the installer site that baseline.md records as reachable. Passwordless `sudo` is present but not used (proposal): no system packages, no root.
3. **Environment.** `uv sync --frozen` against the synced `uv.lock`. The lockfile digest is written to a stamp file. Packages come from the package index, which baseline.md records as reachable from the host. If the probe of step 1 finds it unreachable, bootstrap stops with that reason and leaves no stamp.
4. **Verify.** Import the framework, confirm the GPU is visible to it, and record library versions into the host facts.

**Idempotence.** Each step is guarded by a stamp keyed on its inputs (tool version, lockfile digest, platform). A second run with unchanged inputs performs probes only, changes no file other than the refreshed facts, and exits 0; a changed lockfile re-runs step 3 only. A failed step leaves no stamp. Task 6.1 runs bootstrap twice and compares.

A mismatch between recorded host facts and the facts in [baseline.md](baseline.md) (different GPU, less memory) is reported loudly; the B200 configuration is refused by preflight if its declared requirements no longer hold.

## 4. Synchronisation by content hash (clause 14.2)

- The build host produces a **sync manifest**: for each file, relative path, size and SHA-256; plus the code revision (commit identity and dirty-tree digest), the lockfile digest, the dataset version identity and the label-set version. The manifest itself has a digest that the run record stores as its code and data provenance.
- The remote side computes its own manifest with a stdlib-only script. Only files whose hash differs are sent, written to a temporary name, hashed, and renamed. Proposal: `rsync`, which baseline.md records on the host, carries the transfer over the existing connection; the manifest comparison, not the transfer tool, decides what is sent and whether the result is accepted.
- **Code**: tracked files plus `uv.lock`; never `.env`, the credential file, `runs/` or `data/` raw caches. The ignore rules of [security.md](security.md) are applied before the manifest is built, and the hygiene scanner runs over the outgoing file list.
- **Datasets**: only redacted, derived, versioned datasets (decision d_5) and their manifests. Raw bucket objects are never sent to the GPU host. Base model weights are fetched on the host from the model hub, which baseline.md records as reachable; their digests are pinned in configuration and checked after download.
- After sync, remote and local manifests are compared; any difference fails the command. Repeating a sync with nothing changed transfers nothing.

## 5. Preflight and refusal (clause 14.4)

`launch` always runs preflight first; there is no flag to skip it. Each check produces a line in `preflight.json`; one failure refuses the launch with a non-zero exit and starts nothing.

| Check | Passes when | Clause |
|---|---|---|
| GPU visibility | The expected GPU is visible to the driver tool and to the framework | 14.4 |
| Free GPU memory | Free memory ≥ the configuration's declared requirement; no foreign process holds the device | 14.4 |
| Disk space | Free space ≥ declared need for datasets, checkpoints (with retention) and logs | 14.4 |
| Dataset checksums | Every dataset file matches the dataset manifest; dataset version equals the configuration's | 14.4 |
| Environment | Lockfile stamp equals the synced lockfile digest | proposal |
| Code | Remote manifest digest equals the local one | proposal |
| Host memory | Configured host-memory limit is below physical RAM with headroom | proposal |
| Credentials | Remote env file exists, is mode 600, owned by the run user | 14.3 |
| No concurrent run | No live run holds the workspace lock | proposal |

Thresholds are configuration values of the test-run profile, not constants. Task 6.2 uses failing fixtures for each of the four mandated checks and requires a refusal.

## 6. Credential handling (clause 14.3)

The rules are owned by [security.md](security.md); this section states how remote execution obeys them.

- **Source.** Host address, user and password are read only from the ignored, mode-600 credential file by the secrets loader of task 0.4, which returns self-redacting values (clauses 3.1, 3.5).
- **Askpass.** The SSH client is started with an askpass helper: a small program that reads the credential file and writes the password to the client on request. The password is therefore never an argument, never an exported environment variable, never in shell history and never in a log; a process listing shows only the helper's path. One multiplexed control connection is opened per command so authentication happens once.
- **Logs.** Remote commands are logged by name and argument digest, and all captured output passes through the redacting logger. The host address and user are also redacted in logs and run records.
- **On the host.** The run needs the teacher key for the reader and judge, and by proposal for the second labelling round (section 8). It is written by streaming over the connection's standard input into `run.env` created with mode 600 in the workspace; it is never placed in a command, a unit file or the resolved configuration. The run process loads it and does not print its environment.
- **Teardown** removes `run.env` (section 9). A hygiene check after teardown confirms its absence.
- Task 6.1 inspects process listings and logs on both hosts for the credential values.

## 7. Detached launch, status, logs, retrieval (clause 14.2)

**Launch.** A stdlib launcher on the host starts the run in its own session, detached from the connection's terminal, with standard streams redirected to the log file, and writes `run.json` (run identity, process identifiers, start time, configuration digest, manifest digest) and takes the workspace lock. The launch command returns as soon as `run.json` exists. `tmux` and `screen` are absent from the host (baseline.md), so the launch does not use a terminal multiplexer: the launcher creates the new session itself, with `nohup` available as the equivalent shell form. `systemctl` is present, but the host is a container and no service unit is used (proposal).

**Survival.** The run holds no reference to the connection. Dropping the connection, closing the build-host shell or restarting the build host does not affect it. Task 6.2 drops the connection mid-run and reattaches.

**Status.** The run writes a heartbeat (time, stage, step, last metric) at a fixed interval and a terminal `state` on exit.

| State | Meaning |
|---|---|
| `running` | Process alive and heartbeat fresh |
| `stalled` | Process alive, heartbeat older than the configured threshold |
| `completed` / `failed` | Terminal state written by the run, with exit code |
| `lost` | No process and no terminal state: killed or host restarted; resume from the last checkpoint |

**Logs.** `logs --follow` tails the remote log by byte offset and resumes from the last offset after a reconnect.

**Retrieval.** The run closes its record with `SHA256SUMS` ([training.md](training.md) section 3). `fetch` copies run records, metrics, telemetry, reports and the selected checkpoints (proposal: best and last, plus the bundle) to the build host under `runs/`, recomputes every digest locally and compares; a mismatch fails the command and marks the local copy incomplete. Fetch is resumable by content hash. Nothing is deleted on the host until a fetch has verified.

## 8. B200 test-run definition (clause 14.5)

A declared configuration under `configs/b200/` (task 6.3), resolved and digested like any other. It is a **test run**: it proves that all three stages train and evaluate on the B200 and measures the hardware behaviour; it is not sized to be the best achievable model.

| Aspect | Definition |
|---|---|
| Trains | Retriever, selector with its pointwise comparison, assembler, in the order of [training.md](training.md) section 10, using the base models fixed by the baseline report of task 2.5, on the dataset and label versions produced by task 4.4 |
| Second labelling round | After the retriever is trained, its candidates are labelled through the teacher client under the campaign cap, so the selector and assembler train on the candidates they will serve (task 6.3). Proposal: the round runs on the GPU host, with the teacher cache synchronised so that spend already made is not repeated |
| Sized for | 183 GB of GPU memory and 59 GB of host RAM; batch sizes, sequence lengths and the host-memory limit are the proposals in [models.md](models.md) and [training.md](training.md), revised from the first measured steps |
| Bounded by | A declared wall-clock budget and step counts per stage (deferred; fixed in task 6.3) |
| Evaluates | The bundle through the pipeline interface, on the host and through the same code path as the CPU smoke run (clause 11.3). The full evaluation on the held-out generated set, the held-out bucket slice and the public benchmarks, per domain and category with every baseline and the ablations, is task 6.4 and also runs on the GPU host ([evaluation.md](evaluation.md)) |
| Returns | A checksum-verified run record and report tied to the dataset version and code revision |

The run record must contain:

| Measurement | Source (proposal) | Clause |
|---|---|---|
| GPU utilisation | Recorded by the training core (task 3.4). Sampled time series in `telemetry.jsonl` with a summary (mean, median, fraction of samples idle) per stage | 14.5 |
| Peak GPU memory | Framework peak allocated and reserved per stage, and the device-level peak from the driver tool | 14.5 |
| Throughput | Training examples and tokens per second per stage; evaluation queries per second | 12.6 |
| Stage latency | Median and 95th percentile per pipeline stage and end to end, with assembled tokens | 12.6 |
| Peak host memory | Against the configured limit | 11.4 |

Low GPU utilisation with high host-memory or loader time is an expected finding to report, not to hide: it would show that the 59 GB host is the bottleneck. The `verify_cmd` of tasks 6.1 to 6.4 names `--require gpu`, so an unreachable host fails the task; it cannot be done on a skip, and a test run that did not execute does not satisfy clause 15.5.

## 9. Teardown

1. Refuse while a run is `running`, unless forced by an explicit flag that first stops the run and records it as `failed`.
2. Confirm that every run on the host has a verified fetch, or list those that do not and require acknowledgement.
3. Remove `run.env` and any other credential-bearing file; overwrite before unlinking where the filesystem allows; verify absence (clause 14.3).
4. Close the control connection and remove the local control socket and askpass helper state.
5. Optionally (`--purge`) remove datasets, checkpoints, environment and workspace. Without it, the stamps remain so the next bootstrap is fast.

Teardown is idempotent and writes a teardown record on the build host. No system-level change was made by bootstrap, so none needs reverting.

## 10. Failure handling

| Failure | Behaviour |
|---|---|
| Connection drops during sync | Temporary files discarded; repeat sends only what is still missing |
| Connection drops during a run | Run continues; `status` and `logs` reattach |
| Host restarts or run is killed | State `lost`; relaunch resumes from the last indexed checkpoint if the disk survived, otherwise from the last fetched checkpoint after a re-sync |
| Preflight fails | Launch refused; `preflight.json` names the failed check |
| GPU out of memory | Run fails with the stage and step recorded; no automatic batch-size reduction, because that would change the resolved configuration |
| Host-memory limit crossed | Run stops with `aborted_memory_limit` ([training.md](training.md) section 8) |
| Disk fills | Checkpoint write fails atomically; previous checkpoint intact; run fails cleanly |
| Fetch checksum mismatch | Local copy marked incomplete; remote copy retained; fetch can be repeated |
| Teacher host unreachable from the GPU host | The second labelling round and the end-to-end rows cannot be produced; the run stops at that step with the reason recorded and resumes from its checkpoints once the host is reachable. An incomplete report does not satisfy clause 14.5 |
| Credential file missing or not mode 600 | Every remote command refuses before opening a connection |
| Host unreachable | Tests marked `gpu` report SKIPPED with the reason, never PASS (decision d_9); under `--require gpu`, which tasks 6.1 to 6.4 use, the unmet dependency fails the `verify_cmd` |
