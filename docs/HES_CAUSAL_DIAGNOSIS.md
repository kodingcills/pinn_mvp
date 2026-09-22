# HES Causal Diagnosis

Post-HES causal, quantitative explanation of the HES v0 qualification decision
surface. Diagnostic only: no optimization, no runtime-semantics change, no
workload switch. Every number is reproduced by
`scripts/hes_causal_diagnosis.py` from the frozen artifacts under
`results/hes/v3_hardening_qualification/`, with machine-readable output in
`results/hes_causal_diagnosis/`.

## 0. Executive summary

1. **Only 28 scenarios separate Haplous from the unmediated baseline on bounded
   success.** 131 direct bounded successes, 103 preserved, 28 lost; preservation
   = 0.786, reproducing HES G3 (0.786).
2. **26 of 28 losses (92.9%) are one mechanism**: the reachability refusal
   boundary, a generic runtime check triggered by a single per-task calibration
   constant (`max_initial_offset_m = 0.003` m).
3. **The HARMFUL headline is an evaluator defect.** 0 of 12
   HARMFUL forks have a bounded-success continuation; all
   12 continuations breach a declared hard
   contract. The classifier labels on *task* success, not *bounded* success.
4. **All 5 BENEFICIAL forks are protective stops**; none
   preserved a bounded success that the unmediated arm held.
5. **Attainability is the larger uncertainty than any capability defect.**
   215 scenarios are demonstrated attainable,
   306 demonstrated non-attainable, and
   479 remain UNKNOWN (47.9%).
6. **Recommended path: PATH D (evaluator defect)**, with PATH A
   (generic reachability defect) as the fallback if the fork sweep shows Haplous core
   cannot capture offsets the unmediated arm attains. The highest-information experiment
   is a single-pass fork sweep of the 142 eligible
   boundary_hi reach-rejections plus the priority UNKNOWN subset.

## 1. Scope and method

Inputs (read-only): `episodes.parquet` (1000 scenarios x 3 arms),
`interventions.jsonl` (120 forks), `hes_analysis.json`, `hes_gates.json`,
`manifest.json`, `provenance.json`. Population scenarios are reconstructed from
`benchmarks.heb.population` purely for construction axes (offset, force limit,
deadline); no episode is replayed and no physics is re-run.

Rules used throughout, all canonical to this repo:

- `bounded_success = task_success AND (no hard violations)` (`benchmarks/heb/metrics.py`).
- Preservation is computed against `direct_minimal` only (`hes/metrics.py`,
  `hes/gates.py`).
- A scenario is **attainable** only with evidence: some arm achieved
  `bounded_success`, or a refusal counterfactual satisfied every hard contract.
- A scenario is **non-attainable** only with evidence: benchmark construction
  (guaranteed-abort class, measured-infeasible deadline), measured capability
  incompatibility, or a valid refusal counterfactual that breached a hard
  contract. Task failure alone never implies non-attainability.

## 2. Frozen evidence identity

- experiment_id: `v3_hardening_qualification`
- git commit: `d6330193b080a0525e8b4c8dd05fdf5d95ca414b` (dirty: `True`)
- haplous_version: `0.5.0`, benchmark `internal-heb@0.2.0`
- reference_arm `haplous_mediated`, baseline_arm `direct_minimal`
- manifest sha256 `4b0df50373e2f58437c6f0f31ba8a6df14d7df0f2759042c0db48b22e61395d0`
- report digest ok: `None`

Artifacts are treated as immutable. New evidence is written only to
`results/hes_causal_diagnosis/` and references this identity. SHA-256 digests of
every input file are recorded in `frozen_evidence.json`.

## 3. Preservation waterfall

28 of 131 direct bounded successes were not
preserved. Mechanism Pareto:

| mechanism | episodes lost | share of loss | severity | addressability |
|---|---|---|---|---|
| reach_rejection | 26 | 92.9% | S1 | generically_addressable |
| deadline_rejection | 1 | 3.6% | S1 | generically_addressable |
| harmful_abort | 1 | 3.6% | S1 | generically_addressable |

The 26 reach rejections fire when
`INITIAL_OFFSET_M > caps.max_initial_offset_m`, with the cap fixed at 0.003 m in
`benchmarks/heb/tasks.py` from a development sweep. The population strata
straddle the measured boundary, which is why the loss concentrates there:

