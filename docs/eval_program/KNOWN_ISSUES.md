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
