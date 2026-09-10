# Haplous Evaluation Engine Campaign

Status: infrastructure complete and verified; first evidence waves run.
Baseline: tag `haplous-final-sim-eval-v0` (659707f), 307 passed + 1
xfailed, verified at campaign start. Freeze tag:
`haplous-eval-engine-v1`.

## 1. Claim registry status

`evaluation/claims.yaml` (machine-readable, validated by
`evaluation/registry.py`, 12 claims, each with preregistered
support/weakening/falsification conditions, confounders, and evidence
links). Statuses as of this campaign:

| Claim | Status |
|---|---|
| C1 Non-interference | SUPPORTED_WITHIN_TESTED_SCOPE |
| C2 Bounded execution | SUPPORTED_WITH_CAVEATS |
| C3 Net system value | MIXED |
| C4 Recovery | SUPPORTED_WITHIN_TESTED_SCOPE |
| C5 Controller superiority | REFUTED_WITHIN_TESTED_SCOPE |
| C6 Learned-policy value | UNKNOWN (platform-blocked) |
| C7 Robustness | MIXED |
| C8 Generality | SUPPORTED_WITH_CAVEATS |
| C9 Real-time viability | SUPPORTED_WITHIN_TESTED_SCOPE |
| C10 Runtime reliability | SUPPORTED_WITHIN_TESTED_SCOPE |
| C11 Incumbent differentiation | MIXED |
| C12 Simulation relevance | MIXED |

Frozen negative mechanisms remain withdrawn: R01/R02 probing path, G1
governor, momentum observer O2. Nothing was silently revived.

## 2-5. Benchmark coverage, policies, episode counts, task success, bounded success

Permanent layers and their current evidence (all committed artifacts):

| Layer | Benchmark | N | Result |
|---|---|---|---|
| L0 software | property fuzz | 1,001,695 transitions | 6/6 invariants PASS (`results/l0_fuzz_summary.json`) |
| L0 soak | 100k clean + 20k aborted cycles | 120,000 | 0 failures, exact archive lineage, deterministic replay (`results/soak_100k_summary.json`) |
| L1-L2 internal | HEB dev, three-arm | 27 scenarios x 3 arms | table below |
| L2 external | ManiSkill peg / robosuite Lift (prior waves) | 32 + 20 paired | unchanged, non-interference bit-identical |
| L3 learned policy | Colosseum V2 | 0 | BLOCKED_PLATFORM (`results/external_eval/c01_colosseumv2_disposition.json`) |

Three-arm development comparison (27 dev scenarios, paired shared
realization, benchmark-owned grading):

| System | task success | BSR among accepted | coverage | violations |
|---|---|---|---|---|
| bare_behavior | 22/27 = 0.815 | 15/27 = 0.556 | 1.0 | 8 |
| competent_supervisor | 14/27 = 0.519 | 14/27 = 0.519 | 1.0 | 7 |
| haplous_mediated | 12/27 = 0.444 | 11/22 = 0.500 | 0.815 | 6 |

McNemar (paired): supervisor vs bare task success 14 vs 22, p=0.0078;
bounded success 14 vs 15, p=1.0. Supervisor vs Haplous: task success
14 vs 12, p=0.69; bounded 14 vs 11, p=0.375.

## 6. Risk vs coverage

Unchanged from the final-simulation campaign: mediated coverage 0.842,
BSR 0.844, breach_trials 3; bare BSR 0.789, breach 6 (held-out, HEB
0.2.0). Refusal P/R 1.0/1.0. Reproduced exactly by the new metric stack
(`tests/test_evaluation_aggregate.py`).

## 7-8. Intervention value and recovery

Counterfactual fork implemented (`benchmarks/heb/counterfactual.py`):
full physics state + task-logic state captured at every runtime-initiated
stop; restored-state continuation proven BIT-IDENTICAL to uninterrupted
execution (`tests/test_counterfactual_fork.py`).

Internal insertion family, 18 scenarios, 4 genuine runtime aborts forked
(`results/counterfactual_internal_summary.json`):

| Classification | Count | Detail |
|---|---|---|
| BENEFICIAL | 1 | force-limit stop: direct continuation drove to 14.95 N vs 2 N limit |
| HARMFUL | 3 | freshness/rejection stops cost completions (2.18 N healthy seating) the unmediated arm records |
| UNNECESSARY | 0 | |

Preregistered rule: worsening credited only against a declared hard
limit; with no declared limit, completion dominates (a stop of a
completing execution is HARMFUL, honest per the C2 caveat).

Recovery value remains scenario-level (deterministic physics; bare from
t0 is the exact counterfactual when no command gating precedes
recovery). Stale open-loop rescue remains a negative (0/11, prior
campaign).

## 9-11. Failure taxonomy, physical metrics, temporal tails

Failure taxonomy (`evaluation/taxonomy.py`): S0-S5 severity x closed
root-cause set, mechanical reason mapping, UNKNOWN default. Three-arm
failure mass (dev 27): supervisor 9 EXECUTION_SEMANTICS + 2 PHYSICS +
1 CONTROL; Haplous 11 EXECUTION_SEMANTICS + 2 UNKNOWN + 2 PHYSICS +
1 CONTROL; bare 9 UNKNOWN (behavior timeouts) + 2 PHYSICS + 1 CONTROL.

Physical/process and temporal tails: unchanged from prior campaigns
(sampled enforcement overshoot to 431.8 N disclosed; ~30 us/tick P50,
P99 180 us mediation overhead with alignment proof).

## 12-13. Controller tournament, learned-policy result

