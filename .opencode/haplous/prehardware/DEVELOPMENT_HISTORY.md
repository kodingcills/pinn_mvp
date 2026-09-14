# Haplous Development History

## v2 — capability calibration (Wave 2)

- Commit: (this commit)
- Motivating evidence: results/false_refusal_summary.json (first run):
  2 of 5 Haplous refusals were FALSE_REFUSAL by counterfactual
  execution (insert_force_limit_150n: completes at 2.18 N vs 150 N
  contract; insert_deadline_2s: completes at ~1.62 s vs 2 s contract).
  Hypothesis artifact:
  .opencode/haplous/prehardware/WAVE2_CAPABILITY_CALIBRATION_HYPOTHESIS.md
- Change:
  1. negotiate(): hard MAX_PROCESS_FORCE_N above the sensor ceiling
     degrades (`force_requirement_enforced_at_sensor_ceiling`) instead
     of refusing; build_constraints clamps enforcement to the sensed
     ceiling (breaches above saturation are undetectable, so the clamp
     makes enforcement real and verifiable).
  2. Insertion expected_duration_s calibrated 4.0 -> 2.0 s from
     measured completions (nominal seating 1.62-1.68 s, 9 dev trials;
     offset-path up to 2.62 s). The 4.0 s declaration was never a
     measured quantity.
  3. REJECTED sub-change (tested and reverted within the wave):
     deadline degrade-and-attempt semantics. It also accepted
     slide_deadline_infeasible (1.0 s deadline vs 1.95-6.1 s measured
     slide completions - TRUE-infeasible), converting a clean refusal
     into a recorded violation on unwinnable work. Refusal of
     known-infeasible contracts is a Haplous differentiator (C11);
     attempt-everything matches the supervisor's blind shape. The
     insertion false refusal was instead fixed by calibrating the
     duration declaration.
  4. Scenario expectations for the two falsified refusals updated
     (expected COMPLETE, infeasible_request False) with counterfactual
     justification recorded in the scenario definitions.
- After metrics (paired, 27 dev scenarios):
  - task success 12 -> 14 (McNemar n01=2 n10=0, p=0.5; small n,
    directional)
  - BSR among accepted 11/22 (0.500) -> 13/24 (0.542)
  - coverage 22/27 -> 24/27
  - violations 6 -> 6 (none added)
  - false refusals 2/5 -> 0/5; refusal precision 3/3, recall 3/3
  - vs supervisor: task success now EQUAL (14 vs 14); conditional BSR
    Haplous 0.542 > supervisor 0.519; violations 6 < 7
- Failure redistribution: none observed (the two newly accepted
  scenarios complete within contract; no new stop-interventions).
- Decision: KEEP
