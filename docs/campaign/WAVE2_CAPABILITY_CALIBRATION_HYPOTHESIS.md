# Development Wave 2 Hypothesis: Capability Calibration (degrade-don't-refuse)

## Failure class
CAPABILITY.FALSE_REFUSAL (taxonomy: EXECUTION_SEMANTICS / capability model)

## Current frequency (measured, results/false_refusal_summary.json)
2 of 5 Haplous refusals in the 27-scenario dev family are FALSE_REFUSAL
(40% false-refusal rate; 95% Wilson CI on 2/5: [7.2%, 82.9%] - small n,
directional evidence):
- insert_force_limit_150n: refused `force_requirement_unsensed`;
  counterfactual completes at 2.18 N peak, far inside the 150 N contract.
- insert_deadline_2s: refused `deadline_unachievable`; counterfactual
  completes at ~1.7 s seating, inside the 2 s contract.

## Current severity
S1 (productivity loss / false abort): feasible work refused outright.

## Proposed mechanism (minimal)
Two negotiate() semantics changes, both "degrade with disclosure instead
of refuse where the runtime can enforce the requirement during
execution":
1. MAX_PROCESS_FORCE_N hard, limit > max_sensed_force_n: DEGRADE with
   `force_requirement_enforced_at_sensor_ceiling` - accept and enforce
   at the sensor ceiling (stricter than requested, honors the declared
   upper bound subject to the same sampled-overshoot caveat as any
   enforced limit). The current refusal conflates "cannot verify above
   ceiling" with "cannot honor".
2. DEADLINE_S hard, limit < expected_duration_s: DEGRADE with
   `deadline_at_risk_attempt_enforced` - accept and attempt; the runtime
   already enforces deadlines mid-execution (stop at deadline breach),
   so the requirement remains hard-enforced by termination. Refusal is
   reserved for deadlines below the state period (cannot even take one
   tick).

## Why the mechanism should help
Both refusals reject work the machine can physically perform and the
runtime can bound during execution. Degraded acceptance converts
guaranteed zero task success (refusal) into a chance of bounded success
with unchanged enforcement semantics.

## Metrics expected to improve
- false refusal rate (primary): 2/5 -> 0/5 in the dev family
- task success (Haplous arm): +2 scenarios (force_limit_150n,
  deadline_2s)
- system BSR: +2 bounded successes (both counterfactually complete
  within contract)
- coverage: 22/27 -> 24/27

## Metrics at risk
- harmful/unnecessary intervention rate on the newly accepted scenarios
  (deadline abort at 2 s would be a new stop-intervention; the
  counterfactual says it completes, so risk is low)
- refusal precision/recall (must stay 1.0: the 5 mm unreachable goal
  and stale-persistent refusals must remain TRUE)

## Keep criterion
Paired dev rerun: false refusal rate 0/5, refusal precision/recall
remain 1.0, no new hard violations, BSR_system improves with no
regression in bounded success among accepted.

## Revert criterion
Any new hard violation, any refusal-precision loss, or the newly
accepted scenarios fail their contracts (making the refusals TRUE after
all - which would falsify the counterfactual classification and demand
re-examination of the fork methodology instead).
