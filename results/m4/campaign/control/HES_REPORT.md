<!-- hes-report-digest: b746d2e60e7efa6c -->

# HES v0 report: m4-hes-m4-e2-campaign-control

181 scenarios, 724 episodes, arms ['bare_behavior', 'competent_supervisor', 'g1_incumbent', 'haplous_mediated'], unmediated baseline `bare_behavior`.

## Execution authority

| arm | bounded success | coverage | execution capture | false refusal | violation given acceptance |
|---|---|---|---|---|---|
| bare_behavior | 0.127 (n=181) | 1.000 (n=181) | 0.548 (n=42) | 0.000 (n=42) | 0.773 (n=181) |
| competent_supervisor | 0.110 (n=181) | 1.000 (n=181) | 0.476 (n=42) | 0.000 (n=42) | 0.287 (n=181) |
| g1_incumbent | 0.227 (n=181) | 0.707 (n=181) | 0.976 (n=42) | 0.024 (n=42) | 0.430 (n=128) |
| haplous_mediated | 0.227 (n=181) | 0.707 (n=181) | 0.976 (n=42) | 0.024 (n=42) | 0.430 (n=128) |

## Attainability

counts: UNKNOWN=139, ATTAINABLE=42
provenance: NONE=139, PAIRED_BOUNDED_BASELINE=42

Attainment-relative metrics (execution capture, false refusal) are bounded by the demonstrated-attainable mass. `UNKNOWN` is not `non-attainable`.

## Intervention quality

counts: BENEFICIAL=0, HARMFUL=0, UNNECESSARY=0, INSUFFICIENT=0, AMBIGUOUS=0
- `BENEFICIAL`: intervention yields a strictly better bounded outcome than the continuation
- `HARMFUL`: continuation achieves bounded success while the intervention does not
- `UNNECESSARY`: same bounded outcome, no material exceedance difference

| metric | value |
|---|---|
| intervention precision | NOT AVAILABLE (n=0) |
| harmful intervention rate | NOT AVAILABLE (n=0) |
| unnecessary intervention rate | NOT AVAILABLE (n=0) |
| bounded successes recovered | 0 |
| bounded successes destroyed | 0 |


## Non-interference

| arm | preservation | BSR regression | task regression | unnecessary intervention | completion-time tax |
|---|---|---|---|---|---|
| competent_supervisor | 0.870 (n=23) | 0.130 (n=23) | 0.130 (n=23) | 0.000 (n=23) | -0.330 (n=20) |
| g1_incumbent | 0.957 (n=23) | 0.043 (n=23) | 0.043 (n=23) | 0.043 (n=23) | -0.545 (n=22) |
| haplous_mediated | 0.957 (n=23) | 0.043 (n=23) | 0.043 (n=23) | 0.043 (n=23) | -4.595 (n=22) |

## Failure attribution (run arm)

by layer: UPSTREAM_POLICY=13, CONTROLLER=25, EXECUTION_RUNTIME=100, MACHINE_PHYSICS=2
by runtime subcause: reach_rejection=32, freshness_rejection=5, constraint_abort=43, deadline_rejection=15, hold_timeout=4, false_refusal=1
unknown: 0 of 140

## Responsibility decomposition

Refusals are charged as a caused loss only when the scenario was 
demonstrated attainable; UNKNOWN-attainability refusals are tracked separately.

correct_refusal=0, false_refusal_loss=1, haplous_caused_loss=4, haplous_unprevented_violation=43, runtime_refusal_attainability_unknown=52, upstream_controller_physics_limit=40, benchmark_evaluator=0, infrastructure=0, unknown=0

## Failure Pareto (run arm, top 10)

| cause | count | severity | haplous responsibility | addressability | priority |
|---|---|---|---|---|---|
| EXECUTION_RUNTIME/constraint_abort | 43 | S3=43 | 1.000 | generically_addressable | 2.376 |
| EXECUTION_RUNTIME/reach_rejection | 32 | S1=32 | 1.000 | generically_addressable | 0.177 |
| EXECUTION_RUNTIME/deadline_rejection | 15 | S1=15 | 1.000 | generically_addressable | 0.083 |
| EXECUTION_RUNTIME/hold_timeout | 4 | S2=4 | 1.000 | generically_addressable | 0.044 |
| EXECUTION_RUNTIME/freshness_rejection | 5 | S1=5 | 1.000 | generically_addressable | 0.028 |
| EXECUTION_RUNTIME/false_refusal | 1 | S1=1 | 1.000 | generically_addressable | 0.006 |
| CONTROLLER/- | 25 | S2=25 | 0.000 | technically_addressable | 0.000 |
| UPSTREAM_POLICY/- | 13 | S3=12, S2=1 | 0.000 | upstream | 0.000 |
| MACHINE_PHYSICS/- | 2 | S2=2 | 0.000 | technically_addressable | 0.000 |

## Hard gates

