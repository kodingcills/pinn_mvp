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

## v3 — temporal hold/revalidate/resume (Wave 3)

- Motivating evidence: counterfactual fork (post-v2 rerun): 3 of 4
  runtime stop-interventions were HARMFUL, all temporal-integrity stops
  (dropout_large, stale_mid_motion, command_rejection_persistent) where
  the unmediated arm completes within contract. Hypothesis artifact:
  .opencode/haplous/prehardware/WAVE3_TEMPORAL_HOLD_HYPOTHESIS.md
- Change: temporal-integrity faults (freshness gap, stale delivery,
  command-rejection streak) now SUSPEND authority (DEGRADED, compliant
  hold of the last accepted command) instead of aborting; on restoration
  the fresh observation is revalidated (normal constraint evaluation) -
  clean resumes, breached aborts. Bounded by new MachineCapabilities
  field max_hold_unobserved_s (default 0.5 s = 25x the 20 ms freshness
  contract; measured safe hold 200 ms). Pre-execution stale refusal and
  machine-fault aborts unchanged. Temporal faults never consume recovery
  budget (recovery is for physical breaches).
- Grading correction (arm-symmetric, machine-recorded): staleness
  violations now require ACTUATION within the stale span (applied-
  command times from the machine session, open interval). Closes the
  caching loophole (bare acting through blackouts still grades in
  violation: 4052 staleness catches stand for bare where it actuated);
  suspension-without-actuation no longer counts machine-imposed
  staleness against the execution.
- Scenario expectations updated where counterfactual evidence falsified
  them (4 dev temporal scenarios + v0-sim dropout_large and
  unsupported_requirement_force, all ABORTED/REFUSED -> COMPLETE).
- After metrics (paired, 27 dev scenarios, three arms):
  - haplous: SR 14 -> 19 (0.704), BSR among accepted 13/24 (0.542) ->
    15/20 (0.750), coverage 24/27, violation-trials 3
  - supervisor: SR 0.519, BSRacc 0.519, violation-trials 4
  - haplous now EXCEEDS the supervisor on every headline metric
  - counterfactual: 0 HARMFUL interventions (was 3); 1 BENEFICIAL
  - false refusals 0/3; refusal precision/recall 1.0
  - v0-sim conformance 20/20 under updated expectations
- Failure redistribution: none (per-scenario diff: only ABORTED ->
  COMPLETE transitions, zero regressions).
- Decision: KEEP
