# F1 state filter report

Date: 2026-08-16

Repository state entering F1a: `c64dc55` (`120 passed`)

F1a implementation and synthetic evidence: `ed4f40f`

Scope of this report: **F1a only**. F1b error injection and F1c physics wiring
were not started because the F1a discrimination requirement was falsified.

## 1. Outcome

The state filter core is implemented without a MuJoCo import. It uses the
specified state, penetration feedforward correction, random-walk prediction,
99.9% two-dimensional NIS gate, solve-based Kalman gain, Joseph covariance
update, covariance symmetrization, and recent-bearing observability diagnostic.
An outlier returns `SALVAGE` before either persistent state or covariance is
changed.

Six requested behaviors were confirmed. Golden-angle probes converged;
fixed-bearing probes remained rank deficient; a 200 µm fixture step gated at
the first post-step probe with d²=99.502; a 5 µm/cycle fixture ramp produced no
gate; covariance remained exactly symmetric and positive-definite over 5,000
probes; and the time-averaged NEES was 2.913, inside the requested 3-DOF 95%
interval `[0.215795, 9.348404]`.

**F1a does not pass as a product discriminator.** With the specified working
values `q_h=(20 µm/cycle)²` and `q_c=(1 nm/cycle)²`, a 10 µm clearance step at
probe 100 moved the fixture estimate by as much as 9.925 µm. At probe 199 the
filter estimated only 5.000 µm of the 10 µm clearance change and retained
4.978 µm of fixture error. This falsifies T5's required less-than-2 µm fixture
motion. The direction of the process-noise asymmetry makes newly observed
motion cheap in `h` and extremely expensive in `dc`; rotating bearings recover
the spatial distinction only slowly. At 1,000 probes the final fixture error
fell to 0.990 µm and `dc` reached 9.005 µm, but the original 9.925 µm transient
remained and therefore still failed T5.

The specified `q_h/q_c` values were not reversed, inflated, or supplemented
with special-case classification logic to manufacture a pass. F1b and F1c are
stopped pending a decision on the process model or on a permitted multi-probe
classification/update policy.

## 2. Full measured configuration

F1a contains no physics rollout. No MuJoCo model, contact setting, solver,
controller, spiral, PackML state, MES/OPC UA path, recovery behavior, chart, or
viewer participates in these measurements.

| Group | F1a value |
|---|---|
| State | `x=[h_x, h_y, dc]`, all entries in meters; `h` is world-frame fixture position and `dc=c_true-c_cad` |
| Observation | `z=hole_est`; `H=[[1,0,cos(theta)],[0,1,sin(theta)]]`; known bias `b=-penetration*[cos(theta),sin(theta)]`; corrected observation `z_corr=z-b` |
| Initial state | `[0, 0, 0] m` |
| Initial covariance | `diag((1 mm)², (1 mm)², (1 mm)²)`; deliberately broad synthetic prior, not an empirical production prior |
| Process model | `x[k+1]=x[k]+w`; `Q=diag(q_h,q_h,q_c)` |
| Working `q_h` | `(20 µm/cycle)² = 4.0e-10 m²/cycle`; placeholder pending a customer drift measurement or the hardware run |
| Working `q_c` | `(1 nm/cycle)² = 1.0e-18 m²/cycle`; no empirical basis |
| Process variance ratio | `q_h/q_c=4.0e8`; this ratio is the only intended fixture/wear separator; no other filter logic distinguishes the states |
| Synthetic `R` | `(1 µm)² I = diag(1.0e-12, 1.0e-12) m²`; independent Gaussian axes |
| Simulation penetration | `2.9630093418717097e-6 m`, read from `results/n4_facet_convergence_summary.json`, `by_n["64"].penetration_m_from_pooled_minimum` |
| Hardware penetration configuration | `0.0 m`; the same filter code accepts this value, but no hardware data were used here |
| Gate | `d²=nuᵀ S⁻¹ nu`; threshold `13.816`, the requested 99.9th percentile for chi-square with 2 DOF; reject only when `d² > 13.816` |
| Accepted update | Gain computed with `numpy.linalg.solve`; covariance updated in Joseph form and then symmetrized |
| Rejected update | Outcome `SALVAGE`; returned record includes innovation, innovation covariance, d², threshold, state, and covariance; persistent `x` and `P` remain bitwise unchanged |
| Probe schedule | `theta_k=k*pi*(3-sqrt(5))` unless T2 fixes `theta=0` |
| Observability | Condition number of stacked recent observation matrices; explicit infinity for rank below 3; runtime warning at rank failure or condition at least `1/sqrt(float64 epsilon)` |
| T1 | 200 probes; truth `[42,-31,11] µm`; NumPy seed 481516 |
| T2 | 200 probes at zero bearing; truth `[50,0,-50] µm`, chosen so observed `h_x+dc=0` |
| T3 | 200 µm step in `h_x` at zero-based probe index 100; deterministic observations |
| T4 | `h_x=5*k µm`, k=0…199; deterministic observations |
| T5 | 10 µm step in `dc` at zero-based probe index 100; 200-probe required measurement plus a separate 1,000-probe diagnostic; deterministic observations |
| T6 | 5,000 golden-angle probes; deterministic static truth `[7,-9,3] µm` |
| T7 | 100 independent runs; 200 random-walk process and observation samples generated per run; NumPy seed 8675309 |
| Statistics | Configured Gaussian sigma is not a sample statistic. No sample standard deviation is reported, so `ddof` is not applicable. NEES is an arithmetic mean over accepted pre-latch samples. |

