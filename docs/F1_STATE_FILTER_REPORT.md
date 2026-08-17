# F1 state filter report

Date: 2026-08-16

Repository state entering F1a-rev2: `8590893` (`127 passed, 1 xfailed`)

F1a-rev2 implementation and deterministic evidence: `7ddb18c`

Scope: **F1a and F1a-rev2 only**. F1b, F1c, the SPC chart, MES/OPC UA,
PackML transitions, recovery demo, and viewer were not started.

## 1. Outcome

F1a-rev2 replaces per-probe production updates with one standard Kalman update
per antipodal pair while retaining the per-probe API for incomplete pairs and
future single-channel characterization. The pair observation is rank three at
every tested bearing, so `dc` is algebraically distinct from fixture position
inside one pair. No weighted-least-squares reset or prior discard was added.

The simulation feedforward correction remains per probe. For direction `u`,
the plus bias is `-penetration*u`, the minus bias is `+penetration*u`, and the
four-vector is corrected before the batch innovation is formed. The batch uses
`R_pair=block_diag(R,R)` because no cross-correlation has been measured.

The two probes are not treated as simultaneous. The covariance timeline is one
prediction before the plus observation, a second prediction between plus and
minus, then one atomic four-row gate and update. Because the random-walk state
transition is identity and conditioning waits for the complete pair, the code
applies the equivalent local prior `P+2Q` at that update. If the gate rejects
the pair, neither prediction is committed to persistent `x` or `P`.

The recalibrated placeholder process values are
`q_h=(0.2 µm/prediction interval)²` and
`q_c=(0.001 µm/prediction interval)²`. T7 remained consistent: mean NEES was
`3.060536`, inside `[0.215795, 9.348404]`. The required stop condition was not
triggered.

A scheduled 10 µm tool change now raises `P_dc` to a caller-supplied prior
variance. The next pair separated the step in one pair, with `dc` error below
`0.000001 µm`, negligible fixture motion, and `d²=0.000100`, below `13.816`.
Without that commanded event, the first-step values were `81.086976` for the
per-probe path and `197.993340` for the pair path; both correctly gated a step
that contradicted the low-wear prior.

Repeated rejection can no longer leave the filter permanently frozen. The
third consecutive gate emits `ABORT`, returns the last good state and all three
gated innovations, reinitializes `h` from the current corrected observation,
inflates `P_h`, and preserves `dc`. T10 reached `ABORT` on the fourth fast-ramp
pair and the following synthetic pair updated normally. `ABORT` is the filter
outcome intended to map to PackML `Aborted`; no PackML transition or external
publisher was built here.

### 1.1 Patch to the rev-2 work order

The pre-edit check exposed that the original ramp tests contradicted the new
Q values. The corrected executable specification is:

| Test | Rev-2 correction |
|---|---|
| T4 | Fixture ramp is `0.2 µm/cycle`, the rate represented by `sqrt(q_h)` |
| T4b | Fixture ramp at `2.0 µm/cycle`, 10× design rate, must gate |
| T5a | `dc` ramp is `0.001 µm/cycle` for 2,000 pairs, totaling 2 µm |
| T5d | `dc` ramp at `0.010 µm/cycle`, 10× design rate, must gate |
| T10 | Three consecutive gates emit `ABORT`, expose diagnostics, and reinitialize fixture tracking |

These rates make Q and the fixed gate an explicit silent-adaptation boundary.
They are product placeholders, not empirical plant limits.

## 2. Full measured configuration

F1a-rev2 contains no physics rollout. No MuJoCo model, contact solver,
controller, spiral, or physical trajectory participates in these measurements.

