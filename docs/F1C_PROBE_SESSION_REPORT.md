# F1c probe session report

Date: 2026-09-08

Repository state entering F1c: `e31ea85` (N5 evidence committed). F1c
implementation and measurements: `828ceb2`.

Scope: physics integration of the audited `StateFilter` through the
N5-validated antipodal probe primitive, with lifecycle ownership, reset
semantics, sensor-side corruption hooks, and the first measured pair
covariance. R05/R06 (comparative execution benchmark) are NOT started.

## 1. Outcome

`pegsim/probing.py` now owns the full observation path:

```
run_episode (search-disabled probe) -> measured settled tip
    -> fixed TCP calibration bias  -> encoder quantization
    -> nominal-clearance reduction -> StateFilter.update_pair
```

The filter consumes ONLY reduced estimates; ground truth appears solely in
evaluation fields (Rule Zero preserved, asserted by the reset-leak test).
Physics is stepped only by `run_episode`. One new evidence-derived input
replaces a placeholder the F1 report explicitly deferred: the pair
measurement covariance now uses the MEASURED N5 side-error model (strong
anticorrelation, radial −0.86 / tangential −0.98) instead of
`block_diag(R, R)`, via a default-None override on `update_pair` that
leaves the audited F1a path byte-identical.

Measured on real physics at the N5 n=64 scene (six golden-bearing pairs per
session, deterministic):

| Session | Outcomes | Final fixture error | Final dc state | NEES range |
|---|---|---|---|---|
| clean | 6/6 `UPDATED` | **0.024 µm** | +0.069 µm (truth 0) | 0.000–0.486 |
| 3 µm TCP bias + 2 µm quantization | 4/6 `UPDATED`, 2/6 `SALVAGE` | **3.000 µm** (tracks the bias exactly) | +0.36 µm | 226–407 |

The clean session validates the integrated chain end-to-end: NIS ∈
[0.007, 9.09], all below the 13.816 gate, mostly O(1) — the filter is
statistically consistent with physics once the measured covariance replaces
the independent-side placeholder. The corrupted session demonstrates the
R03 confidently-wrong condition on real physics: the filter converges to the
sensor bias with SHRINKING claimed uncertainty (NEES 226→407, the
`confidently_wrong` flag true from the first pair), which is exactly the
silent-adaptation hazard the F1 process priors exist to bound.

### 1.1 The placeholder R_pair was measurably wrong — first integration finding

The first integration attempt gated a physically perfect pair at
NIS = 38.5 (threshold 13.816). Diagnosis: the pair-contrast direction (the
left-null of `H_pair`) receives NO contribution from the broad prior, so
its innovation covariance is R alone; with `block_diag(R, R)` the measured
anticorrelated contrast reads as a ~6σ violation. N5 had measured the
anticorrelation; the F1 report had explicitly deferred the cross-covariance
until it was measured. The fix supplies the measured 4×4 covariance per
pair (assembled in the commanded radial/tangential frame and rotated by the
bearing — a world-frame average washes the structure out). The audited
filter semantics are unchanged: the override is optional and defaults to
the F1a construction.

## 2. Full measured configuration

| Group | Value |
|---|---|
| Scene | canonical task at `n_segments=64` (194 geoms); probe = N2/N5 search-disabled primitive at 2×nominal clearance |
| Penetration bias | 1.1423 µm, loaded from committed `results/n5_antipodal_summary.json` (dc=0 mean full difference / 2); the N4 pooled-minimum 2.963 µm is REFUTED for pair use (N5 report §6) |
| Pair covariance | measured from committed N5 dc=0 raw, aligned by pair index, per side, mean-removed; radial σ 0.751 µm, tangential σ 1.686 µm, side correlations −0.859 / −0.981; assembled per pair in the commanded frame and rotated to world |
| Process priors | q_h = (0.2 µm)², q_c = (0.001 µm)² — F1a-rev2 placeholders, unchanged and untuned |
| Gate | 13.816 (2-DOF 99.9% value reused numerically for the 4-row batch, as documented and not commissioned) |
| Initial state/covariance | [0, 0, 0] m / diag((1 mm)²) — F1a synthetic prior |
| Corruption (corrupted session) | fixed TCP bias 3 µm translation + 0.01 rad yaw, drawn once per session; 2 µm mid-rise encoder quantization on the tip |
| Schedule | 6 pairs, bearings `k·golden_angle`, k=0…5, per session |
| Determinism | no stochastic element; seed null |

