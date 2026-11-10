# Credential and data security

Status: proposed contract, not implementation evidence. Requirements and tasks are in [spec.kvx](spec.kvx).

This chapter owns requirement 3 and the credential clause of requirement 14 (clause 14.3). The rest of requirement 14 is in [remote-execution.md](remote-execution.md). This document names variables and files only; it contains no credential, key, hostname, address or bucket name, and no later revision may add one. All cases (TRM-003, TRM-004, TRM-007, TRM-023, TRM-028) are UNRUN.

## 1. Assets and sensitivity

| Asset | Where it lives | Sensitivity | Rule |
|---|---|---|---|
| Provider and gateway API keys, bucket keys and endpoint | `.env` (names such as `OPENAI_API_KEY`, `CLUSTER_1_GATEWAY_API_KEY`, the `BUCKET_` prefix) | Secret | Section 2 |
| GPU host address, user and password | `.ssh.creds` | Secret | Sections 2 and 7 |
| Raw bucket objects | The bucket only | Sensitive: by [baseline.md](baseline.md) they contain client headers with IP addresses and installation identifiers, and prompts may contain secrets typed or read by an agent | Streamed, never persisted ([data.md](data.md) section 2.1) |
| Existing `datasets/v2` rows | The bucket only | Sensitive: pattern-redacted by another pipeline whose README says to review before sharing | Treated as raw; re-redacted |
| Derived records, labels, teacher cache | Ignored `data/` | Redacted but not anonymous: real session content | Sections 4 and 5 |
| Run records, logs, checkpoints | Ignored `runs/` | Must contain no secret and no raw content | Section 3 |
| Tracked files | The repository | Public by assumption | Section 3 |

Redaction lowers risk; it does not make trace content publishable. Sharing any derived dataset outside the two hosts is an operator decision outside this specification.

## 2. Credential rules (clauses 3.1, 3.5)

1. **Two sources only.** Credentials are read from the process environment or from the ignored credential files `.env` and `.ssh.creds`, through one secrets loader (task 0.4). No other module reads those files or environment names.
2. **Ignored twice.** Each credential file is ignored by git and by Hyperfide (`.gitignore` and `.hyperfideignore`). Any new credential file is added to both before it is created. Task 0.1 also adds `data/` and `runs/` to both files.
3. **Mode 600.** Each credential file is readable and writable by its owner only.
4. **Opaque values.** The loader returns a wrapper whose `repr`, `str`, formatting and serialization yield a fixed placeholder. The raw value is obtained through one explicit method at the point of use (an HTTP header, a signing call, the askpass helper) and is not stored on any object that is logged, pickled or written to a run record. The loader never writes a credential anywhere.
5. **Never in:** tracked files, configuration documents, resolved configurations, logs, run records, datasets, cache entries, checkpoints, command lines, process titles, environment dumps, URLs, or error messages. Configuration holds the *name* of the variable to read, never its value.
6. **Error messages.** Exceptions raised by clients are built from status, error class and request digest. Request headers, signed URLs and response bodies that echo a request are not included. A global log filter replaces any occurrence of a loaded secret value as a second line of defence; it is not the primary control.
7. **Request digests** ([labeling.md](labeling.md) section 1) exclude headers and credentials, so cache keys and spend records cannot leak one.
8. **Tests use synthetic secrets only** (task 0.4). No test reads a real credential except through the loader in a test marked for its external dependency.

## 3. The hygiene gate (clauses 3.1, 3.5)

`scripts/check_hygiene.py` (task 0.1) and the artifact scanner (task 0.4) both run inside `make lint` and are therefore fleet gates for every task. Neither prints a matched value: findings are reported as file, line, pattern kind and a short hash.

| Check | Fails when | Clause |
|---|---|---|
| File mode | A credential file exists with any mode other than 600 | 3.5 |
| Git ignore | A credential file is not ignored by git, or is tracked or staged | 3.5 |
| Hyperfide ignore | A credential file is not ignored by Hyperfide | 3.5 |
| Tracked-file scan | A credential pattern occurs in a tracked or staged file | 3.1 |
| Artifact scan | A credential pattern, or a loaded secret value, occurs in produced artifacts under `data/` and `runs/` (the scanner command of task 0.4, wired into `make lint` by that task) | 3.1 |
| Ignored output | `data/` or `runs/` is not ignored by git or by Hyperfide | Supports 2.1, 3.1 |