| Group | Value |
|---|---|
| State | `x=[h_x,h_y,dc]`, meters; `h` is world-frame fixture position and `dc=c_true-c_cad` |
| Initial state | `[0,0,0] m` |
| Initial covariance | `diag((1 mm)²,(1 mm)²,(1 mm)²)`; broad synthetic prior |
| Process model | Random walk, identity transition, `Q=diag(q_h,q_h,q_c)` per prediction interval |
| `q_h` | `(0.2 µm)² = 4.0e-14 m²`; implies the placeholder rate `0.2 µm/cycle` |
| `q_c` | `(0.001 µm)² = 1.0e-18 m²`; implies the placeholder rate `0.001 µm/cycle` |
| Variance ratio | `q_h/q_c=40,000`; the ratio, not either absolute scale, separates slow states on the per-probe path |
| Per-probe observation | `H=[[1,0,u_x],[0,1,u_y]]` |
| Pair observation | `H_pair=[H(+u);H(-u)]`, four rows by three columns |
| Synthetic `R` | `(1 µm)² I`; independent Gaussian axes |
| Pair `R` | `block_diag(R,R)`; no measured cross-correlation is available |
| Pair timing | predict, acquire plus, predict, acquire minus, one batch update; local prior is `P+2Q` |
| Simulation penetration | `2.9630093418717097 µm`, from `results/n4_facet_convergence_summary.json`, `by_n["64"].penetration_m_from_pooled_minimum` |
| Hardware penetration | `0.0 m`; accepted by the same constructor, not measured here |
| Per-probe bias | `-penetration*u`; subtract before innovation |
| Pair biases | plus `-penetration*u`, minus `+penetration*u`; subtract the stacked four-vector |
| Gate | reject only when `d²=nuᵀS⁻¹nu > 13.816` |
| Pair gate note | The work order retains the numeric threshold `13.816`; it is the 99.9% two-DOF value for a single probe, not a newly calibrated four-DOF percentile |
| Tool-change event | Raise `P_dc` to caller-supplied prior variance; tests use the configured broad `1.0e-6 m²` prior |
| Consecutive gate limit | 3, constructor-configurable; first two return `SALVAGE`, third returns `ABORT` |
| Accepted covariance | Solve-based gain, Joseph update, explicit symmetrization |
| Bearing schedule | `theta_k=k*pi*(3-sqrt(5))`, except fixed-bearing tests |
| Statistics | Posterior/configured sigma values are not sample standard deviations; `ddof` is not applicable. NEES is an arithmetic mean over accepted pre-latch samples. |

Both Q values have no empirical basis. Their implied rates are written in
µm/cycle so a customer discussion can replace the actual product numbers:
how fast the fixture and tooling move in that cell, and at what rate the line
should stop rather than silently adapt.

## 3. Evidence and reproduction

Durable evidence:

- Filter: [`../pegsim/filter.py`](../pegsim/filter.py)
- Permanent tests: [`../tests/test_filter.py`](../tests/test_filter.py)
- Deterministic driver: [`../scripts/measure_f1a_state_filter.py`](../scripts/measure_f1a_state_filter.py)
- Derived summary: [`../results/f1a_filter_summary.json`](../results/f1a_filter_summary.json)

Commands from the repository root:

```bash
uv run pytest -q
uv run pytest tests/test_filter.py -q -x
uv run python scripts/measure_f1a_state_filter.py
uv run pytest -q
```

The first full suite was the unmodified `8590893` baseline. The driver performs
no physics and overwrites only `results/f1a_filter_summary.json`. The final full
suite is recorded in Section 8.

| Evidence state | Commit |
|---|---|
| F1a entry state and original T5 falsification | `8590893` |
| Pair batching, Q recalibration, event prior, abort behavior, tests, and measurements | `7ddb18c` |
| Exact report text | `git log -1 --oneline -- docs/F1_STATE_FILTER_REPORT.md` |

## 4. Engineering decisions

### D1. Batch the physical measurement unit without discarding the prior

**Problem.** A single probe supplies two equations for three states. Updating
each probe separately loses the antipodal structure and makes `h` versus `dc`
allocation depend on the process prior.

