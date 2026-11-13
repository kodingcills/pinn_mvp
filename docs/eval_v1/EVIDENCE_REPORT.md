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

---

# Iteration 2 (2026-11-04)

Evidence classes are unchanged: verified software correctness and simulated
policy-free infrastructure behaviour. No model-in-the-loop, no hardware, no
Track B/G0 evidence.

## A2. Test suite

| Run | Passed | Failed | xfailed |
|---|---:|---:|---:|
| Resume baseline (`af54a73`) | 459 | 4 | 1 |
| Final (iteration-2 code) | 488 | 4 | 1 |

488 − 459 = 29 new tests: `test_run_registry.py` 47 → 62 (+15),
`test_libero_dynamic_evidence.py` +11, `test_verify_hf_checkpoint.py` +3.
The 4 failures are the same tests as before (no new failures). The capture
failure is now characterized: closest approach 0.50303 mm vs 0.5 mm threshold
(`results/diagnostics/test_rollout_capture_failure.json`).

## B2. M0 additions

| Check | Population | Result |
|---|---|---|
| Official RFC 8785 vectors (cyberphone @ `32a48195`) | 6 files | 6/6 byte-exact |
| Concurrent `start` by 6 processes on one run (barrier, slowed reads) | 1 run | exactly 1 `started` event, 5 refusals. **The same test fails on the iteration-1 code** (multiple `started` appended) |
| Approval replay (same run, other run) | 2 | both refused |
| Signature binding: change any one of 6 fields, or add a field | 50 examples + 1 | all refused |
| Model-in-loop manifest with GPU null / empty / missing | 3 | all rejected |
| `compare` across GPU types | L4 vs L4, L4 vs 4090 | `hardware_comparable` true / false |
| Subject env measured, caller claim overwritten | 1 | pass |

## C2. Fixed-latency timing semantics (LIBERO-Dynamic, CPU, `ef12443`)

Two new bounded runs (1 item, `libero_10_task00__l1__traj00`, 40 steps, zero policy):

| M0 run | L (s) | commit | git_dirty | ok / diag ok / success | sim time per step | sim rollout (s) | wall (s) |
|---|---:|---|---|---|---|---:|---:|
| `c0a-latency-0.03-n1-s40-001` | 0.03 | abba378 | false | 1/1, 1/1, 0/1 | 0.08 (all 40) | 3.2 | 143.3 |
| `c0a-latency-0.147199-n1-s40-001` | 0.147199 | abba378 | true† | 1/1, 1/1, 0/1 | 0.197199 (all 40) | 7.88796 | 121.8 |

† dirty because the previous run's verdict file
(`results/libero_dynamic/cpu_smoke_c0a-latency-0.03…json`) was untracked when
it started. That's a true tree state, not a recorder defect.

Calculations: 40 × (0.05 + 0.147199) = 7.88796 s, which equals the manifest
`policy_rollout_sim_time_s`. Quantizing to control periods would give 0.20 s per
step (8.0 s), and whole substeps would give 0.198 s (7.92 s). Both are excluded
by the trace. **Demonstrated:** sync latency is held for exactly L in simulated
time, independent of wall-clock. **Also found:** the zero policy returns
1-action chunks, so latency is charged at all 40 steps (`replan_steps_requested:
10` notwithstanding). **Not demonstrated:** per-chunk latency with a real K=10
policy (needs G1).

## D2. Not measured (blocked)

- Track B: 0 of 38 cases run, because the fixtures are not present.
- G0: no comparison, because the definition is not present.
- π0.5 checkpoint revision/params/hashes: `huggingface.co` refused (proxy
  `connect_rejected`, 06:16:50Z and later).

---

# Iteration 3 (2026-11-04)

Evidence class: **verified software correctness against independently specified
fixture truth.** No physical safety, no protective-stop behaviour, no OEM
portability, no VLA performance. `TERMINATE`/external-stop cases are software
semantics only (package README).

## A3. Package and test suite

- Package zip SHA-256 `9c4130709feb80684b34d8a291ca95cd37ef6522cf6629bf59788cb3db41390d` (matches).
  `tools/validate_package.py` PASS; package `pytest` 18 passed (same as `VALIDATION_RESULTS.md`).
- Repository suite: iteration-3 baseline 488 passed / 4 failed / 1 xfailed. Final: see §F3.

## B3. Track B: candidate correctness vs fixture truth (population: 38 fixture cases)

Two M0 runs, canonical v1.1 `contract` manifests, commit `7765148`, `git_dirty=False`:

| Run | manifest SHA-256 | passed | state |
|---|---|---|---|
| `track-b-v1.1-001` | `2e6376f8e99aa04557329e064e45f937b6b6e60b92ee01499c520888fa26852a` | 38/38 | VERIFIED |
| `track-b-v1.1-002` | `6f0f6989fd25a8e02297ed301241d4c286717e88c1c196fea24b1121b7d5c81f` | 38/38 | VERIFIED |

