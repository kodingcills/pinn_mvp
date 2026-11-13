<!-- hes-report-digest: 43574f1cd7d001d2 -->

# HES v0 report: m4-hes-m4-e2-campaign-stress

320 scenarios, 1280 episodes, arms ['bare_behavior', 'competent_supervisor', 'g1_incumbent', 'haplous_mediated'], unmediated baseline `bare_behavior`.

## Execution authority

| arm | bounded success | coverage | execution capture | false refusal | violation given acceptance |
|---|---|---|---|---|---|
| bare_behavior | 0.200 (n=320) | 1.000 (n=320) | 0.762 (n=84) | 0.000 (n=84) | 0.756 (n=320) |
| competent_supervisor | 0.200 (n=320) | 1.000 (n=320) | 0.762 (n=84) | 0.000 (n=84) | 0.356 (n=320) |
| g1_incumbent | 0.188 (n=320) | 1.000 (n=320) | 0.714 (n=84) | 0.000 (n=84) | 0.397 (n=320) |
| haplous_mediated | 0.188 (n=320) | 1.000 (n=320) | 0.714 (n=84) | 0.000 (n=84) | 0.397 (n=320) |

## Attainability

counts: UNKNOWN=236, ATTAINABLE=84
provenance: NONE=236, PAIRED_BOUNDED_BASELINE=84

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
| competent_supervisor | 1.000 (n=64) | 0.000 (n=64) | 0.000 (n=64) | 0.000 (n=64) | -0.013 (n=64) |
| g1_incumbent | 0.625 (n=64) | 0.375 (n=64) | 0.375 (n=64) | 0.000 (n=64) | 0.013 (n=40) |
| haplous_mediated | 0.625 (n=64) | 0.375 (n=64) | 0.375 (n=64) | 0.000 (n=64) | -0.356 (n=40) |

## Failure attribution (run arm)

by layer: EXECUTION_RUNTIME=169, UNKNOWN=69, CONTROLLER=22
by runtime subcause: hold_timeout=112, recovery_failure=33, constraint_abort=24
unknown: 69 of 260

## Responsibility decomposition

Refusals are charged as a caused loss only when the scenario was 
demonstrated attainable; UNKNOWN-attainability refusals are tracked separately.

correct_refusal=0, false_refusal_loss=0, haplous_caused_loss=112, haplous_unprevented_violation=57, runtime_refusal_attainability_unknown=0, upstream_controller_physics_limit=22, benchmark_evaluator=0, infrastructure=0, unknown=69

## Failure Pareto (run arm, top 10)

| cause | count | severity | haplous responsibility | addressability | priority |
|---|---|---|---|---|---|
| EXECUTION_RUNTIME/recovery_failure | 33 | S3=33 | 1.000 | generically_addressable | 1.031 |
| EXECUTION_RUNTIME/constraint_abort | 24 | S3=24 | 1.000 | generically_addressable | 0.750 |
| EXECUTION_RUNTIME/hold_timeout | 112 | S2=112 | 1.000 | generically_addressable | 0.700 |
| UNKNOWN/- | 69 | S3=69 | 0.000 | unknown | 0.000 |
| CONTROLLER/- | 22 | S2=21, S3=1 | 0.000 | technically_addressable | 0.000 |

## Hard gates

qualified: **False**
- `G1` integrity: PASS (observed [], threshold no failing integrity check)
- `G2` no_more_conditional_violation_than_baseline: PASS (observed runtime=0.397, baseline=0.756, threshold runtime <= baseline (bare_behavior))
- `G3` non_interference: FAIL (observed 0.625, threshold >= 0.95)
- `G4` false_refusal_ceiling: PASS (observed 0.000, threshold <= 0.2)
- `G5` reproducibility_provenance: PASS (observed ['benchmark', 'benchmark_version', 'cpu_count', 'dependencies', 'episode_schema_version', 'git_commit', 'git_dirty', 'haplous_version'], threshold provenance present)

## Integrity

