# Evidence report: iteration 1 (2026-11-04)

Evidence class for everything here: **verified software correctness** (§A, §B)
and **simulated, policy-free infrastructure health** (§C). There are **no** model-in-
the-loop results, no causal-intervention evidence, no real-hardware validation,
and no customer outcomes.

## A. Repository test suite (CPU, this container)

| Run | Population | Passed | Failed | xfailed |
|---|---|---:|---:|---:|
| Baseline, before any edit (after installing undeclared test dep `h5py`) | `pytest tests` | 412 | 4 | 1 |
| Milestone, commit `897f161` + docs | `pytest tests` | 459 | 4 | 1 |

459 − 412 = 47 new passing tests, all in `tests/test_run_registry.py`. The **same
4 tests fail before and after**, so there are no new failures:

- `tests/test_r05.py::test_g8_evidence_fingerprints_pass_on_committed_files`,
  `::test_g8_fingerprints_fail_closed_on_drift`,
  `::test_g5_unseatable_pair_leaves_filter_unchanged`. Root cause (from the
  error text): locked N5 covariance scalar `radial_side_covariance_m2` expected
  `-4.844441351432012e-13`, found `-4.844441351432015e-13`. That is last-ULP
  float drift on this platform (numpy 2.5.1 / mujoco 3.11.0). Not touched:
  locked/behaviour-frozen area.
- `tests/test_rollout.py::TestRunEpisode::test_capture_localization_error_within_clearance`:
  `capture_step` is `None` (MuJoCo rollout did not capture). Not diagnosed further,
  because it is in the physics substrate and unrelated to this work.
- `AGENTS.md` says to "expect 396 passed, 1 xfailed". That figure is stale
  relative to the code at `0a0bd9e`, which collected 417 tests before this work.

## B. M0 registry: adversarial and property tests (47 tests, all pass)

| Category | Cases | Result |
|---|---|---|
| Malformed / non-strict JSON (duplicate keys at top level and nested, NaN, Infinity, trailing comma, single quotes, trailing garbage, int > 2^53, lone surrogate, empty, BOM, invalid UTF-8) | 12 | all rejected |
| RFC 8785 vectors (key order incl. non-ASCII/control, ES6 number format) | 1 | exact bytes match |
| Property: canonicalization idempotent and key-order independent | 200 examples | pass |
| Property: any canonical output re-reads (regression, D4) | 200 examples | pass |
| Invalid manifests (bad month, Feb 30, space separator, non-UTC offset, free text, path-traversal run_id, empty run_id, unknown track, extra field, missing provenance, bad commit sha, track-conditional violations ×4) | 15 | all rejected, each logged as `rejected_input` with no manifest stored |
| Duplicate run_id, unreadable float | 2 | rejected; original manifest unchanged |
| Lifecycle: refused transitions | 1 test, 5 refusals | pass |
| Property: random op sequences never change state on refusal and never corrupt the chain | 60 sequences | pass |
| Artifact corruption: byte edit / deletion / unrecorded extra | 1 | 3/3 detected, state → `INTEGRITY_FAILED` |
| Manifest tamper (canonical re-write) | 1 | detected |
| Ledger tamper: edit, delete line, reorder, forged append | 4 | 4/4 detected |
| Clock moving backwards | 1 | refused, state unchanged |
| Approvals: altered decision, untrusted key, wrong manifest binding | 3 | 3/3 rejected |
| Provenance: registry writes don't mark checkout dirty; real edits do | 1 | pass |
| End-to-end record → verify → compare | 1 | pass |
| Committed registry evidence intact (`results/m0_registry`) | 1 | pass (4 runs) |

**Not demonstrated:** resistance to an attacker who can rewrite the *whole*
ledger (the hash chain alone cannot detect that). Ed25519 here is test-only, with
caller-supplied keys and no key management. It does not enforce approval gates.

## C. Campaign 0A: LIBERO-Dynamic, CPU policy-free (pinned `ef12443`)

Population per run: upstream documented zero-policy smoke, `--levels l1
--traj-indices 0 --limit 4 --max-steps 40`. That is 4 items
(`libero_10_task00..03__l1__traj00`), each 40 control steps = 2.0 s simulated.
Videos disabled. Renderer: OSMesa (CPU).

| M0 run | Commit | git_dirty | total | ok_status | diagnostic_ok | task success | Upstream acceptance (4 checks) |
|---|---|---|---:|---:|---:|---:|---|
| `c0a-smoke-l1-n4-s40-001` | 8a29040 | true* | 4 | 4/4 | 4/4 | 0/4 | pass |
| `c0a-smoke-l1-n4-s40-002` | 8a29040 | true* | 4 | 4/4 | 4/4 | 0/4 | pass |
| `c0a-smoke-l1-n4-s40-003` | 897f161 | false | 4 | 4/4 | 4/4 | 0/4 | pass |
| `c0a-upstream-unit-tests-001` | 8a29040 | true* | upstream `pytest tests`: **42 passed** | | | | |

\* `true` is caused by the recorder itself (decision D5), fixed and confirmed by run 003.

0/4 task success is expected and is **not a baseline**: a zero policy does not
attempt the task. Upstream itself says the zero policy is an infrastructure and
physics smoke, not a task-success baseline.

**Reproducibility** (`scripts/compare_libero_smoke_runs.py`): 001 vs 002 and
001 vs 003 are both `simulation_reproducible: true`. All 4 per-step trace files
(`traces/*.jsonl`), stdout, and the effective config are byte-identical. Every
differing JSON field is wall-clock latency/elapsed time or an absolute path
(full list in `results/libero_dynamic/repro_*.json`).

**CPU cost** (wall-clock `elapsed_seconds` per item, includes env construction):

| Run | per-item s | sum s |
|---|---|---:|
| 001 | 90.8, 56.1, 57.6, 44.2 | 248.7 |
| 002 | 81.6, 56.9, 53.0, 41.5 | 232.9 |
| 003 | 79.8, 57.3, 57.3, 43.0 | 237.4 |

So on this host it takes roughly 41–91 s to build and run a 40-step item. This
does **not** support extrapolating to full 600-step episodes: the split between
construction and per-step cost was not measured.

**Upstream defects found (measured):** the README's preflight checks 0 tasks and
exits 0. The preflight script can't load the released `physical_conveyor_patch`
configs. First import blocks on `input()` without `LIBERO_CONFIG_PATH`.

## D. Track B and G0: no evidence

The 38 Track B fixtures and the G0 definition are not in the repository (see
DECISION_LOG D1). **There are no false-admission, false-refusal, substitution,
authority, precedence or lifecycle error counts to report**, because no
specified fixtures were available to run.

## E. Not demonstrated

- Any Haplous effect on any policy or benchmark.
- Any model-in-the-loop LIBERO-Dynamic result. All upstream success rates quoted in
  docs are **copied from upstream's `results/`, not measured**.
- Checkpoint integrity (HF unreachable).
- GPU throughput or cost (estimates only, GPU_HANDOFF.md).
