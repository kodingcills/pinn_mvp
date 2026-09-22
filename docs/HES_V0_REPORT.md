<!-- hes-report-digest: 90e7aafe32f336ce -->

# HES v0 report: v3_hardening_qualification

1000 scenarios, 3000 episodes, arms ['competent_supervisor', 'direct_minimal', 'haplous_mediated'], unmediated baseline `direct_minimal`.

## Execution authority

| arm | bounded success | coverage | execution capture | false refusal | violation given acceptance |
|---|---|---|---|---|---|
| competent_supervisor | 0.132 (n=1000) | 1.000 (n=1000) | 0.614 (n=215) | 0.000 (n=215) | 0.332 (n=1000) |
| direct_minimal | 0.131 (n=1000) | 1.000 (n=1000) | 0.609 (n=215) | 0.000 (n=215) | 0.805 (n=1000) |
| haplous_mediated | 0.182 (n=1000) | 0.573 (n=1000) | 0.847 (n=215) | 0.149 (n=215) | 0.490 (n=573) |

## Attainability

counts: ATTAINABLE=215, UNKNOWN=785
provenance: PAIRED_BOUNDED_BASELINE=214, NONE=785, COUNTERFACTUAL=1

Attainment-relative metrics (execution capture, false refusal) are bounded by the demonstrated-attainable mass. `UNKNOWN` is not `non-attainable`.

## Intervention quality

counts: BENEFICIAL=5, HARMFUL=12, UNNECESSARY=6, AMBIGUOUS=0
- `BENEFICIAL`: continuation would have recorded a strictly worse force breach
- `HARMFUL`: continuation would have completed without worsening; stop destroyed work
- `UNNECESSARY`: continuation neither completes nor worsens; same bounded failure

| metric | value |
|---|---|
| intervention precision | 0.217 (n=23) |
| harmful intervention rate | 0.522 (n=23) |
| unnecessary intervention rate | 0.261 (n=23) |
| bounded successes recovered | 0 |
| bounded successes destroyed | 12 |


## Non-interference

| arm | preservation | BSR regression | task regression | unnecessary intervention | completion-time tax |
|---|---|---|---|---|---|
| competent_supervisor | 0.870 (n=131) | 0.130 (n=131) | 0.130 (n=131) | 0.000 (n=131) | -3.711 (n=114) |
| haplous_mediated | 0.786 (n=131) | 0.214 (n=131) | 0.214 (n=131) | 0.206 (n=131) | -6.998 (n=103) |

## Failure attribution (run arm)

by layer: EXECUTION_RUNTIME=617, UPSTREAM_POLICY=68, CONTROLLER=67, MACHINE_PHYSICS=24, UNKNOWN=42
by runtime subcause: false_refusal=32, constraint_abort=173, deadline_rejection=108, reach_rejection=265, other=22, hold_timeout=17
unknown: 42 of 818

## Responsibility decomposition

Refusals are charged as a caused loss only when the scenario was 
demonstrated attainable; UNKNOWN-attainability refusals are tracked separately.

correct_refusal=0, false_refusal_loss=32, haplous_caused_loss=17, haplous_unprevented_violation=173, runtime_refusal_attainability_unknown=395, upstream_controller_physics_limit=159, benchmark_evaluator=0, infrastructure=0, unknown=42

## Failure Pareto (run arm, top 10)

| cause | count | severity | haplous responsibility | addressability | priority |
|---|---|---|---|---|---|
| EXECUTION_RUNTIME/constraint_abort | 173 | S3=173 | 1.000 | generically_addressable | 1.730 |
| EXECUTION_RUNTIME/reach_rejection | 265 | S1=265 | 1.000 | generically_addressable | 0.265 |
| EXECUTION_RUNTIME/deadline_rejection | 108 | S1=108 | 1.000 | generically_addressable | 0.108 |
| EXECUTION_RUNTIME/hold_timeout | 17 | S2=17 | 1.000 | generically_addressable | 0.034 |
| EXECUTION_RUNTIME/false_refusal | 32 | S1=32 | 1.000 | generically_addressable | 0.032 |
| EXECUTION_RUNTIME/other | 22 | S1=22 | 1.000 | generically_addressable | 0.022 |
| UPSTREAM_POLICY/- | 68 | S3=63, S2=5 | 0.000 | upstream | 0.000 |
| CONTROLLER/- | 67 | S2=64, S3=3 | 0.000 | technically_addressable | 0.000 |
| UNKNOWN/- | 42 | S3=42 | 0.000 | unknown | 0.000 |
| MACHINE_PHYSICS/- | 24 | S2=24 | 0.000 | technically_addressable | 0.000 |

## Hard gates