passed: **True**
- shortcut_solvability: PASS - always-accept does not reach perfect contract-safe success
- mirrored_scenarios: PASS - no mirrored pairs supplied
- small_sample_intervals: PASS - rates on small samples with a point value must carry an interval
- metric_gaming: PASS - violation-after-acceptance rate 0.477 within ceiling 0.5

## Benchmark health

healthy: **False**
- discriminating_power: PASS - 0.863 of scenarios grade identically for every arm
- degenerate_scenarios: PASS - 40 trivially solved and 236 unsolved by every arm (informational; not necessarily a defect)
- population_balance: FAIL - 2 task families, 0 offset strata across 320 scenarios
- grader_consistency: PASS - bounded success never coexists with a recorded hard violation

## Claim ledger

| claim | registry | HES | executable | notes |
|---|---|---|---|---|
| C1 NON_INTERFERENCE | SUPPORTED_WITHIN_TESTED_SCOPE | UNKNOWN | True | baseline direct_upstream_stream has no paired arm in this run |
| C2 BOUNDED_EXECUTION | SUPPORTED_WITH_CAVEATS | SUPPORTED_WITH_CAVEATS | True | vs bare_behavior: violation 0.397 vs 0.756; coverage 1.000 vs 1.000 |
| C3 NET_SYSTEM_VALUE | SUPPORTED_WITHIN_TESTED_SCOPE | WEAKENED | True | haplous_mediated does not dominate bare_behavior on (BSR, coverage, violation) |
| C4 RECOVERY_VALUE | SUPPORTED_WITHIN_TESTED_SCOPE | NOT_EXECUTABLE_IN_HES_V0 | False | recovery channels are absent from canonical episode rows |
| C5 CONTROLLER_SUPERIORITY | UNKNOWN | NOT_EXECUTABLE_IN_HES_V0 | False | requires a controller-candidate tournament, not an execution campaign |
| C6 LEARNED_POLICY_VALUE | UNKNOWN | NOT_EXECUTABLE_IN_HES_V0 | False | requires an external learned-policy platform run |
| C7 ROBUSTNESS | MIXED | NOT_EXECUTABLE_IN_HES_V0 | False | requires boundary sweeps; run `hes boundary` and feed the envelope |
| C8 GENERALITY | SUPPORTED_WITH_CAVEATS | NOT_EXECUTABLE_IN_HES_V0 | False | requires cross-adapter code-diff evidence, not an execution campaign |
| C9 REAL_TIME_VIABILITY | SUPPORTED_WITHIN_TESTED_SCOPE | NOT_EXECUTABLE_IN_HES_V0 | False | requires loop-latency rows, not canonical episode rows |
| C10 RUNTIME_RELIABILITY | SUPPORTED_WITHIN_TESTED_SCOPE | NOT_EXECUTABLE_IN_HES_V0 | False | requires the reliability fuzz/soak harness, not an execution campaign |
| C11 INCUMBENT_DIFFERENTIATION | SUPPORTED_WITHIN_TESTED_SCOPE | WEAKENED | True | vs competent_supervisor: preservation 0.625 vs 1.0 |
| C12 SIMULATION_RELEVANCE | MIXED | NOT_EXECUTABLE_IN_HES_V0 | False | requires cross-engine pairing, not a single-engine campaign |

## Recommended next experiments

- **counterfactual_sweep_on_unknown_attainability** (priority 1.238): removes uncertainty about attainability of the large UNKNOWN scenario mass. 236/320 scenarios have UNKNOWN attainability; every attainment-relative metric is bounded by this Cost: counterfactual forks on a stratified sample.
- **capture_improvement_on_attainable_scenarios** (priority 0.686): removes uncertainty about why demonstrated-attainable execution is not captured. execution capture is 0.714; the gap is a runtime regression target Cost: paired replay of the missed capturable scenarios.
- **incumbent_differentiation_head_to_head** (priority 0.600): removes uncertainty about whether the runtime differentiates from a competent supervisor. BSR 0.188 vs supervisor 0.200; no differentiation shown Cost: preregistered head-to-head on a fresh population.

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

