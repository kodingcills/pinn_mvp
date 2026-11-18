# E19: temporal-hold force exceedance, development comparison (pre-registration DRAFT)

Status: DRAFT. Nothing in this protocol has been run. Issue #19, steps 1 and 2. Step 3 (the
candidate mechanism) waits for the #16 runtime refactor. The candidate arm is a placeholder until
an amendment binds it to a commit SHA, and that amendment lands before any E19 row exists.

Development only. This is not a confirmatory test and does not touch Tier 3. `m4_stress@v1` is
frozen. It is not tuned on and not reused as a holdout. Its case `m4s_stale_long_00116` was
reproduced once for diagnosis only (step 1).

## 1. What step 1 found (evidence for the design)

Driver `scripts/e19_diagnose.py`, output `results/e19/diagnosis/summary.json`, timelines
`results/e19/diagnosis/m4s_stale_long_00116_{haplous_mediated,g1_incumbent}_timeline.png`.

- The reproduction matches the committed row. Peak 296.69 N at t = 0.751 s for both guarded
  arms. Haplous's applied-command digest is identical (`e546e30e...`, 815 commands).
- The exceedance happens during the hold, not after a resume. Hold entry is at 0.4075 s and the
  stale window ends at 0.779 s. Haplous never resumed. The last command before the hold
  (t = 0.407 s, target z = 0.925 mm, k_z = 500 N/m) is the last command applied until the
  abort. H1 (target or stiffness keeps advancing during the hold) is refuted.
- Every M4 force exceedance in a temporal stratum sits inside its fault window. That holds for
  57 of 60 rows (stale_long and rejection_long, all arms). The other 3 are one scenario
  (`m4s_rejection_long_00072`, reported at t = 0.0045 s). Its timestamp most likely follows a
  MuJoCo auto-reset. That is inferred, not checked.
- Root cause: the MuJoCo machine's hold replays the last joint wrench open loop. The servo law runs
  only on a tick with a newly accepted command (`haplous/adapters/mujoco_adapter.py:319`).
  Otherwise the frozen `qfrc_applied` is replayed (`:340`, `:345`). That wrench holds gravity
  compensation and the damping term for the pre-hold velocity (`pegsim/control.py:237`, `:244`).
  So the peg coasts on at its approach speed of about 10 mm/s and reaches the hole surface at
  0.5195 s. From there it tilts with no restoring term. Tilt doubles about every 20 ms (0.06 deg
  at 0.54 s, 1.24 deg at 0.62 s, 18.5 deg at 0.70 s, 69 deg at 0.74 s). It hits at 126.7 deg
  tilt (296.7 N, one tick), then the integrator diverges (z = -32 m at tick 1518). MuJoCo
  auto-resets, so G1 then "completes" from qpos0. The tilt guard in the servo (10 deg,
  `pegsim/control.py:228`) never runs during the hold, for the same reason.
- Both arms withhold commands during the hold. Haplous does it at `haplous/runtime.py:482-501`
  and `:625-627`. G1 does it at `benchmarks/incumbent.py:141-146` and `:183`. That is the shared
  mechanism named in G1 design section 6, item 7. The runtime assumes the machine "compliantly
  holds its last accepted command" (`haplous/runtime.py:275`). The adapter does not. Its stale
  fault delays only the receive stamp (`mujoco_adapter.py:272-273`), so stale state does not
  hide the contact. Haplous saw the 296.7 N on the tick it happened.
- Decisive counterfactual (one episode per arm). The same scenario was run with a closed-loop hold,
  where a withheld tick re-applies the last accepted command so the servo runs on live state
  against the held target. Both arms then COMPLETE with bounded success, a peak of 2.41 N and a
  maximum tilt of 0.13 deg.

The design consequence is that resume-time mechanisms (a re-anchored target, a force-ramp
revalidation) act too late. The question for E19 is how the system holds, under fault kind ×
window length × contact phase at hold entry, and whether the answer holds up on a machine whose
own hold is closed loop.

## 2. Population

`e19_stale_resume_dev@v1`, generator `benchmarks/stale_resume_population.py` (`e19_population`),
seed 19019, n = 64. The test `tests/test_stale_resume_population.py` checks that it is
deterministic and disjoint from `m4_stress@v1` (seed, ids and fault windows). Register it in
`hes/populations.py` when the run is authorized.

Insertion only, built with the M4 stress `_insertion` helper. That gives a 25 N process-force
ceiling, a 0.02 s state-age bound and interior offsets U(0, 2.5 mm). The design is full factorial
with 2 replicates per cell (32 cells):