Patterns come from the same catalogue as redaction (section 4), so the gate and the redactor cannot drift apart. Fixtures containing synthetic secrets live under `tests/fixtures/security/` and are matched by an explicit, reviewed path exemption with a marker in each file; an exemption anywhere else fails the gate. Case TRM-003 proves the gate fails on a planted credential and on a loosened mode, and passes on the repository.

## 4. Redaction (clause 3.2)

Redaction runs before any record is persisted, in the same process that parsed it, and writers accept only the redacted record type ([data.md](data.md) section 2.1). It applies to every source, including generated text and public sets, because the external-call check in section 6 depends on it.

| Kind | Matches | Action |
|---|---|---|
| `api_key` | Provider-style API keys | Replace with `[REDACTED:api_key]` |
| `cloud_token` | Cloud access key identifiers and secret keys | Replace |
| `repo_token` | Repository-hosting and package-registry tokens | Replace |
| `bearer` | Bearer and basic authorization values, signed-URL signature parameters | Replace |
| `private_key` | PEM-style private key blocks | Replace the whole block |
| `assignment` | Values assigned to names such as password, secret, token, key, in code, shell, environment-file and JSON forms | Replace the value, keep the name |
| `ip_address` | IPv4 and IPv6 addresses | Dropped: the address is removed and a typed placeholder that carries nothing of it marks the place. Clause 3.2 makes no exception for any range |
| `installation_id` | Installation, device and machine identifiers | Dropped in the same way |
| `header` | Request and client header fields | Dropped entirely, name and value (below) |

- **Headers.** Client headers are dropped entirely (clause 3.2, task 0.4). They are structured data, so they are removed as a whole, name and value, and not pattern-scanned; there is no allow-list of kept fields and no configuration that keeps one. An unknown new header is therefore dropped like every other.
- **Placeholders** are typed and stable, so later stages can treat them as ordinary tokens and cross-source dedupe can normalise them. They do not encode the original value or its hash.
- **Counts.** Every record carries redaction counts by kind, and every build sums them into its manifest (clause 3.2). A source with no redactions at all is reported, since that can mean the redactor did not run.
- **Order.** Structured drops first, then block patterns, then token patterns, then assignments. Redaction is idempotent: redacting a redacted record changes nothing.
- **Limits.** Pattern redaction misses secrets with no recognisable shape, such as a password in prose. The audit in section 5 lowers that risk and cannot remove it; the data card says so.

## 5. Residual-secret audit (clause 3.3)

The audit runs over a sample of every persisted dataset: ingestion output, episodes, generated histories, converted public sets, assembled dataset versions, label sets and the teacher cache. Task 1.1 runs it on the written ingestion shards, task 2.4 on each dataset version, and task 4.4 on the production dataset and label versions (case TRM-028).

- **Detectors.** The redaction catalogue applied again, plus stricter detectors not used for redaction: high-entropy strings next to credential-like names, known key prefixes, private-key markers, and a direct comparison with the values currently held by the secrets loader (compared in memory, never printed).
- **Confidence.** Each detector declares its matches high or low confidence. Any high-confidence match fails the build (clause 3.3). Low-confidence matches are counted and listed by record identity for review; they do not fail the build.
- **Sample.** Seeded, stratified by source and shard; proposal: small datasets are scanned in full. Sample size and seed are recorded. No sampling rate has been measured or justified yet, and a sample cannot prove absence.
- **On failure.** The dataset version is not published, its manifest is not written, and the finding is handled under section 9. The fix is a new pattern in the catalogue and a rebuild, never an edit of the offending record alone.
- **Report.** Dataset identity, sample size, counts by detector and confidence, and record identities. Never the matched text.

## 6. External-call boundary (clause 3.4)

| Rule | Contract |
|---|---|
| Redacted content only | A request body to an external model is built only from redacted record types. The teacher client additionally runs the high-confidence detectors on the outgoing body and refuses on a match (task 1.3) |
| Host allow-list | Destination hosts are listed in configuration by role (teacher, bucket, public dataset sources, package and model sources, GPU host). The resolved configuration records the list |
| Refusal | A call to a host not on the list raises a typed error before name resolution or connection, sends nothing, is logged with the host and caller but no payload, and is not retried |
| Redirects | Not followed to a host off the list; credentials are never forwarded across hosts |
| Gateways | The gateway routes in `.env` are not teacher destinations (decision d_4); they are off the allow-list unless the operator adds them |
| No other egress | Training and evaluation need no external service (clause 11.6). Metrics, telemetry and crash reporting to third parties are disabled |

Clause 3.4 names external models. Proposal: the same allow-list governs every outbound connection the package makes, so there is one place to review. The provider receives redacted session content; that this is acceptable is part of the operator decision in [data.md](data.md) section 9.

