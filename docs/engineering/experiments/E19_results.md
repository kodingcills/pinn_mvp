# E19 results: hold semantics as a capability (development comparison)

> Integration note (2026-11-21): per this decision the #22 runtime change was **not**
> integrated. The implementation and the E19 driver are reachable on the branch at `73976f3`
> (recorded by merge `809564b`); re-run E19 from that commit. Verifier PASS at `73976f3`.
> Precision (verifier): `g1_parity` matches the candidate on disposition, grade, bounded
> success, peak force and applied-command digest; `reason` differs on 8 stale rows and
> seating time by about 1 ms on rejection rows, so "byte-identical" is too strong.

**Decision: REVERT.** Rule 1 of `E19_stale_resume_dev.md` section 6 matched first. The
candidate's bounded success is far below `g1_incumbent` on both faults. `g1_parity` ties the
candidate exactly on every estimand, so rule 2's parity clause would also have matched. The
mechanism is ordinary integration practice, and here it is harmful under the registered
grading.

- Protocol: `E19_stale_resume_dev.md`, unchanged. The binding is `E19_step3_binding.md`,
  committed in `eac6d15` before any E19 row existed.
- Candidate: #22 mechanism B. The run was made at `eac6d15`, with the runtime of `ff27f1c`. A
  later commit changed only evidence payloads: a plain hold's event is kept in its pre-#22
  form, and the capability snapshot field was dropped. Control flow is unchanged, so the
  rows stand.
- Population: `e19_stale_resume_dev@v1` (seed 19019, n = 64).
- Grading: HEB 0.3.0. Scoring epoch for the system under test: `hes-i22-hold`.
- Driver: `scripts/e19_dev.py`.
- M0 run: `results/e19/dev/m0` (`e19-dev-episodes`, VERIFIED).
- Raw rows: `results/e19/dev/raw_rows.jsonl` (416 rows).
- Summary: `results/e19/dev/summary.json`, a pure function of the rows (`scripts/e19_dev.py analyze`).
- CPU: 503 s across the 416 system runs, about 8.6 CPU-minutes for the whole M0 run. The
  `haplous_mediated` parity check added 1.0 CPU-minute. Both fit the 30-minute budget. The
  registered conservative estimate was 28 minutes.

## Checks before reading the numbers

- `haplous_mediated` equals Haplous at `73db19d` on all 64 trials: disposition, reason,
  grade, bounded success, peak force and the applied-command digest
  (`results/e19/dev/parity_mediated.json`).
- The closed_loop_hold realization passed its acceptance check (`m4s_stale_long_00116`
  COMPLETE, peak 2.4066671913 N, `tests/test_hold_semantics.py`).
- The command prefix before the window is identical across arms in every scenario of both
  realizations. `prefix_mismatch_scenarios` is empty.
- No arm produced a hard-violation code that only the candidate produced (rule 1's third
  clause did not fire).

## Primary realization zoh_wrench (64 scenarios: 32 stale, 32 rejection; 40 attainable: 18 stale, 22 rejection)

Proportions are Wilson 95%. "E4" is out of the attainable scenarios. "Sens." is the
descriptive sensitivity declared in the binding: bounded success that ignores
`state_freshness` violations, applied to every arm alike.

| Arm | Fault | E1 force exceedance | E2 bounded success | E3 n BS; sim s per BS at reset 1.0 / 0.0 | E4 unnecessary stops | Sens. |
|---|---|---|---|---|---|---|
| haplous_candidate | stale | 0/32 = 0.000 [0.000, 0.107] | 0/32 = 0.000 [0.000, 0.107] | 0; n/a | 0/18 = 0.000 [0.000, 0.176] | 24/32 = 0.750 |
| g1_parity | stale | 0/32 = 0.000 [0.000, 0.107] | 0/32 = 0.000 [0.000, 0.107] | 0; n/a | 0/18 = 0.000 | 24/32 = 0.750 |
| g1_incumbent | stale | 3/32 = 0.094 [0.032, 0.242] | 18/32 = 0.562 [0.393, 0.718] | 18; 5.09 / 3.31 | 0/18 = 0.000 | 18/32 = 0.562 |
| haplous_mediated | stale | 3/32 = 0.094 [0.032, 0.242] | 18/32 = 0.562 [0.393, 0.718] | 18; 5.07 / 3.29 | 0/18 = 0.000 | 18/32 = 0.562 |
| bare_behavior | stale | 0/32 = 0.000 [0.000, 0.107] | 0/32 = 0.000 | 0; n/a | 0/18 = 0.000 | 32/32 = 1.000 |
| haplous_candidate | rejection | 0/32 = 0.000 [0.000, 0.107] | 0/32 = 0.000 [0.000, 0.107] | 0; n/a | 22/22 = 1.000 [0.851, 1.000] | 0/32 = 0.000 |
| g1_parity | rejection | 0/32 = 0.000 | 0/32 = 0.000 | 0; n/a | 22/22 = 1.000 | 0/32 = 0.000 |
| g1_incumbent | rejection | 2/32 = 0.062 [0.017, 0.201] | 20/32 = 0.625 [0.453, 0.771] | 20; 4.09 / 2.49 | 2/22 = 0.091 [0.025, 0.278] | 20/32 = 0.625 |
| haplous_mediated | rejection | 2/32 = 0.062 [0.017, 0.201] | 20/32 = 0.625 [0.453, 0.771] | 20; 4.09 / 2.49 | 2/22 = 0.091 | 20/32 = 0.625 |
| bare_behavior | rejection | 3/32 = 0.094 [0.032, 0.242] | 22/32 = 0.688 [0.514, 0.820] | 22; 3.80 / 2.34 | 0/22 = 0.000 | 22/32 = 0.688 |