C5: no new Haplous controller candidate was designed - the failure mass
does not concentrate on control (taxonomy above: 1 CONTROL-class trial
per arm, the modeled tilt guard, identical across arms). Per the
promotion rule, no candidate is justified by current evidence.

C6: BLOCKED. Colosseum V2 canonical repo verified (jstmn/ColosseumV2);
official ACT protocol is physx_cuda-only (200 eps/task/perturbation,
11.5 h on RTX 4090); checkpoints 2 x 1.19 GB; host disk 5.1 GiB free,
no NVIDIA GPU. Exact runnable package committed
(`scripts/run_colosseum_v2_official.sh`); gate rule recorded: official
ACT baseline must be reproduced before any Haplous comparison.

## 14-15. Incumbent supervisor result

COMPETENT SUPERVISOR implemented (`benchmarks/heb/systems.py`):
behavior + independent force/tilt/freshness/deadline watchdogs from
declared requirements only; no negotiation, recovery, capability
semantics, or evidence trail.

Honest finding: the supervisor MATCHES Haplous on bounded success and
BEATS both bare and Haplous on task success in this dev family; it
kills bare's task success (p=0.008) without improving bounded success.
Discordants: the supervisor wins on capability-negotiation refusals
(150 N limit, 2 s deadline - Haplous refuses requests its capability
matrix cannot honor) and persistent command rejection; Haplous wins on
dropout tolerance (one lost sample stops the supervisor; Haplous
degrades and recovers) and microsecond refusal of infeasible requests.
At this n, no demonstrated Haplous superiority over the competent
supervisor exists in this family. This is recorded as a serious result
against differentiation, not tuned away.

## 16-17. Robustness envelopes, compound perturbations

Single-axis envelopes unchanged (capture 3.0-3.5 mm shared exactly,
clearance [0.1, 0.5] mm, facets n>=16, stale 20 ms, dropout 20 ms, kick
0.3-0.4 m/s). NEW compound grid (offsets 2.5/3.0/3.5 mm x
none/dropout_small/dropout_large/stale_mid/kick_y, 15 cells, three
arms, `results/compound_perturbation_summary.json`): NO multiplicative
interaction found - stressor effects are additive and uniform across
offsets; the capture boundary is not shifted by timing stressors. The
supervisor flips on every timing stressor including the 5 ms dropout
(9/12 cells); Haplous only on large stressors (6/12); bare's flips are
grading catches of blind windows.

## 18-19. Cross-physics transfer, reliability/soak

Cross-physics: unchanged (MuJoCo vs PhysX ranking preserved,
boundedness replicates, force proxies non-comparable).
Reliability/soak: NEW at campaign scale - 1,001,695 transitions +
120,000 soak cycles, zero violations/failures.

## 20-23. Engineering leverage, development iterations, kept/reverted

Delta vs `659707f`: +4179 LOC. Guarded haplous core: 0 LOC. Adapter:
+37 (snapshot/restore - machine-state serialization, generic). CLI:
+94 (doctor --full). Benchmark layer: +120 (SupervisedSystem) +37
(counterfactual module) +5 (build_realization hooks). Evaluation engine:
~900 LOC + ~600 test LOC + ~900 driver LOC.

Kept: all of the above (each verified by tests + rerun evidence).
Reverted in-flight: two classification-rule bugs (zero-baseline
worsening; success-path stops recorded as interventions) - fixed, not
kept. Layering fix: counterfactual module moved from evaluation/ to
benchmarks/heb (evaluation/ stays simulator-free).

## 24-26. Negative results, strongest evidence for/against

Negatives this campaign:
1. Compound perturbation: no interaction found (additive stressors).
2. Counterfactual value in the internal family: 3 of 4 stop-interventions
   HARMFUL (temporal-integrity stops cost completions the blind arm
   records). Consistent with the C2 honesty note; the freshness/rejection
   stops bought temporal integrity, not completion preservation.
3. C11: the competent supervisor matches Haplous bounded success and
   beats it on task success in the dev family.

Strongest FOR Haplous: non-interference (bit-identical peaks, 32/32 +
20/20 parity), bounded termination vs unbounded continuation at ~30
us/tick across two engines, 1M+ transition reliability, refusal
precision/recall 1.0, dropout tolerance (only arm completing through a
5 ms blackout).

Strongest AGAINST: the competent supervisor result (C11) - simple
watchdogs achieve equivalent bounded success in the internal family;
counterfactual HARMFUL mass 3/4 on this family; capability-refusal
semantics cost task success where a supervisor would just run.

## 27. Claim statuses

See section 1; registry is machine-checked
(`tests/test_evaluation_structure.py`).

## 28. What remains unknown

- C6 learned-policy value (platform-blocked; package ready).
- C11 differentiation at scale and on external tasks (the single most
  important open question this campaign sharpened).
- C5 controller superiority (no promoted candidate; deferred from
  evidence).
- Hardware anything (unchanged gate).

## 29. Physical hardware experiments now required

Unchanged from the v0-sim report (500 Hz sampled-loop stability, F/T
accuracy, touch-off, watchdog, evidence instrumentation), plus: real
freshness/dropout statistics to price the temporal-integrity stops whose
counterfactual cost this campaign measured directly.

## Reproduction

```
python scripts/measure_l0_fuzz.py              # L0 fuzz, 1M transitions
python scripts/measure_soak_100k.py            # 120k soak cycles
python scripts/measure_counterfactual_internal.py
python scripts/measure_competent_supervisor.py
python scripts/measure_compound_perturbations.py
python -m pytest tests/test_evaluation_*.py tests/test_counterfactual_fork.py -q
```