qualified: **True**
- `G1` integrity: PASS (observed [], threshold no failing integrity check)
- `G2` no_more_conditional_violation_than_baseline: PASS (observed runtime=0.430, baseline=0.773, threshold runtime <= baseline (bare_behavior))
- `G3` non_interference: PASS (observed 0.957, threshold >= 0.95)
- `G4` false_refusal_ceiling: PASS (observed 0.024, threshold <= 0.2)
- `G5` reproducibility_provenance: PASS (observed ['benchmark', 'benchmark_version', 'cpu_count', 'dependencies', 'episode_schema_version', 'git_commit', 'git_dirty', 'haplous_version'], threshold provenance present)

## Integrity

passed: **True**
- shortcut_solvability: PASS - always-accept does not reach perfect contract-safe success
- mirrored_scenarios: PASS - no mirrored pairs supplied
- small_sample_intervals: PASS - rates on small samples with a point value must carry an interval
- metric_gaming: PASS - violation-after-acceptance rate 0.417 within ceiling 0.5

## Benchmark health

healthy: **True**
- discriminating_power: PASS - 0.873 of scenarios grade identically for every arm
- degenerate_scenarios: PASS - 19 trivially solved and 139 unsolved by every arm (informational; not necessarily a defect)
- population_balance: PASS - 2 task families, 3 offset strata across 181 scenarios
- grader_consistency: PASS - bounded success never coexists with a recorded hard violation

## Claim ledger

| claim | registry | HES | executable | notes |
|---|---|---|---|---|
| C1 NON_INTERFERENCE | SUPPORTED_WITHIN_TESTED_SCOPE | UNKNOWN | True | baseline direct_upstream_stream has no paired arm in this run |
| C2 BOUNDED_EXECUTION | SUPPORTED_WITH_CAVEATS | WEAKENED | True | vs bare_behavior: violation 0.430 vs 0.773; coverage 0.707 vs 1.000 |
| C3 NET_SYSTEM_VALUE | SUPPORTED_WITHIN_TESTED_SCOPE | WEAKENED | True | haplous_mediated does not dominate bare_behavior on (BSR, coverage, violation) |
| C4 RECOVERY_VALUE | SUPPORTED_WITHIN_TESTED_SCOPE | NOT_EXECUTABLE_IN_HES_V0 | False | recovery channels are absent from canonical episode rows |
| C5 CONTROLLER_SUPERIORITY | UNKNOWN | NOT_EXECUTABLE_IN_HES_V0 | False | requires a controller-candidate tournament, not an execution campaign |
| C6 LEARNED_POLICY_VALUE | UNKNOWN | NOT_EXECUTABLE_IN_HES_V0 | False | requires an external learned-policy platform run |
| C7 ROBUSTNESS | MIXED | NOT_EXECUTABLE_IN_HES_V0 | False | requires boundary sweeps; run `hes boundary` and feed the envelope |
| C8 GENERALITY | SUPPORTED_WITH_CAVEATS | NOT_EXECUTABLE_IN_HES_V0 | False | requires cross-adapter code-diff evidence, not an execution campaign |
| C9 REAL_TIME_VIABILITY | SUPPORTED_WITHIN_TESTED_SCOPE | NOT_EXECUTABLE_IN_HES_V0 | False | requires loop-latency rows, not canonical episode rows |
| C10 RUNTIME_RELIABILITY | SUPPORTED_WITHIN_TESTED_SCOPE | NOT_EXECUTABLE_IN_HES_V0 | False | requires the reliability fuzz/soak harness, not an execution campaign |
| C11 INCUMBENT_DIFFERENTIATION | SUPPORTED_WITHIN_TESTED_SCOPE | WEAKENED | True | vs competent_supervisor: preservation 0.9565217391304348 vs 0.8695652173913043 |
| C12 SIMULATION_RELEVANCE | MIXED | NOT_EXECUTABLE_IN_HES_V0 | False | requires cross-engine pairing, not a single-engine campaign |

## Recommended next experiments

- **counterfactual_sweep_on_unknown_attainability** (priority 1.268): removes uncertainty about attainability of the large UNKNOWN scenario mass. 139/181 scenarios have UNKNOWN attainability; every attainment-relative metric is bounded by this Cost: counterfactual forks on a stratified sample.
- **refusal_cause_decomposition** (priority 0.546): removes uncertainty about which refusals are correct vs productivity losses. coverage is 0.707; need per-refusal counterfactual classification Cost: fork each refused scenario once.
- **make_unexecutable_claims_executable** (priority 0.300): removes uncertainty about which registered claims are actually testable by HES. 8 of 12 claims have no executable test Cost: 1-2 days per claim to define an executable test.

## Caveats


## Not available from this evidence

- `task_progress`: canonical rows carry no progress channel
- `integrated_constraint_excursion`: violation rows are pruned per episode
- `collision_metrics`: no collision channel in the internal substrate
- `decision_latency`: runtime decision timing is not in canonical rows
- `evidence_completeness`: not derived from canonical rows
- `portability_engineering_leverage`: manifest-level metadata only

## Historical failure corpus

20 entries, 20 without a regression test.