## 3. Evidence and reproduction

- Module: [`../pegsim/probing.py`](../pegsim/probing.py)
- Permanent tests: [`../tests/test_probing.py`](../tests/test_probing.py) (12 tests, one real-physics)
- Driver: [`../scripts/measure_f1c_probe_session.py`](../scripts/measure_f1c_probe_session.py)
- Derived summary: [`../results/f1c_session_summary.json`](../results/f1c_session_summary.json)

```bash
uv run pytest tests/test_probing.py -q
uv run python scripts/measure_f1c_probe_session.py
uv run pytest -q
```

Full suite at `828ceb2`: **190 passed, 1 xfailed**.

## 4. Lifecycle ownership (§14.1 contract)

- The session owns the filter, the reusable model/data pair, and reset.
- `run_episode` resets all physics state per episode (`mj_resetData`).
- `reset()` reinitializes the estimator to its configured prior.
- The mandated leak test runs a real physics pair, disturbs the estimator
  through a 300 µm inconsistent feed, resets, re-runs the same pair, and
  requires state/covariance/estimates identical to a fresh session
  (rtol 1e-12). Measured: exact match.
- An unseated probe pair raises `RuntimeError` and leaves the filter
  untouched — a failed probe never contaminates estimator state.

## 5. Failure scenarios exercised

| Scenario | Mechanism | Result |
|---|---|---|
| Valid measurements | clean physics session | 6/6 `UPDATED`, final h error 24 nm |
| Bad initial estimate | 300 µm inconsistent feed then reset | recovered exactly (leak test) |
| One corrupted measurement | 200 µm fixture step | 1st pair `SALVAGE`, state/covariance unchanged |
| Repeated outliers | 3 consecutive 200 µm steps | `SALVAGE`, `SALVAGE`, `ABORT`; fixture reinitialized to the corrected observation, dc preserved (F1a-rev2 semantics unchanged) |
| Systematic sensor bias | 3 µm TCP bias, real physics | filter converges to the bias; NEES 226–407; confidently-wrong flagged; 2/6 pairs gated |
| Reset | mandated lifecycle test | no state leak across episode boundary |

The corrupted-session result is the R03 analysis hook operating as
specified: low estimator uncertainty + high actual error, detected
explicitly rather than silently adapted away. It also demonstrates the
hazard the F1a process priors exist to bound: a 3 µm systematic bias is
absorbed by the random-walk model within a few pairs.

## 6. What this does not establish

- The observation path covers deliberate probing only. Natural-search
  measurement (N3) is unmeasured; no claim that probing is mandatory.
- The gate threshold remains the documented 2-DOF 99.9% value; a calibrated
  4-DOF pair percentile is not commissioned.
- The measured covariance describes the N5 n=64 population with its facet
  structure averaged over bearings; it is an effective R for that scene,
  not a hardware uncertainty.
- The corruption model here is sensor-side only (TCP bias, quantization);
  truth-side part variation enters through scene construction as in N5.
- No execution behavior consumes the estimator state yet (R05/R06).
- `q_h`/`q_c` remain customer-facing placeholders with no empirical basis.

## 7. What F1c changes for downstream work

1. `StateFilter.update_pair` gained an optional full 4×4 measurement
   covariance override (default None → audited F1a behavior, byte-identical,
   all 21 existing filter tests unchanged).
2. The evidence-loaded constants for the n=64 scene are:
   penetration bias 1.1423 µm; pair covariance per `load_n5_pair_measurement_model()`.
3. The confidently-wrong detection hook is demonstrated and committed
   (`results/f1c_session_summary.json`), ready for the R03 corruption sweeps.