**Decision.** Stack the plus and minus probes into `H_pair`, correct both known
biases, use `block_diag(R,R)`, and perform one standard Kalman update. Retain
the original per-probe update method for incomplete pairs and F1b channel work.

**Defense.** `rank(H_pair)=3` and condition number is
`1.0000000000000002` at 0°, 30°, 45°, and 90°. A commanded 10 µm `dc` step was
within 1 µm after one pair while `||h||` remained below `2e-16 µm`.

**Rejected alternatives.** Per-probe production updates throw away the
rank-three batch. Measurement-only weighted least squares discards the prior
and its noise averaging. Special-case step classification duplicates a
scheduled cell event with weaker information.

### D2. Represent the time between antipodal probes

**Problem.** Treating the two probes as simultaneous hides fixture motion that
appears directly in the plus-minus contrast and therefore contaminates `dc`.

**Decision.** Represent one prediction before plus and another between plus and
minus, then condition once. Keep the filter model common-state and measure the
unmodelled intra-pair motion explicitly in T9.

**Defense.** Injected along-bearing motion of 0.2, 2, and 20 µm produced `dc`
errors of -0.100000, -1.000000, and -9.999995 µm: a measured ratio of
`-0.49999975` at every level, confirming the predicted half-drift coupling.

**Rejected alternatives.** Simultaneous observations make the probe interval
invisible. An augmented two-time-state filter is not justified until hardware
establishes actual interval and drift statistics.

### D3. Make Q the explicit adaptation-rate statement

**Problem.** The earlier `q_h=(20 µm/cycle)²` asserted roughly 20 µm fixture
motion per cycle. Replacing it with 0.2 µm while retaining 5 µm fixture and
0.05 µm wear ramps made the tests 25× and 50× faster than their own models.

**Decision.** Keep `q_h=(0.2 µm)²` and `q_c=(0.001 µm)²`; set T4 and T5a to
those implied design rates; add 10× mirror tests that must gate. Do not tune Q
against NEES or the desired test outcome.

**Defense.** T4 had zero gates and a final `h_x` lag of -0.904999 µm. T5a had
zero gates, maximum `dc` lag 0.481514 µm or 0.680283 innovation sigma, and
negligible fixture leakage. T4b first gated at probe 5. T5d first gated at pair
584. T7 independently remained inside its prescribed consistency band.

**Rejected alternatives.** Keeping the old ramp rates contradicts Q. Marking
the undecided rates as expected failures hides a product decision. Increasing
Q solely to pass the tests is circular and broadens silent adaptation.

### D4. Treat a tool change as a commanded covariance event

**Problem.** A 10 µm tooling step contradicts a filter that has learned very
small `P_dc`. Algebraic observability identifies which state explains the pair,
but it does not by itself make an improbable step pass the NIS gate.

**Decision.** Add `inflate_clearance_variance(prior_variance_m2)`. The caller
invokes it on the cycle after the scheduled tool change. The method can only
raise `P_dc`; it cannot silently reduce learned uncertainty.

**Defense.** Without the event, first-step d² was 81.086976 per probe and
197.993340 per pair, both above 13.816. With the event it was 0.000100 on both
paths, and the pair recovered `dc` in one update.

**Rejected alternatives.** Automatic step detection confuses a known schedule
with fault inference. Raising `q_c` continuously sacrifices still-state noise
averaging. Resetting the whole state discards valid fixture knowledge.

### D5. Escalate repeated gates instead of freezing forever

**Problem.** A persistent model mismatch previously produced endless rejected
measurements while the last state silently became stale.

**Decision.** Count consecutive gates. The third returns `ABORT`, includes the
last good state and bounded innovation history, reinitializes `h` from the
current corrected measurement, adds the initial fixture prior covariance to
`P_h`, zeros stale `h`/`dc` cross-covariance, and retains `dc`.

