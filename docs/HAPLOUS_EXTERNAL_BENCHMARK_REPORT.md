# Haplous External Benchmark Report

Status: DRAFT — X92/X93 review sections pending; verdicts will be
adjudicated before this document becomes canonical.

## 1. Provenance

- Campaign: external benchmark, robustness and SOTA falsification, after
  the pre-hardware freeze `haplous-prehardware-eval-v0`.
- Start HEAD `9d196ea`; freeze tag `haplous-external-eval-v0` = `af485e9`;
  final external evaluation committed after the freeze.
- Platform: Apple M4 Pro (14 cores, 24 GB), macOS, no NVIDIA GPU.
- Environments: main venv (mujoco 3.11.0, pytest), robosuite venv
  (robosuite 1.5.2 + mujoco 3.9.0, macOS CPU headless), clean-room venv
  (wheel-only install).
- Baseline at campaign start: 278 passed, 1 xfailed; at freeze: 306
  passed, 1 xfailed.

## 2. Prior HEB result status (X00)

S16 (independent review): SUPPORTED WITH CAVEATS; S17 (red team): 2
violations addressed by the HEB 0.2.0 regrade; no methodology-invalidating
defect. Prior held-out evaluation stands VALID. Canonical 0.2.0 regrade
numbers: mediated coverage 0.842, BSR 0.844, breach_trials 3; bare BSR
0.789, breach_trials 6; refusal P/R 1.0/1.0.

## 3. External frameworks evaluated

| Framework | Result | Disposition |
|---|---|---|
| robosuite 1.5.2 (mujoco 3.9.0) | integrated, dev+qual+FINAL paired runs | VERIFIED FACT (integration) |
| Isaac Lab / Isaac Sim | no darwin wheels, no NVIDIA GPU, no CUDA-in-Docker-on-macOS | REJECTED, evidence-backed (results/x10_isaac_disposition.json) |
| ContactBench (Simple-Robotics) | C++ clone-and-build, no PyPI wheel, no insertion harness | REJECTED, evidence-backed (results/x41_contactbench_disposition.json) |
| Drake 1.46.0 | installed in temp venv; hydroelastic sphere-plane probe ran | PROBED_NOT_ADOPTED (results/x41_drake_probe.json) |
| ROS/MoveIt | not forced | deferred to hardware campaign per spec (section 37) |

## 4. Incumbent baselines and the external final result matrix (X91)

FINAL run `20260909T213850Z` (frozen tag, seeds 10-24, 15 paired seeds x 3
systems = 45 trials), robosuite NutAssemblySquare, Panda + official
OSC_POSE composite (kp=150, machine-owned):

| System | success | bounded | refusals | mean peak F | max peak F |
|---|---:|---:|---:|---:|---:|
| direct_minimal | 0/15 | 0/15 | 0 | 404.3 N | 488.8 N |
| strong_osc (same loop, full telemetry) | 0/15 | 0/15 | 0 | 404.3 N | 488.8 N |
| haplous_mediated | 0/15 | 0/15 | 0 | 25.8 N | 32.6 N |

Mediated outcome reasons: 9x hard force constraint, 6x hard tilt
constraint. All 15 mediated trials terminated on declared hard
constraints instead of driving to 400-490 N peaks.

**Interpretation limits (stated in full)**: the scripted nut-acquisition
policy never acquired the nut (0/15 everywhere, after two permitted
redesigns in dev). The measured distinction is therefore NOT task
success; it is constraint enforcement on identical command streams: the
unmediated systems push through to 16-20x the declared 25 N force limit
while the mediated system aborts at the bound. direct and strong are
bit-identical physically (same loop, different telemetry), so the honest
reading of `strong_osc` is "the incumbent is the OSC controller itself;
no competing incumbent supervisor exists in robosuite to compare".

## 5-8. Internal reference matrix (frozen, unchanged)

Internal HEB held-out (HEB 0.2.0 regrade, N=38): mediated BSR 0.844,
breach 3; bare BSR 0.789, breach 6; direct had 3 violations in 0.1.0-era
grading. Coverage/overhead unchanged from the frozen prior campaign.

## 7. Execution governor result (X30/X31)

**Negative. Not promoted.** G1 (sliding-window force peak, 80% activation,
axial stiffness attenuation to 25% at limit, targets preserved exactly):

