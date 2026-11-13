# Known issues (pre-existing; do not count as regressions)

Baseline on `main` @ `4ca4e71`, 2026-11-13: **599 passed, 4 failed, 1 xfailed** (`pytest tests -q`).

| Test | Cause (diagnosed in docs/eval_v1, results/diagnostics) |
|---|---|
| `tests/test_r05.py::test_g8_evidence_fingerprints_pass_on_committed_files` | locked N5 covariance scalar differs in the last float digit on this platform |
| `tests/test_r05.py::test_g8_fingerprints_fail_closed_on_drift` | same |
| `tests/test_r05.py::test_g5_unseatable_pair_leaves_filter_unchanged` | same |
| `tests/test_rollout.py::TestRunEpisode::test_capture_localization_error_within_clearance` | closest approach 0.50303 mm vs 0.5 mm capture threshold; episode seats without `capture_step` |

`AGENTS.md`'s "396 passed" is stale.

## Program findings (open, not regressions)

| Finding | Source | Status |
|---|---|---|
| `evaluation/replay.py` `replay()` reports a pose-check failure in `pose_check.pass` instead of raising; only the CLI exits 1 | M2.2 verifier | open; make it a precondition when a library caller appears |
| Replay pose check covers observed ticks only (3251 of 3651 in the M2 run). State in dropout ticks, and model changes that do not move `peg_tip`, are protected only by M0 SHA-256. Trust root is the M0 ledger; a forger who re-seals M0 is out of scope (Tier 3 signing) | M2.2 verifier planted defects a2, c2 | documented limit |
| Events are mapped to physics ticks by `round(t_s/dt)` (≤0.5 tick), not captured at emission; evidence captured via scoped `mock.patch` of `systems.HaplousRuntime` | M2.1 owner | `shortcut:` in `benchmarks/recording.py`; revisit when `systems.py` may expose evidence |
| Committed `hes qualify` exits 1: `non_interference` gate 0.786 < 0.95 threshold | M2.3 owner, baseline observation | pre-existing; M4 must not hide it |
| `haplous inspect`/`timeline` cannot read a recording; `haplous run` records no evidence events (interventions visible only in a recording's `events.jsonl`/`.rrd`) | M2.3 owner | open; candidate M5.1 improvement |
| Removing Haplous events (e.g. `temporal_hold_entered`) from `events.jsonl` and re-sealing M0 passes the M2 gate and replay; only `evidence.sh` with a trusted ledger (`trust_root`) catches it. Not yet listed in the skill's trust_root-only table | M2.3 verifier, planted defect | open; assigned to M3.3 |
| `benchmarks/recording.py` sets `ARROW_DEFAULT_MEMORY_POOL=system` via `os.environ.setdefault` at import: process-wide, inherited by child processes, no effect if pyarrow already loaded | M3.1 owner | accepted (measured fix for recorder RSS growth); revisit if another pyarrow user appears |
| **Scientific exclusion.** `slide_disturbance_no_recovery` and `slide_disturbance_recoverable_ceiling` outcomes depend on the 16-tile annulus mesh (`pegsim/scene.py:_table_tiles`): facet taps ~1.72–1.77 N. At 32/64 segments peak force falls to 1.29–1.39 / 1.17–1.18 N, `no_recovery` flips ABORTED→COMPLETE and `recoverable_ceiling` records 0 violations. Their results (including the committed M3 slide recording's force events) are pipeline evidence only, not scientific evidence | M3.1 verifier, mesh-resolution sweep | open; M4 excludes them unless re-run on a validated finer mesh in a new scoring epoch |
| `benchmarks/recording.py:276` hard-codes `insertion_scenarios() + sliding_scenarios()` | M3.1 verifier | assigned to M3.4 |
| Gate 9 plain comparator skips `grade_run` (recorded runs grade): overstates recorder RSS growth by ~0.65 MB at the long horizon (conservative) | M3.1 owner + verifier, measured | assigned to M3.4 |
| Replay pose tolerance 1e-9 m: a re-sealed 1e-10 m pose shift and edits to non-pose state columns (e.g. time) are not caught by the pose check | M3.1 verifier, planted defects | documented limit (trust root = M0 ledger) |
| `benchmarks/passthrough.py` `_Tap` compares commands the behaviour EMITS, not what the machine RECEIVES: dropping/altering a command the machine would reject is invisible (no effect on BareSystem outcomes, which ignore command outcomes) | M3.2 verifier, planted probe | M4 requirement before reusing the wrapper around HaplousSystem |
| **Truth leak.** Scenario id (e.g. `insert_dropout_large`, which names the fault) reaches the runtime via `intent.goal_description` (`benchmarks/heb/systems.py` `build_intent`) | M3.3 owner, structural canary detector | strict xfail `test_scenario_id_does_not_cross_execution_boundary`; fix assigned to M3.5 |
| **Capability negotiation does not check the behaviour's required command mode.** `negotiate()` refuses only an empty mode set: a machine offering only FORCE_CONTROLLED_DESCENT runs the cartesian-impedance insertion behaviour to COMPLETE; a str alias passes negotiation and fails later at `haplous/runtime.py:126` | M3.3 owner (measured) | behaviour-frozen area; operator decision OD-1 in PROGRAM_STATE.md; pinned by strict xfail |
| `hes.regression.paired_rate_delta` is not pairing-guarded: `tests/test_hes_smoke.py::test_regression_gate_and_paired_delta` relies on duplicate ids collapsing (3-arm list, 60 rows → 20 pairs) | M3.3 owner | open |
| Truth-leak detector matches canary VALUES: a scaled secret (`fault_at_s*1000`) or a secret formatted into a string (`f"t={fault_at_s}"` in `CommandOutcome.reason`) is not found | M3.3 verifier, planted leaks | documented limit |
| No negative test isolates the replay timestep guard (`evaluation/replay.py:114`): its case is also caught by the sim_time guard, so deleting the guard fails no test | M3.3 verifier | open, small |
| Capability negative test: only the empty-mode-set case is a clean refusal; the str alias is a crash after negotiation accepts it; descent-only is a strict xfail (OD-1) | M3.3 verifier | documented |
| A task family whose `scenarios` callable raises or returns non-list makes every lookup fail loudly, but the error does not name the family | M3.4 verifier | minor, open |
| **Pre-existing HES report drift on `main`.** `hes report results/hes/v3_hardening_qualification --check` fails on `main` @ `4ca4e71` and on the integration head alike: committed `docs/HES_V0_REPORT.md` carries digest `90e7aafe32f336ce`, current code computes `dc913cf8e9886bfd`. Earlier "`--check` matches dc913cf8" statements in this program were circular (rendered to `--out` then checked the same file); the before/after byte-identity comparisons of rendered output remain valid | coordinator, reproduced on a detached checkout of main | open; M4 regenerates under an explicit scoring epoch |
| `scripts/measure_fleet.py` crashes: `AttributeError: 'InterventionClassification' object has no attribute 'epoch'` (:166) | M3.6 fix-round owner; also crashes at `c9997c6` (pre-OD-1) | pre-existing, open |
| `results/counterfactual_internal_summary.json` is stale vs current code: 1e-11 force drift and `mediated_reason` on `insert_stale_persistent` (`pre_execution_state_invalid` now, null in the file); identical before and after OD-1 | M3.6 fix-round owner | pre-existing, not regenerated (frozen evidence) |
| robosuite / mani_skill not installed: external-policy contracts checked by direct `negotiate()` against their tasks' capabilities only, no episodes run | M3.6 fix-round owner | environment limit |
| `command_mode_admission` crashes (AttributeError) instead of refusing when a BEHAVIOUR declares a str in `required_command_modes` (developer-side error; machine-side str alias is refused correctly) | M3.6 verifier | minor, open |
| Runtime now builds the behaviour at submit: a factory runs even for refused submits, and a raising factory raises at submit instead of execute; side-effect counters on factories (e.g. `created` lists in measure_fleet / measure_counterfactual_internal) shift | M3.6 verifier | accepted consequence of OD-1 design |
| A machine offering only CARTESIAN_IMPEDANCE is now refused for insertion and slide (both declare {CI, FCD}); slide's FCD is the stricter choice for a force-hold phase | M3.6 owner + verifier | by design (OD-1); relevant for ManiSkill-style machines |
| **Committed HEB scenarios do not discriminate Haplous from a competent incumbent:** haplous_mediated and g1_incumbent agree on all 27 (19 COMPLETE / 5 ABORTED / 3 REFUSED), same guard class per outcome; existing `population()` stays below the 0.5 s hold budget (dropout ≤0.2 s, stale 0.1–0.2 s, rejection ≤0.02 s) | M4.1 verifier | drives M4.2a stress population; not evidence for or against Haplous |
| Dynamic truth-leak detector cannot see a system reading `Scenario` fields in its own code (planted read in G1 went undetected); G1 design doc overclaimed coverage | M4.1 verifier | assigned to M4.1b |
| G1 is mildly strong: it uses the machine's qualification record (`expected_duration_s`, `max_initial_offset_m`) for free, and has authorized budgeted retry many real incumbents lack | M4.1 verifier | disclosed; favours G1 (conservative for Haplous claims) |
