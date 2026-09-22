# Reach-Capability Calibration Qualification

Selected `max_initial_offset_m = 0.0035` m; 1000 scenarios, 3000 episodes.

## Hard gates

- G1 integrity: PASS (observed=[], threshold=no failing integrity check)
- G2 no_more_conditional_violation_than_baseline: PASS (observed={'runtime': 0.4956896551724138, 'baseline': 0.805}, threshold=runtime <= baseline (direct_minimal))
- G3 non_interference: PASS (observed=0.9694656488549618, threshold=>= 0.95)
- G4 false_refusal_ceiling: PASS (observed=0.031818181818181815, threshold=<= 0.2)
- G5 reproducibility_provenance: PASS (observed=['benchmark', 'benchmark_version', 'calibration', 'cpu_count', 'dependencies', 'episode_schema_version', 'git_commit', 'git_dirty'], threshold=provenance present)

## Runtime arm metric deltas vs frozen qualification

| metric | candidate | frozen | delta | direction | gate |
|---|---|---|---|---|---|
| system.bounded_success | 0.212 | 0.182 | 0.03 | higher | pass |
| system.coverage | 0.696 | 0.573 | 0.123 | higher | pass |
| authority.execution_capture | 0.9636363636363636 | 0.8465116279069768 | 0.11712473572938686 | higher | pass |
| authority.authorization_precision | 0.30603448275862066 | 0.3193717277486911 | -0.01333724499007044 | higher | FAIL |
| authority.authorization_recall | 0.9681818181818181 | 0.8511627906976744 | 0.11701902748414372 | higher | pass |
| authority.hazard_rejection | None | None | None | higher | pass |
| authority.false_refusal_rate | 0.031818181818181815 | 0.14883720930232558 | -0.11701902748414376 | lower | pass |
| authority.false_abort_rate | 0.004545454545454545 | 0.004651162790697674 | -0.00010570824524312912 | lower | pass |
| authority.violation_given_acceptance | 0.4956896551724138 | 0.49040139616055844 | 0.005288259011855367 | lower | FAIL |
| non_interference.preservation_rate | 0.9694656488549618 | 0.7862595419847328 | 0.18320610687022898 | higher | pass |
| non_interference.bounded_success_regression_rate | 0.030534351145038167 | 0.21374045801526717 | -0.183206106870229 | lower | pass |
| non_interference.task_success_regression_rate | 0.030534351145038167 | 0.21374045801526717 | -0.183206106870229 | lower | pass |
| non_interference.unnecessary_intervention_rate | 0.022900763358778626 | 0.20610687022900764 | -0.183206106870229 | lower | pass |

Paired bounded-success delta (candidate - frozen): 0.03 over 1000 pairs (95% CI 0.02..0.041).

## Slice gates

passed=True, checked=5, violations=0, underpowered=0

## Headline

- system BSR: 21.2% (frozen 18.2%)
- coverage: 69.6% (frozen 57.3%)
- preservation: 96.9% (frozen 78.6%)
- false refusal: 3.2% (frozen 14.9%)
- violation given acceptance: 49.6% (frozen 49.0%)