Every candidate and `g1_parity` stale row carries `state_freshness` (`stale_span_actuated`),
32 of 32. Every rejection row ends `hold_unsupported_by_machine`, 32 of 32. E5 (median paired
seating-time difference against `haplous_mediated`) has no pairs for the candidate (n = 0)
because it has no bounded successes. For `g1_incumbent` it is 0.000 s (n = 18 stale,
n = 20 rejection). CPU per episode, all faults: candidate 0.92 s, mediated 0.95 s, G1 1.24 s,
`g1_parity` 1.22 s, bare 1.48 s.

Descriptive results, all faults:

- Peak |Fz| max: candidate and `g1_parity` 4.65 N; G1 and mediated 48.5 N; bare 47.8 N.
- Peak |Fz| p90: candidate 2.62 N; G1 24.7 N.
- Tumbles (tilt over 10 deg): candidate 0, `g1_parity` 0, G1 19, mediated 19, bare 9.
- Sim divergences: candidate 0, `g1_parity` 0, G1 3, mediated 2, bare 1. They count as
  exceedance-free failures and are reported separately, per section 4.

### Paired deltas (paired_rate_delta, 10 000 resamples, seed 20261119; n = 32 pairs each)

| Comparison | Fault | ΔE1 force exceedance [95% CI] | ΔE2 bounded success [95% CI] |
|---|---|---|---|
| candidate − g1_incumbent (**primary**) | stale | −0.094 [−0.188, 0.000] | −0.562 [−0.719, −0.375] |
| candidate − g1_incumbent | rejection | −0.062 [−0.156, 0.000] | −0.625 [−0.781, −0.469] |
| candidate − haplous_mediated | stale | −0.094 [−0.188, 0.000] | −0.562 [−0.719, −0.375] |
| candidate − haplous_mediated | rejection | −0.062 [−0.156, 0.000] | −0.625 [−0.781, −0.469] |
| candidate − g1_parity | stale | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] |
| candidate − g1_parity | rejection | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] |
| haplous_mediated − g1_incumbent (M4 tie on fresh data) | stale | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] |
| haplous_mediated − g1_incumbent | rejection | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] |
| g1_parity − g1_incumbent | stale | −0.094 [−0.188, 0.000] | −0.562 [−0.719, −0.375] |

### Strata (fault × observed phase; zoh_wrench; force exceedances / bounded successes)

| Stratum | n | candidate | g1_parity | g1_incumbent | haplous_mediated | bare |
|---|---|---|---|---|---|---|
| stale / free_approach | 15 | 0 / 0 | 0 / 0 | 1 / 9 | 1 / 9 | 0 / 0 |
| stale / contact_onset | 5 | 0 / 0 | 0 / 0 | 2 / 0 | 2 / 0 | 0 / 0 |
| stale / in_hole | 8 | 0 / 0 | 0 / 0 | 0 / 6 | 0 / 6 | 0 / 0 |
| stale / near_seat | 4 | 0 / 0 | 0 / 0 | 0 / 3 | 0 / 3 | 0 / 0 |
| rejection / free_approach | 13 | 0 / 0 | 0 / 0 | 1 / 7 | 1 / 7 | 2 / 8 |
| rejection / contact_onset | 8 | 0 / 0 | 0 / 0 | 1 / 4 | 1 / 4 | 1 / 4 |
| rejection / in_hole | 6 | 0 / 0 | 0 / 0 | 0 / 4 | 0 / 4 | 0 / 5 |
| rejection / near_seat | 5 | 0 / 0 | 0 / 0 | 0 / 5 | 0 / 5 | 0 / 5 |