The absolute scales of `q_h` and `q_c` have no empirical basis. The design
premise was that their ratio, rather than either absolute scale, would separate
fixture drift from tooling wear. T5 falsifies that premise for the required
clearance-step transient under this sequential model.

## 3. Evidence and reproduction

Durable evidence:

- Filter core: [`../pegsim/filter.py`](../pegsim/filter.py)
- Permanent synthetic tests: [`../tests/test_filter.py`](../tests/test_filter.py)
- Deterministic measurement driver: [`../scripts/measure_f1a_state_filter.py`](../scripts/measure_f1a_state_filter.py)
- Derived synthetic summary: [`../results/f1a_filter_summary.json`](../results/f1a_filter_summary.json)

Exact commands from repository root:

```bash
uv run pytest -q
uv run pytest tests/test_filter.py -q -x
uv run python scripts/measure_f1a_state_filter.py
uv run pytest -q
```

The first full-suite command was the unmodified baseline. The driver performs
no physics and deterministically overwrites only
`results/f1a_filter_summary.json`. The final full-suite command is recorded in
Section 8 after the report is complete.

Commit provenance:

| Evidence state | Commit |
|---|---|
| N4 report and F1a entry state | `c64dc55` |
| Filter, synthetic tests, measurement driver, and derived F1a summary | `ed4f40f` |
| Exact report text | use `git log -1 --oneline -- docs/F1_STATE_FILTER_REPORT.md` |

## 4. Engineering decisions

### D1. Make rejection a non-mutating terminal boundary

**Problem.** Applying even the random-walk prediction to persistent covariance
after a gated measurement would mean that the rejected probe changed filter
state. Applying the gain would silently adapt to the condition that is supposed
to trigger salvage-and-latch.

**Decision.** Compute `x_pred` and `P_pred` locally, calculate innovation and
NIS, and return `SALVAGE` before constructing the Kalman gain when the threshold
is exceeded. Neither `x` nor `P` is assigned on this path.

**Defense.** T3 measured d²=99.501979 against 13.816 and verified bitwise
equality of both stored arrays across the rejected call. The returned record
retained the 200 µm innovation and complete gate diagnostics.

**Rejected alternatives.** Updating then rolling back risks missing a mutated
field; committing `P+Q` after rejection violates “do not update x or P”; clipping
the innovation would convert a special cause into silent adaptation.

### D2. Use solve-based gain and Joseph covariance

**Problem.** Explicit inversion is unnecessary and the simple covariance form
can lose symmetry or positive-definiteness over long operation.

**Decision.** Solve `S Y = H P_pred` and transpose `Y` to obtain the gain. Use
`(I-KH)P_pred(I-KH)ᵀ + K R Kᵀ`, symmetrize, and fail immediately if a result is
non-finite or non-positive-definite.

**Defense.** Across 5,000 probes, maximum absolute covariance asymmetry was
exactly `0.0 m²`; the minimum eigenvalue was `2.425634e-14 m²`.

**Rejected alternatives.** `numpy.linalg.inv` violates the specified numerical
method. `(I-KH)P_pred` does not provide the requested long-run numerical
protection.

### D3. Keep penetration as constructor-supplied feedforward

**Problem.** The n=64 simulation has a deterministic contact-solver penetration
artifact, while hardware should use zero for this correction. Treating it as a
state would confound `dc`; treating N4's deterministic distribution as `R`
would invent measurement noise.