| stratum | n | direct BS | haplous BS | refused | reach rejections | preservation |
|---|---|---|---|---|---|---|
| interior | 528 | 81 | 142 | 81 | 0 | 98.8% |
| boundary_lo | 145 | 24 | 40 | 19 | 0 | 95.8% |
| boundary_hi | 153 | 24 | 0 | 153 | 153 | 0.0% |
| beyond | 174 | 2 | 0 | 174 | 174 | 0.0% |

`boundary_hi` (3.0-3.5 mm) is the discontinuity: Haplous refuses nearly all of
it while the measured capture boundary sits above 3.5 mm. All three mechanisms
are generically addressable runtime failures, class S1 (false abort /
productivity loss).

The two non-reach losses are individually informative:
- `fleet_insert_000793` (offset 2.87 mm, 25 N limit): the unmediated arm completed
  within contract, while Haplous aborted on a 49.7 N reading. This is a genuine
  mediation-induced divergence, not a calibration issue.
- `fleet_insert_000708` (offset 0.77 mm, deadline 1.8 s): refused with
  `deadline_unachievable` because the runtime's `expected_duration` is 2.0 s, but
  the unmediated arm completed within the 1.8 s deadline. The deadline model is
  miscalibrated in the same way as the reach constant.

## 4. HARMFUL fork adjudication

12 of 23 intervention forks were
classified HARMFUL; 6 UNNECESSARY, 5 BENEFICIAL.

| scenario | task family | declared force limit (N) | continuation peak force (N) | continuation violates limit |
|---|---|---|---|---|
| fleet_insert_000003 | peg_insertion | 25.0 | 2.7464780977257646 | False |
| fleet_slide_000007 | constrained_slide | 1.0 | 1.7357580112114475 | True |
| fleet_slide_000012 | constrained_slide | 1.25 | 1.7357580466895706 | True |
| fleet_slide_000013 | constrained_slide | 1.0 | 1.6738710958830618 | True |
| fleet_slide_000020 | constrained_slide | 1.0 | 1.7357580466895706 | True |
| fleet_slide_000024 | constrained_slide | 1.5 | 1.6901232890459545 | True |
| fleet_slide_000067 | constrained_slide | 1.25 | 1.7357580466895706 | True |
| fleet_slide_000070 | constrained_slide | 1.0 | 1.7841337048647803 | True |
| fleet_slide_000080 | constrained_slide | 1.0 | 1.7357580466895706 | True |
| fleet_slide_000103 | constrained_slide | 1.0 | 1.7370173244344422 | True |
| fleet_slide_000106 | constrained_slide | 1.5 | 1.7617865435075353 | True |
| fleet_slide_000118 | constrained_slide | 1.0 | 1.7357580466895706 | True |

**All 0 HARMFUL forks have a
bounded-success continuation**; 12 of
12 continue into a peak force above the declared hard limit
(or a missed deadline). The classifier
(`benchmarks/heb/counterfactual.py`) assigns HARMFUL when the continuation
*completes the task* without strictly worsening the metric at
`max(intervention_force, hard_limit)*1.1`; it never checks bounded success. The
11 slide forks are the clearest case: the nominal contact
force (~1.74 N) already exceeds the 1.0-1.5 N declared limit, so "completion" is
itself a breach. This contradicts the canonical bounded-success definition and
supersedes the HARMFUL label.

## 5. BENEFICIAL fork adjudication

| scenario | task family | haplous reason | cont. completed | cont. peak (N) | declared limit (N) | haplous BS |
|---|---|---|---|---|---|---|
| fleet_insert_000027 | peg_insertion | temporal_hold_budget_exhausted | False | 52.0021460014088 | 12.0 | False |
| fleet_insert_000045 | peg_insertion | hard_constraint_process_force | False | 14.950646568179053 | 12.0 | False |
| fleet_insert_000057 | peg_insertion | hard_constraint_process_force | False | 14.95057826921334 | 12.0 | False |
| fleet_insert_000075 | peg_insertion | hard_constraint_process_force | False | 17.268607392378883 | 2.0 | False |
| fleet_slide_000047 | constrained_slide | hard_constraint_process_force | True | 1.763355527551088 | 1.5 | False |

