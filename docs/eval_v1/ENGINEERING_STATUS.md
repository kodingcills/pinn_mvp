# Engineering status: Haplous Generalized Execution Evaluation

This is the persistent engineering state file. Update it at every iteration.
Last update: 2026-11-04, iteration 2, branch `claude/magical-curie-si1vjr`.

## Iteration 2 headline

The v1.1 package was reported as supplied at `evaluation/spec/haplous_eval_v1_1/`.
**It is not accessible.** That path does not exist in the working tree, on any
remote branch (`main`, `UI`, `claude/charming-feynman-y7k2ir`,
`claude/magical-curie-si1vjr`; checked after `git fetch`), in any pull request
(the repository has none), in GitHub code search for `BASELINE_SPEC`, or in the
session upload directories. Dependent work is stopped. Nothing was substituted.
`huggingface.co` is still refused by the egress proxy, despite being reported
as enabled.

## Status by required delivery item

| # | Item | Status |
|---|---|---|
| 1 | M0 reconciled to canonical v1.1 schemas | **BLOCKED** (no schemas). Spec-independent requirements implemented and tested, see table below |
| 2 | Track B execution-contract engine | **BLOCKED** (no fixtures, no BASELINE_SPEC). Not started, by design |
| 3 | 38 cases + adversarial tests | **BLOCKED** |
| 4 | G0 comparison | **BLOCKED** (G0 definition is in the missing BASELINE_SPEC) |
| 5 | Manifests, raw evidence, deterministic summaries | **DONE** for Campaign 0A (7 M0 runs). Contract-track pipeline blocked with item 2 |
| 6 | CPU/GPU instructions with verified checkpoint memory and hashes | CPU **DONE**. GPU job **READY** (`scripts/gpu/g1_one_episode.sh`). Checkpoint numbers **BLOCKED** (HF refused), tool ready and offline-tested |
| 7 | Report separating implemented/tested/blocked/unverified | this file + EVIDENCE_REPORT.md |
| 8 | Tier 2 / Tier 3 dependencies | below |

## M0 requirements checked without the canonical spec

"Canonical requirement" can't be filled in until the package arrives. These are
the requirements named in the iteration-2 assignment.

| Requirement | Before iteration 2 | Satisfied now? | Change | Test |
|---|---|---|---|---|
| Contract- vs VLA-track manifests | `cpu_contract` / `sim_policy_free` / `sim_model_in_loop` conditional schema | yes (provisional names) | model-in-loop now also requires a measured `provenance.gpu` | `test_invalid_manifests_rejected_and_logged`, `test_model_in_loop_requires_measured_gpu` |
| RFC 8785 with official vectors | own vectors + property tests only | **yes** | vendored cyberphone testdata @ `32a48195` (6 files) | `test_official_rfc8785_vectors` (6/6 exact bytes) |
| Duplicate-key rejection | yes | yes | — | `test_strict_loads_rejects`, `test_duplicate_key_manifest_bytes_rejected` |
| Date-time validation | RFC 3339 + `Z` required | yes | — | 5 timestamp cases |
| Immutable manifests | O_EXCL + digest in ledger | yes | concurrent same-id register now rejected cleanly | `test_duplicate_run_id_rejected`, `test_verify_detects_manifest_tamper` |
| Reproducible artifact integrity | SHA-256 per file | yes | — | `test_verify_detects_corrupted_missing_and_extra_artifacts`, committed-evidence test |
| Provenance across Python envs | recorder env only; subject env ad hoc in driver | **yes** | `record_run(subject_python=…)` → measured `config.subject_environment` | `test_subject_environment_is_measured_not_claimed` |
| Ledger concurrency + transition invariants | **defect**: state checked outside the lock | **fixed** | decisions run on the history read under the lock | `test_concurrent_processes_cannot_double_transition` (fails on the old code, passes now) |
| Ed25519 test signing | yes | yes (test-only) | — | approval tests |
| Signature binds the full decision | exact field set + canonical body | yes | — | `test_signature_binds_every_decision_field` (every field, plus an extra field) |
| Mutated / replayed authorization | mutation rejected; replay **not** rejected | **fixed** | replay refused (keyed on signed-body digest, under lock); cross-run replay refused by binding | `test_approval_replay_rejected_same_and_other_run` |

Test-only signing is not a Tier 3 security implementation: there is no key
management, no revocation and no environment-enforced gate.

## Tests

- Baseline at resume: 459 passed, 4 failed, 1 xfailed, identical to iteration 1.
  Failure summary kept in `results/diagnostics/suite_failures_2026-11-04.txt`.
- Final: see EVIDENCE_REPORT.md §A.
- Undiagnosed physics failure, one timeboxed attempt
  (`results/diagnostics/test_rollout_capture_failure.json`): the episode seats,
  but the closest lateral approach is 0.50303 mm vs the 0.5 mm capture
  threshold (3.03 µm outside), so `capture_step` stays `None`. Probably the same
  platform floating-point sensitivity as the three R05 failures. Not modified.

## Files changed in iteration 2

`evaluation/run_registry.py` (lock-held decisions, replay rejection,
`probe_environment`, `subject_python`, measured GPU, `hardware_comparable`),
`evaluation/schemas/run_manifest.schema.json` (`provenance.gpu`),
`scripts/run_libero_dynamic_cpu_smoke.py` (`--latency-s`, probe),
`scripts/verify_hf_checkpoint.py`, `scripts/gpu/g1_one_episode.sh`,
`tests/test_run_registry.py`, `tests/test_libero_dynamic_evidence.py`,
`tests/test_verify_hf_checkpoint.py`, `tests/data/rfc8785/`,
`results/diagnostics/`, 2 new M0 runs, docs.

## Remaining dependencies

**Tier 1 (blocked on inputs, no approval needed once supplied)**
- The v1.1 package at `evaluation/spec/haplous_eval_v1_1/` (4 schemas, 38 Track B
  scenarios, fixture defaults, BASELINE_SPEC.md, SIGNING_PROTOCOL.md,
  CPU_ASSIGNMENT.md, validator + tests). It unblocks M0 reconciliation, Track B, G0
  and the contract evidence pipeline.
- `huggingface.co` reachable from the session. It unblocks checkpoint step 0.

**Tier 2 (needs human approval: paid compute)**
- G1: one π0.5 episode (≤$5, ≤2 h), then G2 subset, then G3 full population,
  each separately approved. Provider account and an rclone destination are
  needed.
- Any cross-hardware A/A check, if arms ever run on different GPU types.

**Tier 3 (needs human approval; not prepared)**
- Sealed holdout execution, hidden seeds, external performance claims,
  physical robot deployment. Production-grade signing (key custody,
  revocation, environment-enforced approval gates) would be needed before any
  Tier 3 approval record could be trusted. M0's Ed25519 is test-only.

## Next executable actions

1. When the package is present: run its validator, diff its four schemas
   against `evaluation/schemas/`, fill in the "canonical requirement" column
   above, then implement Track B against the 38 fixtures plus property tests,
   then G0 per BASELINE_SPEC.
2. When HF is reachable: run step 0 from GPU_HANDOFF.md and commit
   `results/checkpoints/*.json`.
3. With approval: run `scripts/gpu/g1_one_episode.sh`.