All 5 output artifacts are byte-identical across the runs (`results.jsonl`
`243bee32…`, `summary.json` `e82e2d73…`, `report.md` `ce834fea…`).

| Metric | Count / denominator |
|---|---|
| passed (decision + reason + required details) | 38 / 38 |
| decision correct | 38 / 38 |
| reason correct | 38 / 38 |
| false admissions | 0 |
| false refusals | 0 |
| failed terminal transitions | 0 |
| wrong reason / wrong details | 0 / 0 |
| multi-fault (interaction) cases correct | 8 / 8 |

Confusion (expected → observed) is diagonal: ADMIT 8, REFUSE 23, SUSPEND 4,
TERMINATE 3. Interaction cases with their decisive fault listed first:
`external_stop_over_authority` [OEM_STOP_ACTIVE, AUTHORITY_REVOKED];
`precedence_epoch_over_capability_and_clock` [AUTHORITY_REVOKED, CAPABILITY_UNSUPPORTED, CLOCK_INVALID];
`precedence_capability_over_clock`, `precedence_clock_over_freshness`
(actually clock over **command deadline**: name only, label correct),
`precedence_command_deadline_over_obs_stale`, `precedence_epoch_over_stale`,
`stale_ack_over_obs_stale`, `wrong_resource_with_stale_obs`.
Full per-case traces: `results/track_b/track-b-v1.1-001_report.md`.

Independent tests (`tests/test_track_b.py`, 79): single faults (17), all
compatible fault pairs (>100), 400 random fault sets, determinism and key-order
independence, revocation and lifecycle invariants, input completeness (28
missing-key plus 11 malformed cases), no-silent-downgrade (500 random mode
configurations), inclusive boundaries (200 random limits), and judge
non-vacuity. Mutation check: 3/3 engine mutants killed (one only after adding
the IDLE-REVOKE injector).

## C3. G0 mechanism-isolation comparison (NOT competitive)

`baseline_g0_minimal_guards` is a deliberately limited ablation. No advantage
over it is an advantage over ROS 2, an OEM controller or an integrator.

| | Count / 38 |
|---|---|
| G0 output equals its own spec label (`expected_baseline_g0`) | 38 |
| G0 aligned with truth (ADMIT→ALLOW, REFUSE/SUSPEND→BLOCK, TERMINATE→STOP) | 19 |
| G0 ALLOW where truth is not ADMIT (omission by construction) | 18 |
| G0 BLOCK where truth is ADMIT | 1 (`behavioral_native_force_approved_and_verified`) |

Truth × G0: ADMIT {ALLOW 7, BLOCK 1}; REFUSE {ALLOW 12, BLOCK 11};
SUSPEND {ALLOW 4}; TERMINATE {ALLOW 2, STOP 1}. G0 implements 3 of the 20
checks the candidate performs (external stop state, exact mode availability,
command deadline). It does not implement the other 17 (authority owner,
resource, epoch, lease, ACK epoch, lifecycle terminate ×2, verified+authorized
substitution, non-substitutable authority, sensors, calibration, clock domain,
future skew, generic and mode-specific freshness, mode handover, input
completeness).

**What this shows:** the candidate implements the specified semantics. G0's 19
misses are checks G0 was defined not to have. **What it does not show:** any
benefit over a competent incumbent (G1 unspecified), or any physical effect.

## D3. M0 reconciliation

36 tests in `tests/test_m0_v11.py` plus the retained registry tests. Key
results: both package example manifests are schema-valid but **refused as runs**
(placeholder digests). A forged REJECTED→APPROVED edit with a re-hashed (valid)
ledger chain is caught only by signature re-verification. Replayed nonces are
refused across runs. Private key material is refused. Projected ledger events
validate against `ledger-event.schema.json`. `verified` stays unmapped.

## E3. Checkpoint (one attempt; reachable)

`rsj2003/libero-dynamic-paper-models` / `pi05/static_official` @
`ffd700087b34b734805ca5148e278a6a61fa5a8a`. Format: LeRobot safetensors, so the
PyTorch path applies. 3,616,758,006 parameters (3,497,036,912 BF16 +
119,720,608 F32), 7,472,958,200 weight bytes (6.96 GiB). `model.safetensors`
LFS SHA-256 `877b3ec1…0997ae`. Local streamed re-hash: see §F3.
Peak inference memory is **not measured** (ACCELERATOR_DEPENDENT).

## F3. Final verification

| Check | Result |
|---|---|
| Full suite, final | **599 passed, 4 failed, 1 xfailed**. 599 = 488 − 4 retired provisional-format approval tests + 36 (`test_m0_v11.py`) + 79 (`test_track_b.py`). Same 4 known failures, none new |
| Checkpoint local re-hash (streamed, 9 files incl. 7,473,096,344-byte `model.safetensors`) | **all 9 match** (8 against HF LFS/fetched hashes; `.gitattributes` has no LFS oid, local `11ad7efa…`) → `results/checkpoints/pi05_static_official_rehashed.json` |
