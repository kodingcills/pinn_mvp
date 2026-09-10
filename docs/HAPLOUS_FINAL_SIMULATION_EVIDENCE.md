# Haplous Final Simulation Evidence Campaign

Status: COMPLETE after adjudication. The A00 independent review and A01
red-team review identified three methodology defects in the first-pass
evidence (different grading formulas per arm, unequal stopping semantics,
misaligned latency pairing); the affected evaluations were rerun with
harmonized stopping semantics, identical grading formulas, and corrected
latency pairing. All headline numbers below are from the corrected
(v2/v3) committed artifacts. Adjudication record:
`results/external_eval/a00_a01_review_adjudication.json`. Freeze tag:
`haplous-final-sim-eval-v0` (re-cut after adjudication).

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
  (scripted policy never acquired the nut); mediation stopped executions
  at peaks 25.8-32.6 N where unmediated ran to 404-489 N. Honest scope:
  constraint enforcement on identical command streams, not task success.
- Negative mechanisms (probing, StateFilter systematic error, G1 governor,
  momentum observer) remain not promoted; not reopened.

## 3. The headline comparison: same upstream stream, direct vs Haplous

Mode A (open-loop frozen-stream mediation). Identical frozen action
streams; IDENTICAL stopping semantics (both arms end on the first
benchmark-success observation; reviewer-harmonized after A00/A01) and
IDENTICAL grading (both arms graded on recorded physical peaks:
3D force norm vs 25 N, tilt vs 30 deg).

### ManiSkill PegInsertionSide (SAPIEN/PhysX CPU, 20 Hz)

| Suite | N | direct succ | hap succ | direct bnd | hap bnd | direct peaks | hap peaks | step parity |
|---|---|---|---|---|---|---|---|---|
| Dev (episodes 0-21 subset) | 16 | 16/16 | 16/16 | **15/16** | **15/16** | 4.6-146.5 N | 4.6-146.5 N | 16/16 |
| S00/S01 sweep (12 perturbation seeds) | 12 | 1/12 | 1/12 | 0/12 | 1/12 | 336-2115 N | 0-432 N | n/a (failure arms) |
| **G02v2 FINAL (conversion-success-qualified holdout, episodes 23-38)** | 16 | 16/16 | 16/16 | **16/16** | **16/16** | <= 13.0 N | <= 13.0 N | 16/16 |

- Under identical stopping and grading, mediation is NON-INTERFERING:
  per-pair peak forces are bit-identical, step counts identical (parity
  16/16 in both suites), success and completion identical.
- The single dev bounded failure is episode 4: a 146.5 N same-step
  success impulse recorded identically in BOTH arms (completion 9.05 s
  both); it breaches the 25 N bound identically in both gradings.
- Cohort scope (A01): the paired cohort is a baseline-qualified holdout
  (episodes whose official-stream conversion succeeded), NOT an
  outcome-blind unseen sample; it answers "does mediation preserve
  already-successful converted streams", not general ManiSkill task
  performance.

### robosuite Lift (MuJoCo, successful-execution baseline)

| Suite | N | direct | haplous | bounded | peaks |
|---|---|---|---|---|---|
| Dev v2 (seeds 0-9) | 10 | 10/10 | 10/10 | 10/10 both | 7.1-8.5 N, bit-identical per seed |
| **G02v2 FINAL (seeds 10-19)** | 10 | 10/10 | 10/10 | 10/10 both | 7.4-8.0 N, bit-identical per seed |

- Lift is a safe-path pass-through test (A01): no requirement is
  challenged, no command gated, no abort exercised. It proves
  pass-through non-interference on recorded scalars, not enforcement
  value.

### Off-distribution (stale stream, perturbed initial realization)

Both arms fail the task 11/12: supervision cannot rescue a stale
open-loop stream (honest negative; single frozen episode, 11 mismatched
seeds - A01 correctly limits generalization). Direct runs unbounded to
the step timeout (up to 2115 N, 42 deg tilt, 228 steps); mediated stops
on the first observation after a sampled breach (recorded session peaks
0-432 N over 49-179 steps). Because the arms execute different numbers
of steps, these peaks are NOT an exposure-matched force reduction and no
ratio is claimed (A01): the demonstrated property is bounded termination
vs unbounded continuation. Mediated peaks still exceed the 25 N
requirement (worst 431.8 N) because enforcement is sampled at
observation rate - a one-tick overshoot is structurally possible and
was measured.