| Factor | Levels |
|---|---|
| fault | stale (sample age 0.05 s), command rejection |
| intended phase at window start (start range, s) | free_approach U(0.15, 0.35); contact_onset U(0.45, 0.55); in_hole U(0.80, 1.30); near_seat U(1.45, 1.60) |
| window duration (s) | 0.10, 0.25, 0.40, 0.60 (0.60 exceeds the 0.5 s hold budget) |

**Observed phase (the analysis stratum).** The intended phase only sets the start range. The
driver classifies the phase from the `haplous_mediated` observation at the first tick with
`source_s >= t0`. All arms share the same command prefix before the window, and the driver checks
that from the applied-command digests. The rules, in order:

- free_approach if `in_contact == 0` and tip z > 0;
- contact_onset if -0.5 mm < z <= 0, or if in contact with z > -0.5 mm;
- in_hole if -9 mm < z <= -0.5 mm;
- near_seat otherwise.

Exclusions: none. Slides are left out by design (no M4 slide had a temporal-stratum exceedance).

## 3. Arms and realizations

| Arm | Role |
|---|---|
| `haplous_mediated` | current Haplous at the pinned SHA |
| `g1_incumbent` | competent incumbent, unchanged (never weakened) |
| `bare_behavior` | context, the direct-execution baseline |
| `haplous_candidate` | PLACEHOLDER for the step-3 mechanism (options in section 7) |
| `g1_parity` | PLACEHOLDER. G1 given the same hold change, when the candidate is something an integrator could add to G1. It is required for options A to C. |

Realizations of the machine hold:

- `zoh_wrench` (primary) is today's adapter.
- `closed_loop_hold` (secondary) is a benchmark-side `machine_factory` that re-applies the last
  accepted command on withheld ticks, as the step-1 probe did. Its acceptance check, before any
  E19 run, is to reproduce the probe on `m4s_stale_long_00116`: COMPLETE, peak 2.41 N.

The primary realization runs all 5 arms on all 64 scenarios. The secondary runs
`haplous_mediated`, `g1_incumbent` and `haplous_candidate` on replicate 1 (32 scenarios).

## 4. Estimands (per arm × realization; strata are fault × observed phase, pooled per fault)

| Id | Estimand | Numerator / denominator |
|---|---|---|
| E1 (primary) | force exceedance rate | scenarios with at least one graded `process_force` hard violation / all scheduled |
| E2 | bounded success | `hes.metrics.system_metrics` |
| E3 | accepted work | bounded successes, plus total simulated time per bounded success at reset 1.0 s and 0.0 s (M4 `time_per_bounded_success`) |
| E4 | unnecessary stops | ABORTED or REFUSED with no graded hard violation, on a scenario `hes.run.Run.attainability` marks attainable / attainable scenarios |
| E5 | added sim cycle time | median paired (arm - `haplous_mediated`) `seating_time_s` over scenarios where both reach bounded success; CPU s/episode reported separately as compute |

Descriptive only: peak |Fz| (median, p90, max), tumble count (max tilt > 10 deg), and
sim-divergence count.