**Defense.** After a 100-pair warm-up, T10's 2 µm/pair fixture ramp emitted
`ABORT` on pair 4. Its history contained exactly three innovations, reset
fixture error was zero, and the next synthetic pair returned `UPDATED` with
fixture error `0.000001 µm`.

**Rejected alternatives.** Infinite gating is silent estimator death. Updating
through every outlier defeats the gate. Reinitializing `dc` during a fixture
model mismatch discards independently identifiable tooling information.

### D6. Preserve the T5 falsification

**Problem.** Original T5 required Q asymmetry alone to assign a simultaneous
10 µm step to `dc`. Under `q_h/q_c=4e8`, the prior correctly made movement in
`h` much cheaper and produced a 9.925 µm fixture excursion.

**Decision.** Keep T5 as a strict `xfail`, label it `SUPERSEDED`, and retain the
original `8590893` measurement in this report. Pair batching, not Q tuning,
supplies the missing state separation.

**Defense.** A random-walk process-noise ratio separates slow motion from fast
motion. It cannot separate a step from another step when both hypotheses fit
the observation; the covariance prior chooses the state allowed to move.

**Rejected alternatives.** Deleting the test loses a real model-limit finding.
Reversing Q or manufacturing a pass changes the product statement rather than
fixing observability.

## 5. Measurements

Distances are µm unless stated otherwise. Values are deterministic for the
committed seeds and configuration. No table reports a sample standard
deviation, so `ddof` is not applicable.

### 5.1 T1–T4 per-probe path

| Test | Measurement | Result |
|---|---:|---|
| T1 condition number, 200 golden-angle probes | 1.005078 | confirmed |
| T1 final error `[h_x,h_y,dc]` | `[-0.111216,+0.073423,-0.000949] µm` | all within posterior 3 sigma |
| T2 fixed-bearing condition | infinity | per-probe non-observability confirmed |
| T2 unresolved state-error norm | 70.710678 µm | confirmed |
| T3 200 µm fixture-step d² | 32732.223959 | `SALVAGE`; `x,P` unchanged |
| T4 rate | 0.2 µm/cycle | Q-implied design rate |
| T4 final `h_x` truth / estimate | 39.800000 / 38.895001 µm | zero gates; within 3 posterior sigma |
| T4b rate | 2.0 µm/cycle | 10× design rate |
| T4b first gate | probe 5 | fault boundary confirmed |

T2 applies only to the retained per-probe path. One pair is rank three.

### 5.2 T5 falsification, wear, step, and gate behavior

| Test/path | Measurement | Result |
|---|---:|---|
| Historical T5 at `8590893` | max `||h||=9.925217 µm` | **falsified**, now superseded |
| T5a wear rate and horizon | 0.001 µm/cycle for 2,000 pairs | final truth 2.000000 µm |
| T5a final / maximum `dc` lag | -0.481514 / 0.481514 µm | 0.680283 max innovation sigma |
| T5a maximum `||h||` | `6.79e-17 µm` | below 2 µm |
| T5a gates / aborts | 0 / 0 | design-rate adaptation confirmed |
| T5b pairs to separation | 1 | within five-pair requirement |
| T5b error after five pairs | `-0.000001 µm` | below 1 µm |
| T5b maximum `||h||` | `1.85e-16 µm` | below 2 µm |
| T5d wear rate | 0.010 µm/cycle | 10× design rate |
| T5d first gate | pair 584 | fault boundary confirmed |

| T5c path | Commanded event | d² at 10 µm step | Threshold | Outcome |
|---|---|---:|---:|---|
| Per probe | no | 81.086976 | 13.816 | `SALVAGE` |
| Pair batched | no | 197.993340 | 13.816 | `SALVAGE` |
| Per probe | yes | 0.000100 | 13.816 | `UPDATED` |
| Pair batched | yes | 0.000100 | 13.816 | `UPDATED` |