## 4. Per-tick latency (L00v3, corrected pairing)

Timing wrappers identical in both arms; per-tick NON-PHYSICS time
(tick gap minus its paired physics step, `gaps[i] - steps[i+1]`; 0
negative samples in 1412 pooled ticks; raw rows committed in
`results/external_eval/maniskill-3.0.1/20260910T064409Z/latency_rows.json`):

| system | P50 | P95 | P99 | max | n ticks |
|---|---|---|---|---|---|
| direct loop | 92.5 us | 134.2 us | 166.0 us | 261.6 us | 711 |
| haplous mediated | 122.3 us | 160.3 us | 180.4 us | 236.3 us | 701 |

Mediation overhead: ~30 us/tick P50, ~26 us P95, ~14 us P99 - under
0.1% of the 50 ms control period (0.06% P50, 0.04% P99). The first-pass
claim is restored on corrected instrumentation (A00/A01 defect fixed
and re-verified).

## 5. Cross-physics (X00/X01)

- MuJoCo internal offset sweep: 9/9 cells identical outcomes mediated vs
  bare; capture boundary 3.0->3.5 mm shared exactly (zero displacement).
- PhysX sweep: 12/12 seed-level success/failure agreement between arms.
- Ranking preserved on both engines; bounded termination replicates on
  both engines (wording per A00: "aborts earlier and reduces exposure",
  not "caps force"). Force magnitudes NOT compared across engines
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
  Lift ~420 LOC, drivers ~900 LOC. Zero task-specific branches in core.

## 8. Claim classes (post-adjudication)

- VERIFIED FACT (committed paired data, harmonized stopping + grading):
  where the upstream stream succeeds, mediation is non-interfering -
  identical success, identical completion times, bit-identical per-pair
  peak forces, identical step counts (32/32 parity across the two
  ManiSkill suites, 20/20 Lift).
- VERIFIED FACT: mediation overhead ~30 us/tick P50 (< 0.1% of the 50 ms
  control period) on PhysX CPU at 20 Hz, measured with alignment-proof
  instrumentation (0 negative samples, raw rows committed).
- VERIFIED FACT: on failing command streams, mediated execution stops on
  the first observation after a sampled hard-constraint breach while the
  unmediated arm continues unbounded to timeout (11/12 vs 11/12 task
  failures; recorded session peaks 0-432 N vs 336-2115 N; NOT an
  exposure-matched ratio).
- INTERNAL RESULT: one stale open-loop stream was not rescued under 11
  mismatched initializations; bounded termination, not recovery, is the
  demonstrated failure-mode value.
- UNKNOWN: hardware behavior, sensor noise, real force scales, OEM
  timing, sim-to-real correlation for Haplous, model/VLA track
  (platform-blocked).

## 9. What this supports

Haplous as an execution layer is a non-interfering pass-through where the
upstream stream succeeds (bit-identical execution and outcomes), and a
bounded-termination layer where it does not (stops at the first sampled
breach instead of running unbounded), at ~30 us/tick overhead - shown
across two engines (MuJoCo, PhysX), two task families, on-distribution
and off-distribution, with frozen baselines and a baseline-qualified
holdout final. It does NOT demonstrate: force capping below the declared
bound (sampled overshoot measured up to 431.8 N), rescue of failing
streams, or model-policy supervision.

## 10. Exact hardware questions remaining

Physical force-scale calibration (sim proxies are not N-scale truth;
sampled enforcement vs continuous safety), sensor noise/dropout realism,
OEM control-rate stability (R04: 500 Hz sampled-loop instability
unresolved for the UR5e path), watchdog/safety layer (pegsim safety.py
never built), touch-off calibration, and all sim-to-real gaps.
NEXT GATE: PHYSICAL HARDWARE.
