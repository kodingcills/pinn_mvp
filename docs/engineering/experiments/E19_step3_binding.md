# E19 step 3: candidate binding (written before any E19 row exists)

This note binds the placeholders of `E19_stale_resume_dev.md` (left byte-unchanged) to code,
as its header requires before the run. It fixes how each decision-rule term is computed. It
does not change an estimand, the margin, the statistics or the decision rule. Nothing below was
chosen after an E19 row existed. The only E19-adjacent episodes run before this note are the
`m4s_stale_long_00116` checks in `tests/test_hold_semantics.py` (the protocol's acceptance
check, on the frozen M4 diagnosis case, not on the E19 population).

## Arms (driver `scripts/e19_dev.py`)

| Arm | Bound to |
|---|---|
| `haplous_candidate` | Haplous at the commit of #22 (`docs/engineering/designs/I22_hold_semantics.md`), mechanism B. The standard machine declares `zero_order_wrench`, so stale holds are active holds and rejection holds stop. |
| `haplous_mediated` | Haplous on `PreI22AssumptionMachine`. This is the standard physics declared `closed_loop_hold`, so the runtime plain-holds on every cause, as before #22. `scripts/e19_dev.py parity-mediated --before 73db19d` checks every trial against Haplous at `73db19d` (disposition, reason, grade, bounded success, peak force, applied-command digest). |
| `g1_incumbent` | `G1IncumbentSystem()`, unchanged. |
| `g1_parity` | `G1IncumbentSystem(hold_semantics_aware=True)`, the same hold table given to G1 (option B is something an integrator could add). On a zero-order machine it re-applies its last accepted command on untrusted ticks and stops at its first rejected command. G1's hold entry is per rejected tick (G1 design section 6, item 5), while Haplous's is after 3 consecutive rejections. |
| `bare_behavior` | `BareSystem`. |

Realization `closed_loop_hold` is `benchmarks/heb/hold_realizations.py:ClosedLoopHoldMachine`. Its
acceptance check passes: `m4s_stale_long_00116` under `haplous_mediated` is COMPLETE and bounded,
with a peak of 2.4066671913 N (the probe's value to 1e-9). On that machine `haplous_mediated` and
`haplous_candidate` are the same system (both see `closed_loop_hold` and plain-hold). Replicate 1
is population indices 0 to 31, which cover each of the 32 cells once.

## Term bindings for sections 4 to 6

- ΔE1 and ΔE2 per fault: `paired_rate_delta(candidate, reference, field=...)` on
  `process_force_exceeded` and `bounded_success`, with 10 000 resamples and seed 20261119.
- "CI contains 0" means `ci_low <= 0 <= ci_high`. "Inside ±0.05" means `ci_low >= -0.05` and
  `ci_high <= 0.05`.
- Rule 1 checks both faults on zoh_wrench. "A hard-violation code no other arm produces" is
  checked per realization against the union of every other arm's codes.
- Rules 2 and 3 use the primary comparison (fault stale, zoh_wrench), and so does rule 2's
  `g1_parity` clause. Rule 3's E4 is the stale-subset point estimate of the candidate minus
  `g1_incumbent`. Its E5 is the stale-subset median of candidate minus `haplous_mediated`.
- E4: `reported_disposition` is ABORTED or REFUSED, with no graded hard violation, a grade
  other than SIM_DIVERGED, and a scenario that `Run.attainability()` marks ATTAINABLE (paired
  bounded baseline `bare_behavior` on zoh_wrench, any arm otherwise).
- Observed phase is classified from the zoh_wrench `haplous_mediated` observations. The
  closed_loop_hold rows reuse it.

## Known grading interaction (disclosed before the run)

HEB 0.3.0 grades a command applied inside a stale span as `state_freshness`
(`stale_span_actuated`). An active-hold re-assertion is such a command. On
`m4s_stale_long_00116`, both `haplous_candidate` and `g1_parity` remove the 296.7 N exceedance
(peak 2.41 N) and complete, yet they grade as not bounded. Grading stays HEB 0.3.0, as
registered. A descriptive sensitivity, outside the decision rule, is computed for every arm
alike: bounded success counting task successes whose only hard violations are
`state_freshness`.

Prediction, written before the run. On stale, ΔE1 goes below 0 against `g1_incumbent`, and
ΔE2 falls well below -0.05 (the freshness grading). On rejection, ΔE2 falls well below
-0.05 (stops). So rule 1, REVERT, is the likely outcome, and `g1_parity` should tie the
candidate.

## Deviation from section 9

Recordings of discordant pairs are not made in this run. The analysis is a pure function of
`results/e19/dev/raw_rows.jsonl` (`scripts/e19_dev.py analyze`), which serves as the recompute
path.