**Decision.** Require `penetration_m` at construction and subtract the known
bias before innovation calculation. F1a uses the exact committed N4 n=64 pooled
minimum value. Hardware will construct the identical class with zero.

**Defense.** T1 uses nonzero penetration in every synthetic observation and
recovers `dc` to 0.000784 µm error. `pegsim/filter.py` has an AST-checked
permanent test prohibiting a MuJoCo import.

**Rejected alternatives.** A module constant would make simulation and hardware
code differ or silently use the wrong bias. Folding penetration into `R` mixes
a known mean error with random covariance.

### D4. Treat missing rank as infinite condition number

**Problem.** A single 2×3 observation matrix has only two returned singular
values, so a naive rectangular `numpy.linalg.cond(H)` can appear well
conditioned while one state direction is completely absent.

**Decision.** Stack recent matrices, test for full column rank with a tolerance
derived from matrix size and float64 epsilon, and return infinity below rank 3.
Warn when rank is missing or conditioning exceeds the float64 half-precision
boundary `1/sqrt(epsilon)`.

**Defense.** The 200-bearing golden schedule measured condition number 1.00508.
The repeated zero-bearing schedule returned infinity and emitted the runtime
warning; its null-direction covariance retained 1.000201 times its prior value.

**Rejected alternatives.** Reporting a finite rectangular condition number
hides structural unobservability. A hard-coded condition threshold would have
no numerical or measured basis.

### D5. Preserve the specified process asymmetry and expose T5

**Problem.** T5 predicts that `q_h >> q_c` will put a 10 µm clearance step into
`dc` while moving `h` by less than 2 µm. Sequential Kalman allocation does the
opposite initially: the large `q_h` replenishes fixture covariance every cycle,
while the tiny `q_c` makes clearance nearly immutable.

**Decision.** Keep the requested working values unchanged and encode T5 as a
strict expected failure. Measure both the required 200-probe horizon and a
1,000-probe diagnostic.

**Defense.** At 200 probes the filter estimated `dc=5.000207 µm`, final
`||h||=4.978217 µm`, and maximum `||h||=9.925217 µm`. At 1,000 probes it reached
`dc=9.005398 µm` and final `||h||=0.990310 µm`, showing that rotating bearings
eventually identify the state but do not prevent the disallowed transient.

**Rejected alternatives.** Reversing the ratio contradicts the work order;
raising `q_c`, resetting covariance at the tool change, batching a pair, or
adding a classifier introduces a different process/update model that requires
an explicit decision before implementation. Testing only the final 1,000-probe
state would hide the 9.925 µm leakage.

### D6. Stop NEES at the salvage boundary

**Problem.** Under the null, a 99.9% per-probe gate has a nonzero false-positive
rate. Once `SALVAGE` occurs, production is latched and `x,P` intentionally stop
advancing. Continuing the random-walk truth against those frozen arrays makes
all later values in that run non-filter NEES samples.

**Decision.** Generate all 200 process and measurement samples for each of 100
runs, accumulate NEES only for accepted samples before the first latch, and
terminate filtering for that run at `SALVAGE`.

**Defense.** The seeded population produced 18,176 accepted pre-latch samples,
16 gated runs, and 84 complete runs. Mean NEES was 2.913454, close to the
three-state expectation of 3 and within the specifically requested interval.

**Rejected alternatives.** Continuing after salvage measured divergence from a
deliberately frozen estimator and produced mean NEES 2,065.88; disabling or
raising the fixed production gate would test different code; discarding whole
gated runs would censor valid pre-latch estimates.

## 5. Measurements

Distances are µm unless the table labels another unit. Values are deterministic
for the committed seeds and configuration. Configured sigma is the known input
distribution or posterior square root, not a sample standard deviation;
`ddof` is therefore not applicable. No measurement below comes from physics or
hardware.

### 5.1 T1 static truth and rotating observability

| State | Truth (µm) | Final estimate (µm) | Error (µm) | Posterior sigma (µm) | Within 3 sigma |
|---|---:|---:|---:|---:|---|
| `h_x` | 42.000000 | 41.299157 | -0.700843 | 1.254312 | yes |
| `h_y` | -31.000000 | -30.609189 | +0.390811 | 1.000128 | yes |
| `dc` | 11.000000 | 11.000784 | +0.000784 | 0.763908 | yes |

| Probes | Gated | Stacked-H condition number | Prediction |
|---:|---:|---:|---|
| 200 | 0 | 1.005078 | **confirmed** |