All 5 BENEFICIAL forks are protective stops
(four on insertion and one borderline on sliding), where the unmediated
continuation blows past the declared limit and does not complete the task.
**Zero BENEFICIAL forks preserved a bounded success.** The intervention sample
therefore contains no positive evidence that stopping itself buys bounded
performance; it contains evidence that stopping prevents severity escalation.

## 6. Mechanism matrix

| mechanism | beneficial | protective | harmful | unnecessary |
|---|---|---|---|---|
| hold_timeout | 1 | 1 | 0 | 1 |
| process_force_abort | 4 | 4 | 11 | 4 |
| deadline_abort | 0 | 0 | 1 | 1 |

## 7. Attainability decomposition

| label | n | fraction | evidence |
|---|---|---|---|
| demonstrated attainable | 215 | 21.5% | direct_minimal=131, competent_supervisor=18, haplous_mediated=65, refusal_counterfactual=1 |
| demonstrated non-attainable | 306 | 30.6% | capability_incompatibility=212, benchmark_construction=36, benchmark_construction=5, refusal_counterfactual=53 |
| unknown | 479 | 47.9% | no arm achieved bounded success and no valid counterfactual |

Attainment-relative metrics, each reported only over its labelled population:

| metric | population | value |
|---|---|---|
| authorization / execution capture | attainable | 0.8465 |
| false-refusal rate | attainable | 0.1488 |
| hazard rejection | non-attainable | 0.3497 |

The published HES AUTHORITY numbers (precision 0.319, recall 0.851) are diluted
by the 479 UNKNOWN cases. Over the labelled population, Haplous
captures 84.7% of attainable scenarios
and falsely refuses 14.9%. The
non-attainable population splits into benchmark-constructed impossibility
(guaranteed-abort class, infeasible deadline, sub-contact-force limits) and
measured capability incompatibility.

## 8. Decision-relevant UNKNOWN subset

479 scenarios are UNKNOWN; 364 carry a
claim they could move (false-refusal or false-abort estimate) and
179 are priority (offset 2.5-3.5 mm or deadline 1.0/1.8 s).
UNKNOWN composition:

| haplous disposition | n |
|---|---|
| REFUSED | 288 |
| ABORTED | 176 |
| COMPLETE | 15 |

Decision-relevant split: {'false_abort_estimate': 89, 'false_refusal_estimate': 275}. Priority bands: offset
196, deadline
54.

Recommended sweep:
- **Primary**: the 142 eligible boundary_hi reach-rejections (offset 3.0-3.5 mm, excluding the guaranteed-abort class); they are the single mechanism behind 26/28 preservation losses
- **Secondary**: the 179 priority UNKNOWN scenarios (offset 2.5-3.5 mm or deadline 1.0/1.8 s) that could move the false-refusal / false-abort estimates
- **Stopping rule**: Sequential forks, stop when the Benjamini-Hochberg-adjusted false-refusal estimate and capture interval widths stop changing. Do not fork the non-priority UNKNOWN mass; it cannot move a claim.

## 9. Responsibility decomposition

Using the comparator rule documented in `responsibility.json`:

| class | n | meaning |
|---|---|---|
| R0 not Haplous | 159 | upstream / controller / physics terminal failure, no runtime prevention role |
| R1 correct prevention | 107 | refusal of demonstrated non-attainable scenario |
| R2 mixed / contributing | 172 | protective stop on an already-breaching execution, attainability undemonstrated |
| R3 Haplous-caused | 33 | attainable scenario where Haplous missed a demonstrated bounded execution |
| UNKNOWN | 347 | refused or aborted without a recorded breach, attainability unknown |
| captured | 182 | Haplous achieved bounded success |

**The decision-relevant Haplous-caused mass is R3 = 33**
(32 refusals + 1 abort on demonstrated-attainable scenarios), i.e.
3.3% of the population, against
172 protective stops whose underlying breach is upstream. This is
the correct denominator for "how much is Haplous costing us".

## 10. Addressability

| category | n |
|---|---|
| generic Haplous core | 18 |
| task-specific calibration | 32 |
| upstream / controller | 331 |
| non-addressable | 289 |
| unknown | 330 |

The 32 reach/deadline refusals are triggered by per-task calibration constants (max_initial_offset_m, expected_duration) on a generic capability check. The failure mode is generic, its trigger is task-specific. The 17 no-breach aborts are the residual generic-core candidates.