- X30: BSR 11/22 -> 10/22; completion P95 2.48 -> 3.90 s; force integral
  0.271 -> 0.421 N.s; peak force unchanged (2.898 N both).
- X31 timing stress: BSR 14/24 -> 13/24; jitter peak force 5.56 -> 6.63 N;
  +1 deadline invalidation; only gain: avoided one jitter-induced
  tilt-guard fault.
- Figure: docs/figures/14_governor_stability_boundary.svg.

## 13. Contact-solver sensitivity (X41)

48/48 successful seatings across 6 numerical variants (Newton/PGS/CG,
impratio 1/3, elliptic/pyramidal cone, dt 5e-4/1e-4), canonical-config
protected by `benchmark_deviation=True` guard:

| Conclusion | Verdict |
|---|---|
| capture at 1-2 mm offsets (48/48) | SURVIVES |
| force plateau ~15 N (14.951-14.995 N) | SURVIVES |
| settling behavior / time-to-seat 1.51-3.02 s | SURVIVES |
| post-seat oscillation (<= 5.4e-4 N std) | SURVIVES |
| axial penetration scale (~24.7 um) | SOLVER-SENSITIVE to cone: pyramidal 2.5 um |

Drake hydroelastic probe (sphere-plane, 15 N load): 744 um penetration vs
MuJoCo 24.7 um cylinder-hole — formulations are not apples-to-apples; the
probe documents the magnitude of cross-formulation difference without
claiming equivalence.

## 15. Real-data observer track (X50-X52)

Official archives BLOCKED by disk (54.8 GiB REASSEMBLE, 70-545 GB FMB vs
7.8 GiB free; `df -h` evidence in STATE.md). Derivative HF subsets (49 MB
total, robot-lev/REASSEMBLE + DAVIAN/fmb-insert-1MS-4cam) used, labeled
DERIVATIVE DATA throughout.

| Dataset x Observer | precision | recall | false-contact (free space) |
|---|---:|---:|---:|
| FMB derivative / O0 direct wrench | 0.74 | 1.00 | 0.00 |
| REASSEMBLE derivative / O0 direct wrench | 0.19 | 1.00 | 0.13 |
| REASSEMBLE derivative / O2 momentum residual | 0.056 | 1.00 | 0.81 |

- O1 StateFilter: NOT APPLICABLE (requires peg/hole probe geometry).
- O2 on FMB: IMPOSSIBLE (no joint-effort channel). O2 on REASSEMBLE
  precision 0.056: model-mismatch negative, recorded.
- GT limitation: primitive phase labels + force threshold, NOT physical
  contact truth. Claims are phase-proxy correspondence only.
- Figure: docs/figures/15_observer_real_data_result.svg.

## 18. Engineering-leverage metrics

- haplous/ added: 711 LOC = 115 (ExternalTask adapter boundary) + 596
  (CLI subpackage). Guarded core semantics modules: 0 LOC changed.
- benchmarks/ + scripts/ added: 2250 LOC.
- Core task/backend leakage: none (AST guards pass: no robosuite/isaaclab/
  contactbench imports anywhere in haplous/; no peg/insert vocabulary in
  top-level core; HEB file-set unchanged).
- Figure: docs/figures/16_engineering_leverage.svg.

## 19. Runtime overhead

robosuite FINAL: R2-R1 mean wall -1.71 s (mediation terminated trials
earlier, so wall difference is not overhead). Internal HEB frozen eval:
end-to-end overhead within +-2% (prior campaign). Honest statement: no
clean per-tick overhead measurement exists in this campaign.

## 21-22. Negative results and weaknesses

- G1 governor: negative (detail above).
- O2 momentum observer on real data: precision 0.056 on REASSEMBLE
  derivative; model mismatch dominates.
- Scripted nut policy: 0/15 — task success unmeasured on external tasks
  this campaign; the robosuite result is a force-bounding result, not a
  task-success result.
- O0 on REASSEMBLE precision 0.19: direct-wrench detection does not
  transfer cleanly to that dataset's segment structure.

## Pending (adjudicated before finalization)

- X92 independent evidence review: REVIEW PENDING
- X93 red-team review: REVIEW PENDING
- X80 clean-room developer proxy: REVIEW PENDING