The commanded pair path satisfies the T5c stop condition. The larger
uncommanded pair d² is not a batching defect: the antipodal contrast makes the
unexpected `dc` change more explicit against the learned low-wear prior.

### 5.3 T6–T8 numerical consistency and observability

| Test | Measurement | Result |
|---|---:|---|
| T6 probes | 5,000 | completed |
| T6 maximum `|P-Pᵀ|` | `0.0 m²` | symmetric |
| T6 minimum eigenvalue | `1.005329e-15 m²` | positive-definite |
| T7 accepted pre-latch samples | 17,462 | 21 gated runs, 79 complete runs |
| T7 mean NEES | 3.060536 | inside `[0.215795,9.348404]` |

| T8 bearing | `rank(H_pair)` | Condition number |
|---:|---:|---:|
| 0° | 3 | 1.0000000000000002 |
| 30° | 3 | 1.0000000000000002 |
| 45° | 3 | 1.0000000000000002 |
| 90° | 3 | 1.0000000000000002 |

### 5.4 T9 intra-pair drift sensitivity

Drift was injected along the probe bearing between plus and minus. The true
`dc` was zero.

| Intra-pair fixture drift (µm) | Estimated `dc` error (µm) | Error/drift |
|---:|---:|---:|
| 0.2 | -0.100000 | -0.49999975 |
| 2.0 | -1.000000 | -0.49999975 |
| 20.0 | -9.999995 | -0.49999975 |

Prediction that the `dc` error is approximately half the intra-pair drift:
**confirmed**. This table quantifies sensitivity but does not set an acceptable
probe interval; hardware drift and timing are still absent.

### 5.5 T10 repeated-gate escalation

| Quantity | Measurement |
|---|---:|
| Fast fixture ramp | 2.0 µm/pair |
| `ABORT` | fourth pair after ramp start |
| Consecutive gates at abort | 3 |
| Returned innovation-history length | 3 |
| Fixture error immediately after reinitialization | 0.0 µm |
| Next pair outcome | `UPDATED` |
| Next-pair fixture error | 0.000001 µm |

The returned `last_good_state_m` is the publication payload before reset. The
filter does not publish it itself.

## 6. Predictions versus measurements

| Prediction | Measurement | Status |
|---|---|---|
| A pair is rank three at every bearing | rank 3 and condition 1.0 at four representative bearings | **confirmed** |
| Pair batching separates a commanded 10 µm `dc` step within five pairs | one pair; negligible `h` motion | **confirmed** |
| Commanded pair step stays below 13.816 | d²=0.000100 | **confirmed** |
| Q asymmetry alone separates simultaneous steps | historical max `||h||=9.925217 µm` | **falsified**, superseded |
| Design-rate fixture drift adapts | zero gates; final lag 0.904999 µm | **confirmed** |
| 10× fixture drift gates | first gate at probe 5 | **confirmed** |
| Design-rate wear adapts without fixture leakage | zero gates; max lag 0.680283 innovation sigma | **confirmed** |
| 10× wear gates | first gate at pair 584 | **confirmed** |
| New `q_h` remains NEES-consistent | mean 3.060536 inside prescribed band | **confirmed** |
| Intra-pair drift creates half-sized `dc` error | ratios all -0.49999975 | **confirmed** |
| Sustained mismatch cannot gate forever | `ABORT` on fourth pair; next pair updates | **confirmed** |

## 7. What this does not establish

- `q_h` and `q_c` have no empirical basis. Their implied 0.2 and 0.001
  µm/cycle rates are customer-facing product placeholders.
- `R=(1 µm)²I` is a placeholder until F1b. `R_pair` assumes zero probe
  cross-correlation only because none has been measured.
- No statistic here is a hardware measurement uncertainty. All observations
  and truth are synthetic.
- Nothing in F1a-rev2 touched physics. `pegsim/filter.py` still has a permanent
  AST test prohibiting a MuJoCo import.
