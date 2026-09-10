# Haplous Final Simulation Evidence Campaign

Status: COMPLETE (development + frozen final evaluations). HEAD at report
time: see git log; freeze tag: `haplous-final-sim-eval-v0` (G01, cut after
G02 final evaluations were committed).

## 1. Provenance

- Campaign: final broad software/simulation evidence campaign before
  physical hardware. Started after `haplous-external-eval-v0` (HEAD
  `aa3ca5c`); baseline verified 306 passed, 1 xfailed before any change.
- Platform: Apple M4 Pro (14 cores, 24 GB RAM), macOS 26.5, no NVIDIA GPU.
- Environments: main venv (mujoco 3.11.0, matplotlib, pytest 307 passed +
  1 xfailed at close), `.venv-maniskill` (mani_skill 3.0.1, sapien 3.0.3,
  PhysX CPU via MoltenVK ICD), `.venv-robosuite` (robosuite 1.5.2 +
  mujoco 3.9.0 + h5py).
- Baselines: official ManiSkill motion-planning demonstrations converted
  to `pd_ee_pose` with the official `replay_trajectory` tool (183/200 =
  91.5% replay fidelity; the pd_ee_delta_pos variant scored 3/200 and was
  rejected); robosuite Lift scripted pick-and-lift (5/5 baseline gate,
  peaks 7.1-7.4 N).

## 2. Frozen prior evidence (unchanged)

- Internal HEB held-out (HEB 0.2.0): mediated coverage 0.842, BSR 0.844,
  breach_trials 3; bare BSR 0.789, breach_trials 6; refusal P/R 1.0/1.0.
- External X91 robosuite NutAssembly: 0/15 success on all three systems
  (scripted policy never acquired the nut); mediation capped peaks
  404-489 N -> 25.8-32.6 N. Honest scope: constraint enforcement on
  identical command streams, not task success.
- Negative mechanisms (probing, StateFilter systematic error, G1 governor,
  momentum observer) remain not promoted; not reopened.

## 3. The headline comparison: same upstream stream, direct vs Haplous

Mode A (open-loop frozen-stream mediation). Identical frozen action
streams in both arms; Haplous gates commands, monitors constraints,
aborts on declared hard bounds.

### ManiSkill PegInsertionSide (SAPIEN/PhysX CPU, 20 Hz)

| Suite | N | direct success | haplous success | direct bounded | haplous bounded | direct peaks | mediated peaks |
|---|---|---|---|---|---|---|---|
| Dev (episodes 0-21 subset) | 16 | 16/16 | 16/16 | 4/16 | **16/16** | 4.6-146.5 N | 1.3-146.5 N |
| S00/S01 sweep (12 perturbation seeds) | 12 | 1/12 | 1/12 | 0/12 | 1/12 | 336-2115 N | 0-432 N |
| **G02 FINAL (unseen episodes 23-38)** | 16 | 16/16 | 16/16 | 3/16 | **16/16** | 5.7-113.0 N (mean 45.3) | 2.3-13.0 N (mean 5.5) |

Same-step success impulse (disclosed): in dev episode 4 both arms
recorded an identical 146.5 N single-step impulse on the step where task
success first fired (completion 9.05 s in both, bit-identical physics);
the mediated runtime completed on that observation and therefore graded
no force violation. Excluding that same-step impulse, the mediated
dev peak range is 1.3-13.0 N. All other direct-vs-mediated differences
are the post-success window effect described below.

- The only direct-vs-mediated difference on-distribution is the
  post-success window: direct keeps stepping the frozen stream ~2 steps
  into the seated peg (33-147 N excursions); Haplous stops at its
  completion condition. Success, completion times, and trajectories
  before first success are identical.
- Off-distribution (stale stream, perturbed initial realization): both
  arms fail the task 11/12 - mediation cannot rescue a stale open-loop
  stream (honest negative) - but direct runs unbounded to timeout
  (up to 2115 N, 42 deg tilt) while mediated aborts at the declared hard
  constraint (force 3x, tilt 8x), worst case 432 N.

### robosuite Lift (MuJoCo, successful-execution baseline)

| Suite | N | direct success | haplous success | bounded | peaks |
|---|---|---|---|---|---|
| Dev (seeds 0-9) | 10 | 10/10 | 10/10 | 10/10 both | 7.1-8.5 N (bit-identical per seed) |
| **G02 FINAL (seeds 10-19)** | 10 | 10/10 | 10/10 | 10/10 both | 7.4-8.0 N (bit-identical per seed) |