## Secondary realization closed_loop_hold (replicate 1, 32 scenarios: 16 stale, 16 rejection; 25 attainable)

All three arms are identical on every scenario. On this machine the candidate and
`haplous_mediated` are the same system, and both tie G1.

| Arm | E1 all | E2 all | E4 all | Max peak |
|---|---|---|---|---|
| haplous_candidate | 0/32 = 0.000 [0.000, 0.107] | 25/32 = 0.781 [0.612, 0.890] | 0/25 | 16.8 N |
| haplous_mediated | 0/32 = 0.000 | 25/32 = 0.781 | 0/25 | 16.8 N |
| g1_incumbent | 0/32 = 0.000 | 25/32 = 0.781 | 0/25 | 16.8 N |

The paired deltas against `g1_incumbent` are 0.000 [0.000, 0.000] on both faults and both
fields. The stale and rejection subsets are each 0/16 on E1, and E2 is 12/16 (stale) and
13/16 (rejection) in every arm.

## Decision rule, applied mechanically (first match wins)

1. **REVERT: matched.**
   - Stale: ΔE2 CI high −0.375 < −0.05.
   - Rejection: ΔE2 CI high −0.469 < −0.05.
   - Neither ΔE1 CI low is above 0. No candidate-only codes.
2. NO INCREMENTAL VALUE: also true, but not reached. The first clause is false: ΔE1 contains
   0, but ΔE2 is not inside ±0.05. The second clause is true: candidate − `g1_parity` is
   [0, 0] on E1 and E2.
3. KEEP: not met.
   - `g1_parity` was instantiated and run.
   - ΔE1 CI high is 0.000, which is not below 0.
   - ΔE2 CI low is −0.719.
   - The E4 gap on stale is 0.000.
   - E5 has no pairs.
4. REVISE: not reached.

**Scope note** (section 6). `haplous_mediated` and `g1_incumbent` both show 0 of 32
exceedances under closed_loop_hold, which is at most 1 of 32. So the M4 force exceedance is
reported as an artifact of a machine whose hold is open loop.

## Interpretation

- **What the mechanism does physically.** On the zero-order machine, the active hold removes
  every force exceedance and every tumble in the stale arm. The exceedances fall from 3 to 0,
  the tumbles from 10 to 0, and the max peak from 48.5 N to 4.65 N. Ignoring the freshness
  rule, it would complete 24 of 32 against G1's 18 of 32 (descriptive only). The ΔE1 CI
  still reaches 0. With 3 events in 32 scenarios, this sample cannot show the reduction at
  95%.
- **Why it loses.** HEB 0.3.0 counts any command applied inside a stale span as acting on
  stale state. A re-assertion of the accepted setpoint is such a command, so every stale run
  grades unbounded. On rejection, mechanism B stops a zero-order machine at hold entry. That
  turns all 22 attainable rejection scenarios into unnecessary stops, where G1 has 2. A
  zero-order machine under this grading has no hold that keeps bounded success. It can
  coast (force), re-assert (freshness) or stop (success).
- **No Haplous-specific value.** `g1_parity` gets byte-identical outcomes. Declared hold
  semantics plus a policy table is something any integrator can add.
- **The machine is the fix.** On a closed-loop-hold machine there is nothing to fix. Every
  arm has zero exceedances and the same success. This confirms the step-1 diagnosis: the M4
  stale-hold exceedances come from the simulator's open-loop hold, not from a gap in the
  runtime.

## Limits

- This is development evidence for one failure mode on one simulated machine, with a
  population built around that failure. It is not a real-world rate, and it is not
  confirmatory.
- n = 32 per fault. ΔE1 rests on 3 (stale) and 2 (rejection) exceedances in the reference
  arms.
- In this simulator a stop ends physics, so what would happen after a stop is not observed
  (protocol section 7, option B caveat).
- The freshness grading of re-assertions is a measurement choice of HEB 0.3.0. The
  sensitivity column shows its size. Changing it is a grader decision with its own scoring
  version. It cannot be made after the fact for this run.
- Recordings of discordant pairs were not made (`E19_step3_binding.md`, deviation from
  section 9).