qualified: **False**
- `G1` integrity: PASS (observed [], threshold no failing integrity check)
- `G2` no_more_conditional_violation_than_baseline: PASS (observed runtime=0.490, baseline=0.805, threshold runtime <= baseline (direct_minimal))
- `G3` non_interference: FAIL (observed 0.786, threshold >= 0.95)
- `G4` false_refusal_ceiling: PASS (observed 0.149, threshold <= 0.2)
- `G5` reproducibility_provenance: PASS (observed ['benchmark', 'benchmark_version', 'cpu_count', 'dependencies', 'episode_schema_version', 'git_commit', 'git_dirty', 'haplous_version'], threshold provenance present)

## Integrity

passed: **True**
- shortcut_solvability: PASS - always-accept does not reach perfect contract-safe success
- mirrored_scenarios: PASS - no mirrored pairs supplied
- small_sample_intervals: PASS - rates on small samples with a point value must carry an interval
- metric_gaming: PASS - violation-after-acceptance rate 0.473 within ceiling 0.5

## Benchmark health

healthy: **True**
- discriminating_power: PASS - 0.877 of scenarios grade identically for every arm
- degenerate_scenarios: PASS - 91 trivially solved and 786 unsolved by every arm (informational; not necessarily a defect)
- population_balance: PASS - 2 task families, 3 offset strata across 1000 scenarios
- grader_consistency: PASS - bounded success never coexists with a recorded hard violation

## Claim ledger

| claim | registry | HES | executable | notes |
|---|---|---|---|---|
| C1 NON_INTERFERENCE | SUPPORTED_WITHIN_TESTED_SCOPE | WEAKENED | True | preservation vs direct_minimal = 0.786 |
| C2 BOUNDED_EXECUTION | SUPPORTED_WITH_CAVEATS | WEAKENED | True | vs direct_minimal: violation 0.490 vs 0.805; coverage 0.573 vs 1.000 |
| C3 NET_SYSTEM_VALUE | SUPPORTED_WITHIN_TESTED_SCOPE | WEAKENED | True | haplous_mediated does not dominate bare_behavior on (BSR, coverage, violation) |
| C4 RECOVERY_VALUE | SUPPORTED_WITHIN_TESTED_SCOPE | NOT_EXECUTABLE_IN_HES_V0 | False | recovery channels are absent from canonical episode rows |
| C5 CONTROLLER_SUPERIORITY | UNKNOWN | NOT_EXECUTABLE_IN_HES_V0 | False | requires a controller-candidate tournament, not an execution campaign |
| C6 LEARNED_POLICY_VALUE | UNKNOWN | NOT_EXECUTABLE_IN_HES_V0 | False | requires an external learned-policy platform run |
| C7 ROBUSTNESS | MIXED | NOT_EXECUTABLE_IN_HES_V0 | False | requires boundary sweeps; run `hes boundary` and feed the envelope |
| C8 GENERALITY | SUPPORTED_WITH_CAVEATS | NOT_EXECUTABLE_IN_HES_V0 | False | requires cross-adapter code-diff evidence, not an execution campaign |
| C9 REAL_TIME_VIABILITY | SUPPORTED_WITHIN_TESTED_SCOPE | NOT_EXECUTABLE_IN_HES_V0 | False | requires loop-latency rows, not canonical episode rows |
| C10 RUNTIME_RELIABILITY | SUPPORTED_WITHIN_TESTED_SCOPE | NOT_EXECUTABLE_IN_HES_V0 | False | requires the reliability fuzz/soak harness, not an execution campaign |
| C11 INCUMBENT_DIFFERENTIATION | SUPPORTED_WITHIN_TESTED_SCOPE | WEAKENED | True | vs competent_supervisor: preservation 0.7862595419847328 vs 0.8702290076335878 |
| C12 SIMULATION_RELEVANCE | MIXED | NOT_EXECUTABLE_IN_HES_V0 | False | requires cross-engine pairing, not a single-engine campaign |

## Recommended next experiments

- **counterfactual_sweep_on_unknown_attainability** (priority 1.285): removes uncertainty about attainability of the large UNKNOWN scenario mass. 785/1000 scenarios have UNKNOWN attainability; every attainment-relative metric is bounded by this Cost: counterfactual forks on a stratified sample.
- **harmful_intervention_root_cause** (priority 0.922): removes uncertainty about which mediation episodes destroy working execution. harmful intervention rate 0.522 is high Cost: targeted replay of harmful forks with attribution.
- **refusal_cause_decomposition** (priority 0.614): removes uncertainty about which refusals are correct vs productivity losses. coverage is 0.573; need per-refusal counterfactual classification Cost: fork each refused scenario once.

## Caveats

- completion-time comparison vs this baseline is confounded: direct_minimal does not terminate on success, so it runs to horizon

## Not available from this evidence

- `task_progress`: canonical rows carry no progress channel
- `integrated_constraint_excursion`: violation rows are pruned per episode
- `collision_metrics`: no collision channel in the internal substrate
- `decision_latency`: runtime decision timing is not in canonical rows
- `evidence_completeness`: not derived from canonical rows
- `portability_engineering_leverage`: manifest-level metadata only

## Historical failure corpus

20 entries, 20 without a regression test.