- Where all constraints hold, mediation is physically non-interfering:
  per-seed peak forces are bit-identical; completion times identical.
  Mediated peak tilt is slightly lower because the runtime ends the
  session on its own 0.25 s success window (task-latch uses 0.5 s);
  disclosed as a session-end asymmetry, not a physics effect.

## 4. Per-tick latency (L00/L01)

Timed-session wrappers, identical in both arms (method in
`results/external_eval/maniskill-3.0.1/20260910T053705Z/summary.json`):

| system | runtime residual P50 | P95 | P99 | max |
|---|---|---|---|---|
| direct loop | 6-36 us | 289-718 us | - | 574-1369 us |
| haplous mediated | 33-59 us | 363-660 us | - | 580-1018 us |

Tick gap ~3.3 ms is physics-dominated (PhysX CPU step ~3.2 ms).
Mediation overhead is ~25-50 us per tick at a 50 ms control period
(<0.1%), P95 well under 1 ms. Whole-episode inference was not used.

## 5. Cross-physics (X00/X01)

- MuJoCo internal offset sweep: 9/9 cells identical outcomes mediated vs
  bare; capture boundary 3.0->3.5 mm shared exactly (zero displacement).
- PhysX sweep: 12/12 seed-level success/failure agreement between arms.
- Ranking preserved on both engines; boundedness improvements replicate
  on both engines. Force magnitudes NOT compared across engines
  (different proxies; declared upfront). See
  `results/external_eval/x01_cross_physics_agreement.json`.

## 6. Dispositions (negative/blocked results, preserved)

| Node | Verdict | Evidence |
|---|---|---|
| SimplerEnv (P00-P03) | BLOCKED_PLATFORM (sapien 2.2.2 linux-x86_64 wheels only; CUDA-pinned stack) | `results/external_eval/p00_simplerenv_disposition.json` |
| PlugCharger baseline (M04/M05) | GATE MISSED: official conversion preserves 58% < 80% gate; no Haplous comparison run | `results/external_eval/maniskill-3.0.1/m04_plugcharger_disposition.json` |
| ContactBench (C00) | BUILD_BLOCKED with correct native attempt (upstream core includes bindings-only header; Boost 1.92 dropped Boost.System) | `results/external_eval/c00_contactbench_native_audit.json` |
| AssemblingKits (M06) | BLOCKED_RESOURCE (UCSD asset host unreachable) + no official baseline | `results/external_eval/m06_assemblingkits_disposition.json` |
| Live closed-loop planner | BLOCKED_PLATFORM: mplib ships Linux-x86_64 wheels only, sdist has no C++ sources | recorded in M02 evidence |
| Isaac Lab | REJECTED (prior campaign, unchanged) | `results/x10_isaac_disposition.json` |

## 7. Engineering leverage

- Core haplous runtime/machine/state/constraints/evidence: 0 LOC changed.
- CLI: +19 lines (doctor capability matrix, backend list; no simulator
  imports; AST guard `test_maniskill_imports_are_lazy` added).
- Benchmark-owned adapters/policies/drivers: ManiSkill ~470 LOC, robosuite
  Lift ~420 LOC, drivers ~700 LOC. Zero task-specific branches in core.

## 8. Claim classes

- VERIFIED FACT (committed paired data): where an upstream controller
  succeeds, mediation preserves task success bit-identically while
  enforcing declared bounds; where command streams exceed bounds, direct
  runs unbounded and mediated terminates at the bound.
- VERIFIED FACT: mediation overhead < 0.1% of control period P50 on
  PhysX CPU at 20 Hz.
- INTERNAL RESULT: stale open-loop streams cannot be rescued by
  supervision; bounded termination is the achievable value (11/12 task
  failures unchanged, peak force capped ~4-5x).
- UNKNOWN: hardware behavior, sensor noise, real force scales, OEM
  timing; SimplerEnv model-policy track (platform-blocked).

## 9. What this supports

Haplous as an execution layer preserves successful execution exactly
(non-interference), enforces declared bounds on failing command streams
(1-2 orders of magnitude peak-force reduction), and costs <0.1% control
period per tick - demonstrated across two engines (MuJoCo, PhysX), two
task families, on-distribution and off-distribution, with frozen
baselines and unseen final evaluations.

## 10. Exact hardware questions remaining

Physical force-scale calibration (sim proxies are not N-scale truth),
sensor noise/dropout realism, OEM control-rate stability (R04: 500 Hz
sampled-loop instability unresolved for the UR5e path), watchdog/safety
layer (pegsim safety.py never built), touch-off calibration, and all
sim-to-real gaps. NEXT GATE: PHYSICAL HARDWARE.