Scoring: HEB 0.3.0 / epoch `hes-m4-e3-simdiv` (#21) supersedes the draft `e19-v1` rule. A
diverged trial ends at the divergence with fault `sim_diverged`, graded `SIM_DIVERGED` (no task or
bounded success). For E1 and E3/E5, `sim_diverged` trials count as an exceedance-free failure in
every arm alike and are reported separately; the diverged count per arm is a descriptive estimand.

## 5. Pairing and statistics

- The unit is the scenario. There is one row per (scenario, arm, realization), and
  `check_aligned` (`scripts/m4_campaign.py`) stops the driver otherwise.
- Proportions use Wilson 95% (`hes.metrics._prop`). Paired deltas use
  `hes.regression.paired_rate_delta` with 10 000 resamples and seed 20261119, on the fields
  `bounded_success` and `process_force_exceeded` (a row bool written by the driver).
- Primary comparison: `haplous_candidate - g1_incumbent` on E1, fault = stale, realization
  zoh_wrench. Secondary comparisons: the same on rejection; `haplous_candidate - haplous_mediated`;
  `haplous_candidate - g1_parity`; `haplous_mediated - g1_incumbent` (M4 tie on fresh data); every
  arm under closed_loop_hold. Everything is exploratory, with no family-wise claim. The margin is
  0.05.

## 6. Decision rule (first match wins; recorded on #19 and on the Gate C epic)

Here ΔE1 is the primary paired delta in exceedance rate, where negative is better. ΔE2 is the
same pairing on bounded success.

1. **REVERT** applies if, on EITHER fault (stale or rejection), the ΔE1 CI low is > 0 or the ΔE2
   CI high is < -0.05, or the candidate produces a hard-violation code that no other arm produces.
2. **NO INCREMENTAL VALUE** applies if the ΔE1 CI contains 0 and the ΔE2 CI lies inside ±0.05. It
   also applies if `haplous_candidate - g1_parity` lies inside ±0.05 on both E1 and E2. In that
   case the mechanism is ordinary integration practice, not Haplous value. It may still ship as a
   default, but it does not count toward Gate C.
3. **KEEP** requires an instantiated, run `g1_parity` arm (the same hold change given to G1);
   without it KEEP is unreachable and the result is REVISE. KEEP applies if the ΔE1 CI high is < 0, the ΔE2 CI low is >= -0.05, E4 is no more than
   0.05 above `g1_incumbent` (point), and the E5 median is <= 0.10 s.
4. **REVISE** covers anything else, for example fewer exceedances bought with more stops or time.

Scope note, decided now. Suppose `haplous_mediated` and `g1_incumbent` both show <= 1/32
exceedances under closed_loop_hold. Then the M4 exceedance is reported as an artifact of a
machine whose hold is open loop. Any KEEP is then scoped to machines that declare such a hold.

### Amendments before any run (2026-11-18, from the step 1–2 verifier; no E19 row existed)

- `g1_parity` must be instantiated for KEEP (rule 3); rejection regressions can trigger REVERT
  (rule 1); `sim_diverged` handling follows HEB 0.3.0 (section 4).
- Power: with n = 32 scenarios per fault, the rule-2 condition "ΔE2 CI inside ±0.05" is met only
  with near-zero discordance; most real effects will land in REVISE. This is accepted for a
  development comparison; a KEEP would need a separately authorized confirmatory replication.
- This population is built around the observed failure (stale/rejection windows crossing contact
  onset). Results are development evidence for that failure mode only and must not be cited as
  general value or as real-world rates.

## 7. Candidate mechanisms (for the step-3 amendment), ranked

| Rank | Option | Expected effect | Risk |
|---|---|---|---|
| A | Active hold. During a temporal hold Haplous re-asserts the last accepted setpoint every period, or a setpoint re-anchored at the last trusted pose with zero velocity. The behaviour gets no new decision on stale state. | The probe shows it removes the exceedance for stale holds. It does nothing for rejection, because re-sent commands are rejected too. | It equals `g1_parity`, which makes NO INCREMENTAL VALUE likely. |
| B | Hold semantics as a capability. The machine declares `hold_mode` (closed-loop setpoint, zero-order wrench, none). Haplous then picks hold, active hold or stop from the declaration and refuses a hold the machine cannot do. | Covers both faults. A stop in this simulator ends physics, so the consequences after a stop are not visible (a measurement caveat). | Adds a capability field and touches refusal semantics (behaviour freeze, needs a locked design). |
| C | Stop at hold entry when the hold is open loop. | Removes the exceedance but costs bounded success (E4). | Many unnecessary stops. |
| D | Resume-time guard (re-anchored target, force-ramp revalidation). This is issue #19's original idea. | None on the M4 failure, which happens before resume. | Ranked last, measured only as a control if built. |

The machine-side fix (make the MuJoCo adapter's hold closed loop, as its docstring and the runtime
already assume) is a realization fidelity fix, not a Haplous mechanism. It is measured here as
`closed_loop_hold` and belongs to the runtime or HEB owner as a new realization version.

## 8. CPU estimate

Per-episode means come from `results/m4/campaign/cost.json`: bare 16.33 s, G1 2.58 s, Haplous
1.21 s. The candidate is costed at the Haplous mean and `g1_parity` at the G1 mean.

- zoh_wrench: 64 × (16.33 + 2.58 + 1.21 + 1.21 + 2.58) = 1530 s.
- closed_loop_hold: 32 × (1.21 + 2.58 + 1.21) = 160 s.
- Total: about 1690 s, or 28 CPU-minutes.

The M4 stale/rejection insertion rows alone average 1.49 s (bare), 0.61 s (G1) and 0.51 s
(Haplous). That puts the likely cost at about 5 CPU-minutes. The cost.json figure is the
conservative bound.

## 9. Evidence

Raw rows go to `results/e19/dev/raw_rows.jsonl`, with the driver in `scripts/`. M0 manifests and
recordings of every discordant pair go to the gitignored store, with at most 2 exemplar pairs
committed. An independent recompute script grades from raw rows, and the verifier names what it
reproduced at the analysis SHA.