- A random-walk model tracking a ramp necessarily carries lag. Raising Q enough
  to reduce lag also under-averages R while the fixture is still. If hardware
  shows that lag is binding, the upgrade path is a constant-velocity state such
  as `[h,dc,h_dot,dc_dot]`; it was not built here.
- The fixed numeric pair gate is not a calibrated four-dimensional NIS
  percentile. Hardware false-positive tolerance remains undecided.
- T9 does not establish a maximum acceptable probe interval. It supplies the
  transfer ratio needed after hardware provides drift-versus-time data.
- The 1 mm covariance used for commanded tool changes and abort
  reinitialization is a synthetic prior, not a commissioned-cell value.
- Returning `ABORT` does not implement PackML `Aborted`, external publication,
  operator clearance, or recovery transitions.
- TCP bias, bore harmonics, long-lived observation-history retention, and
  post-abort production policy remain uncommissioned.
- F1b error injection, F1c physics wiring, SPC, MES/OPC UA, the recovery demo,
  and the viewer remain unstarted.

## 8. Tests added and verification

T5 remains a strict expected failure marked `SUPERSEDED`; an unexpected pass
still forces review. Parameterized T8 and T9 cases count as separate pytest
cases.

| Test | Final status |
|---|---|
| No MuJoCo import | pass |
| T1 static truth | pass |
| T2 per-probe fixed bearing | pass |
| T3 fixture step gate | pass |
| T4 design-rate fixture ramp | pass |
| T4b 10× fixture ramp | pass |
| T5 superseded simultaneous-step claim | strict xfail |
| T5a design-rate wear | pass |
| T5b commanded pair step | pass |
| T5c commanded gate behavior | pass |
| T5d 10× wear | pass |
| T6 Joseph covariance | pass |
| T7 NEES | pass |
| T8 pair observability, four bearings | pass |
| T9 drift sensitivity, three levels | pass |
| T10 repeated-gate abort and recovery | pass |

Baseline full suite at `8590893`: **127 passed, 1 xfailed**.

Targeted rev-2 filter suite: **20 passed, 1 xfailed**.

Final full suite after report self-review: **140 passed, 1 xfailed**. This was
the only post-edit full-suite execution.

## 9. Commit traceability

| Requirement | Files | Commit |
|---|---|---|
| Pair observation, feedforward, two predictions, batch update, retained per-probe path | `pegsim/filter.py` | `7ddb18c` |
| Q recalibration and tool-change covariance event | `pegsim/filter.py`, `tests/test_filter.py` | `7ddb18c` |
| Consecutive-gate `ABORT`, last-good state, innovation history, fixture reset | `pegsim/filter.py`, `tests/test_filter.py` | `7ddb18c` |
| T4b, T5a–T5d, T8–T10 | `tests/test_filter.py` | `7ddb18c` |
| Reproducible deterministic measurements | `scripts/measure_f1a_state_filter.py`, `results/f1a_filter_summary.json` | `7ddb18c` |
| Original T5 falsification | `docs/F1_STATE_FILTER_REPORT.md` at entry | `8590893` |
| Rev-2 report and remaining risks | `docs/F1_STATE_FILTER_REPORT.md` | use path-specific `git log` command after commit |

## 10. Remaining risks

1. The two Q-implied stop rates are guesses. Customer fixture and tool-wear
   measurements must replace them before product claims are made.
2. The single-probe 99.9% threshold is reused numerically for the four-row pair
   by work-order decision; its pair false-positive rate is not commissioned.
3. T10 prepares filter state after `ABORT`, but real production must define who
   clears the abort and whether the reinitialized state is accepted, inspected,
   or discarded.
4. Pairing converts along-bearing intra-pair fixture motion into half-sized
   `dc` error. Hardware must set the maximum probe interval from actual drift.
5. Random-walk ramp lag may become binding after R is measured. A
   constant-velocity model is the identified upgrade path, not current scope.

F1a-rev2 is complete. Work stops here before F1b.