## 11. Improvement ceiling

| bound | bounded success | rate | assumption |
|---|---|---|---|
| current system | 182 | 0.182 | as measured |
| conservative | 215 | 0.215 | recover all demonstrated-attainable misses |
| optimistic | 254 | 0.254 | + boundary_hi captured at the boundary_lo rate |

Conservative = 182 + 32
refusals + 1 abort. Optimistic adds
39 from the
142 eligible boundary_hi scenarios at the
boundary_lo capture rate 0.276. Neither
assumes UNKNOWN cases are fixable, and both leave
159 upstream and
107 correct-refusal
failures untouched.

## 12. Recommended path

**Recommended path: PATH D.** PATH D - evaluator defect: the counterfactual classifier mislabels HARMFUL on task success alone, and the benchmark's reachability constant max_initial_offset_m is inconsistent with the offset population it generates.

Fallback: PATH A - if the boundary_hi fork sweep shows Haplous core genuinely cannot capture offsets the unmediated arm attains, the reach model becomes a generic Haplous defect.

PATH B (attainability uncertainty) is the largest raw mass (479 UNKNOWN) but it is an evidence gap, not a defect: it bounds the ceiling rather than explaining the observed G3 loss.

PATH C (intrinsic tradeoff) is not supported: zero BENEFICIAL forks preserved a bounded success, so no measured protection requires the reach refusal.

## 13. Highest-information next experiment

- **Experiment**: single-pass fork sweep of the 142 eligible boundary_hi (3.0-3.5 mm) reach-rejections (excluding the guaranteed-abort class), plus the priority UNKNOWN subset, once each
- **Why**: It is the single mechanism behind 26/28 preservation losses and it directly tests the calibration hypothesis. If the unmediated continuation completes within contract materially more often than the 0.003 cap assumes, the constant is wrong; if not, the reach model is a genuine Haplous limitation (PATH A).
- **Cost**: minutes to a few hours of managed forks; no new population, no new episodes
- **Falsifies**: that max_initial_offset_m = 0.003 matches the machine's true capture boundary

Prerequisites that do not require the sweep:
- Fix the counterfactual classifier so HARMFUL requires a bounded-success
  continuation; mark historical HARMFUL labels superseded, do not silently
  relabel.
- Recover the lost violation fields in `interventions.jsonl` (`slim_artifacts`
  appears applied twice; `n_hard_violations` is 0 for every fork).

## 14. Alternatives and what would refute them

- **A generic defect**: refuted as the *dominant* explanation. A general runtime
  defect would not concentrate 92.9% of loss in one capability check at one
  offset band. Residual generic risk remains in the 17 no-violation aborts and
  the 347 UNKNOWN mass.
- **C intrinsic tradeoff**: not supported. No BENEFICIAL fork preserved a
  bounded success, and the reach refusal is not required to prevent any measured
  violation. If the boundary_hi sweep shows Haplous cannot capture those offsets
  while the unmediated arm can, C becomes live for that band.
- **D evaluator defect**: confirmed for the HARMFUL axis and required as a
  precondition for any re-measurement. It does not by itself explain the G3
  loss.
- **B attainability uncertainty**: confirmed as the dominant uncertainty; it
  bounds every headline number in the report.

## 15. Threats to validity

- Attainability labels are evidence-bounded; 479 scenarios stay
  UNKNOWN and are never imputed.
- The intervention fork sample is 23 of 1000
  scenarios and is not a random sample of the population.
- The 2 inconsistent `TRUE_REFUSAL` forks whose direct arm achieved bounded
  success indicate residual label noise in the fork corpus.
- Policy conclusions are specific to HES v0 and the frozen population seed
  `20260916`; results must not be pooled with a different design
  version.
- The source tree was **dirty** at measurement time (`git_dirty = True`);
  the commit identifies the baseline, not the exact working tree. This limits
  exact reproduction of the original episodes but not of this report, which reads
  only committed artifacts.

## Artifacts

`results/hes_causal_diagnosis/`: `frozen_evidence.json`,
`preservation_waterfall.json`, `fork_adjudication.json`, `mechanism_matrix.json`,
`attainability.json`, `attainment_metrics.json`, `unknown_subset.json`,
`responsibility.json`, `addressability.json`, `improvement_ceiling.json`,
`decision.json`.