### 5.2 T2 fixed-bearing non-observability

| Quantity | Measurement |
|---|---:|
| Bearing | 0 rad |
| Truth `[h_x,h_y,dc]` | `[50, 0, -50] µm` |
| Final estimate | `[0, 0, 0] µm` |
| Estimation-error norm | 70.710678 µm |
| Stacked-H condition number | infinity |
| Prior null-direction variance | `9.999999999999997e-7 m²` |
| Posterior null-direction variance | `1.0002009577192307e-6 m²` |
| Posterior/prior null variance | 1.000201 |
| Prediction | **confirmed** |

The fixed observation sees `h_x+dc=0`, exactly matching the zero estimate while
leaving both individual truth values unresolved.

### 5.3 T3 gate on fixture step

| Step probe index | Fixture step (µm) | Innovation (µm) | d² | Threshold | Outcome | `x` unchanged | `P` unchanged |
|---:|---:|---|---:|---:|---|---|---|
| 100 | 200 in `h_x` | `[200, 0]` | 99.501979 | 13.816 | `SALVAGE` | yes | yes |

Prediction: **confirmed**.

### 5.4 T4 slow fixture ramp

| Rate | Final truth `h_x` (µm) | Final estimate `h_x` (µm) | Error (µm) | Posterior sigma (µm) | Gates | Prediction |
|---:|---:|---:|---:|---:|---:|---|
| 5 µm/cycle | 995.000000 | 994.987547 | -0.012453 | 1.254312 | 0 | **confirmed** |

All three final state errors were inside their componentwise 3-sigma bounds.

### 5.5 T5 discriminating clearance step

| Total probes | Post-step probes | Final `dc` estimate (µm) | `dc` error (µm) | `dc` sigma (µm) | Final `||h||` (µm) | Maximum `||h||` (µm) | Gates |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 200 | 100 | 5.000207 | -4.999793 | 0.763908 | 4.978217 | 9.925217 | 0 |
| 1,000 diagnostic | 900 | 9.005398 | -0.994602 | 0.341416 | 0.990310 | 9.925217 | 0 |

The required 200-probe measurement is outside the clearance 3-sigma bound and
exceeds the fixture-motion limit by 4.96×. The longer run eventually converges,
but does not undo the transient. Prediction: **falsified**.

### 5.6 T6 Joseph-form conservation

| Probes | Maximum `|P-Pᵀ|` (m²) | Minimum eigenvalue (m²) | Symmetric to `1e-15` | Positive-definite | Prediction |
|---:|---:|---:|---|---|---|
| 5,000 | 0.0 | `2.4256337193843492e-14` | yes | yes | **confirmed** |

### 5.7 T7 NEES consistency

| Runs | Samples generated/run | Accepted pre-latch samples | Gated runs | Complete runs | Mean NEES | Requested 95% interval | Prediction |
|---:|---:|---:|---:|---:|---:|---|---|
| 100 | 200 | 18,176 | 16 | 84 | 2.913454 | `[0.215795, 9.348404]` | **confirmed** |

The 16 gates are nominal-tail latches, not injected outliers. At a 0.1% null
tail probability per probe, latches over 200-probe runs are expected rather
than evidence of an implementation defect. This result evaluates consistency
before the product's terminal boundary; it does not claim that post-latch state
would continue tracking.

## 6. Predictions versus measurements

| Prediction | Measurement | Status |
|---|---|---|
| Rotating golden-angle bearings identify all three states | Condition 1.005078; every T1 state within 3 sigma | **confirmed** |
| One repeated bearing cannot separate along-bearing `h` from `dc` | Infinite condition; null variance ratio 1.000201; 70.711 µm unresolved error | **confirmed** |
| A 200 µm fixture step gates on its first probe and cannot alter state | d²=99.501979 at index 100; stored `x,P` bitwise unchanged | **confirmed** |
| A 5 µm/cycle fixture ramp does not gate and tracks within 3 sigma | 0 gates; final `h_x` error -0.012453 µm | **confirmed** |
| A 10 µm `dc` step moves `h` less than 2 µm | maximum `||h||=9.925217 µm`; final at 200 probes 4.978217 µm | **falsified** |
| Joseph form remains symmetric and positive-definite for 5,000 probes | asymmetry 0; minimum eigenvalue `2.425634e-14 m²` | **confirmed** |
| The configured linear-Gaussian recursion is NEES-consistent | mean 2.913454 inside `[0.215795,9.348404]` | **confirmed**, before terminal latch |