## 7. Remote host credentials (clause 14.3, tasks 6.1 and 6.2)

- **Connection.** The host address, user and password are read from `.ssh.creds` by the secrets loader. The password reaches the SSH client through an askpass helper that obtains it from the parent process over an inherited descriptor or a mode-600 temporary file removed on exit. It is never an argument, never in a URL, never in an exported environment variable visible in a process listing, and never echoed.
- **Logging.** Remote commands are logged by name and digest. Tracing of shell commands is off wherever a credential could be expanded. Case TRM-023 inspects process listings and logs on both hosts for the credential.
- **Credentials on the host.** A run receives only what it needs. The B200 run trains and evaluates on synchronised data, so it needs no bucket key. It needs the teacher key, because the reader and judge of the evaluations in tasks 6.3 and 6.4 run on the GPU host, through the teacher client and its cap; proposal: the second labelling round of task 6.3 runs there too. What is needed is written to one environment file on the host, created with mode 600 before content is written, transferred over the encrypted channel's standard input, and read by the run through the same secrets loader.
- **Teardown.** Teardown removes the environment file and verifies its absence; a teardown that cannot verify fails loudly. Teardown runs on success, on failure and on cancellation. A run that was detached and lost is cleaned by the next bootstrap, which removes any stale environment file before doing anything else.
- **Host key.** The host's key is pinned on first bootstrap and checked afterwards; a change stops the connection and is reported to the operator.
- **Not on the host:** `.env`, `.ssh.creds`, the git history, or raw bucket content. Only redacted dataset versions are synchronised, by content hash (clause 14.2).

Password authentication is the measured starting state (baseline.md section 4). Moving to key authentication is a proposal for the operator, not a requirement.

## 8. Fleet agents

| Agents may | Agents may not |
|---|---|
| Call the secrets loader from code and tests | Read, print, copy, summarise or quote `.env` or `.ssh.creds`, or any value from them |
| Refer to credentials by variable name | Put a value in a commit, task text, prompt, tool argument, log, specification file or report |
| Run tests marked `network`, `bucket`, `teacher`, `gpu`, which report SKIPPED when the dependency is unmet and fail when the task's `verify_cmd` names it with `--require` (clause 15.1) | Edit credential files, loosen their mode, or remove them from an ignore file |
| Read redacted derived data under `data/` | Persist, paste or forward raw bucket content, or paste derived trace content into a tracked file or an external service |
| Use the teacher only through the teacher client, within the campaign cap | Call a model or any host outside the allow-list, or bypass the client's redaction check |
| Add synthetic secrets under `tests/fixtures/security/` | Use a real credential as a fixture, or weaken a pattern to make a gate pass |
| Stop and report a suspected leak | Rewrite history or delete evidence to hide one |
| Leave evidence entries to the main supervisor, who records them with `scripts/record_evidence.py` when a wave lands | Edit `qualification.kvx` as a task worker, other than in release closure (task 7.2) |

An agent's own conversation with its model provider is an external call. Agents therefore work from fixtures and redacted records, and do not load raw trace content into their context.

## 9. Incident handling

A secret found in an artifact, a log, a dataset or a commit is handled in this order.

1. **Stop.** Halt the build or campaign that produced it. Do not push. Do not copy the value into a report: identify it by file, line, kind and short hash.
2. **Report** to the operator and the main agent at once. Only the operator can rotate a credential, so the report is the first action, not the last.
3. **Rotate.** A credential of this system that has reached a tracked file, a commit, a log, the GPU host outside its environment file, or an external service is treated as compromised and rotated by the operator, even if the exposure looks local.
4. **Contain.** In an ignored artifact: delete the artifact and everything derived from it (dataset versions, label sets, cache entries, checkpoints trained on it), by identity from the manifests. In a commit: the repository has no remote at baseline, so history is rewritten before any push; if it was pushed, rotation is the remedy and rewriting is cleanup. Sent to the provider: record what was sent and when; it cannot be recalled.
5. **Third-party secrets.** A secret belonging to someone else, found in trace content, cannot be rotated by the operator. The artifact is deleted, the pattern is added, and the operator decides whether the owner can be told.
6. **Fix the cause.** Add a redaction pattern or gate check with a synthetic fixture that reproduces the miss, then rebuild from the snapshot. Hand-editing a record is not a fix.
7. **Record.** An incident entry in [qualification.kvx](qualification.kvx), written by the main supervisor, with kind, location, detection path, exposure, rotation status and the fixture added, and no secret material.