## 7. What this does not establish

- `q_h` and `q_c` have no empirical basis. `q_h` is a placeholder pending
  either a customer fixture-drift measurement or the hardware run. The ratio,
  not the absolute scale, is the only intended separator in this model, and T5
  shows that the specified ratio fails the required clearance-step transient.
- `R` is injected. Its 1 µm per-axis magnitude is a placeholder until the
  borrowed-arm run. The deterministic N2/N4/N5 solver and mesh distributions
  were not used as an `R` matrix.
- No statistic here is a hardware measurement uncertainty. F1a uses synthetic
  Gaussian observations and deterministic truth only.
- TCP bias is absorbed into `h` as a constant world-frame offset, so absolute
  fixture position is not established. A constant TCP bias does not average
  down and does not cancel antipodally. That is compatible only with a drift
  claim in which every cycle uses the same TCP.
- Bore out-of-roundness at even harmonic order does not cancel antipodally:
  `cos(k(theta+pi))=cos(k theta)` for even `k`. F1a did not inject or measure
  this effect, and no bore-roundness requirement has been established.
- The 2.963 µm penetration value is a deterministic n=64 simulation correction,
  not observation noise and not a hardware correction. Hardware is configured
  with zero pending evidence.
- NEES was evaluated only before terminal `SALVAGE`. It does not establish a
  restart, recovery, or continued-estimation policy after a latch.
- The broad 1 mm prior covariance is a synthetic test configuration, not a
  commissioned-cell prior.
- F1b's four injected channels, per-channel residual `R` measurements, and
  bore-roundness NEES threshold do not exist yet.
- F1c's n=64 physics trajectory, false-positive gate count, persistent filter
  across `run_episode` resets, and reconvergence measurement do not exist yet.
- No SPC chart, MES/OPC UA publishing, PackML transitions, recovery demo, or
  viewer was built.

## 8. Tests added

`tests/test_filter.py` adds eight tests: one import-boundary test plus T1 through
T7. T5 is a strict expected failure, so an unexpected pass will fail pytest and
force review rather than silently changing the recorded conclusion.

| Test | Status before final suite |
|---|---|
| No MuJoCo import | pass |
| T1 static truth | pass |
| T2 fixed bearing | pass |
| T3 fixture step gate | pass |
| T4 fixture ramp | pass |
| T5 clearance discrimination | strict expected failure |
| T6 Joseph conservation | pass |
| T7 NEES consistency | pass |

Baseline full suite at `c64dc55`: **120 passed** in 30.12 s.

Targeted F1a run after implementation: **7 passed, 1 xfailed** in 0.72 s.

Final full suite after report self-review: **127 passed, 1 xfailed** in 30.05 s.
This was the only post-edit full-suite execution.

## 9. Commit traceability

| Requirement | Files | Commit |
|---|---|---|
| Filter state, bias correction, gate, solve-based gain, Joseph covariance | `pegsim/filter.py` | `ed4f40f` |
| T1–T7 synthetic requirements and MuJoCo import boundary | `tests/test_filter.py` | `ed4f40f` |
| Reproducible F1a measurements | `scripts/measure_f1a_state_filter.py`, `results/f1a_filter_summary.json` | `ed4f40f` |
| F1a outcome, decisions, evidence, and risks | `docs/F1_STATE_FILTER_REPORT.md` | use path-specific `git log` command after commit |

## 10. Remaining risks and required decision

1. **T5 is blocking.** The current process model cannot make a newly observed
   clearance step cheap while simultaneously making `dc` nearly static. Decide
   whether the intended `Q` ordering is reversed, `q_c` changes at known tool
   boundaries, or the product may classify/batch multiple bearings before
   committing state. Each is a different model or interface contract.
2. The requested gate implies nominal false latches over long runs. F1a observed
   16 latched Monte Carlo runs out of 100 200-probe sequences. Operational
   false-positive tolerance has not been specified.
3. The observability diagnostic retains bearing history without a production
   retention bound. F1a's 5,000-cycle test is small, but a long-lived service
   should define the maximum diagnostic window before deployment.
4. `Q`, `R`, the initial covariance, TCP stability, and bore harmonics remain
   uncommissioned. None may be presented as customer-cell performance.
5. F1b and F1c must remain stopped until the T5 decision is explicit; otherwise
   later physics evidence would be built on a falsified state-separation claim.
