# Haplous External Benchmark Campaign State

Started 2026-09-09, HEAD `9d196ea`. Freeze tag
`haplous-prehardware-eval-v0` (5ae48fb) verified in ancestry — untouched.

## X00 — prior review reconciliation: DONE at campaign start
Verified against repo truth (results/heb_final_adjudication.json,
docs/HAPLOUS_PREHARDWARE_EVAL_REPORT.md sec. 23b, prehardware/STATE.md):
- S16 independent evidence review: SUPPORTED WITH CAVEATS. Fixes folded
  into HEB 0.2.0 (dropout grading loophole / gap grading, metric rename,
  slide_07 mechanism claim withdrawn per trace, deadline reclassification,
  provenance fields).
- S17 red team: 2 violations addressed by the 0.2.0 revision; 4 concerns
  documented (B1 = ablation not competing supervisor; modeled rectangular
  fault windows with post-sweep thresholds inside the declared envelope;
  single-operator protocol; adjudication transparency).
- **No methodology-invalidating defect was identified.** The prior held-out
  evaluation stands: VALID (regraded at HEB 0.2.0).
- Canonical 0.2.0 regrade numbers (supercede pre-regrade figures):
  mediated coverage 0.842, BSR 0.844, breach_trials 3; bare BSR 0.789,
  breach_trials 6 (not 5); refusal P/R 1.0/1.0 mediated.

## Node graph (X-series)
X00 reconcile reviews — DONE (this file, above)
X01 external benchmark adapter architecture
X02 unified CLI + result registry
X10 Isaac Lab feasibility [darwin: Apple M4 Pro, no NVIDIA GPU — expected BLOCKED]
X11-X14 Isaac Factory/FORGE/AutoMate [blocked downstream of X10 unless path found]
X20 robosuite adapter
X21 robosuite incumbent controller baselines
X30 governor candidate benchmark
X31 passivity/energy robustness microbench
X40 ContactBench integration
X41 contact-solver sensitivity campaign
X50 REASSEMBLE offline telemetry track
X51 FMB offline telemetry track
X52 observer comparison
X60 cross-framework normalization
X61 external dev campaign
X62 qualification campaign
X70 robustness boundary search (timing/jitter extension)
X71 compound perturbation campaign
X80 clean-room developer proxy
X81 package/dependency hardening
X90 freeze external-eval candidate
X91 held-out/external final evaluation
X92 independent evidence review
X93 red-team review
X94 final report + graphs + release

## Baseline (running/verified at start)
- pytest tests -q -x at 9d196ea: **278 passed, 1 xfailed in 110.86s** (matches
  pre-campaign closed state). HEAD at campaign start: 9d196ea.
- X01 committed aa46c24: haplous/adapters/external.py (ExternalTask protocol +
  ExternalMachineAdapter + open_task_session) + tests/test_external_adapter.py
  (7 tests: protocol conformance, Machine surface, runtime smoke over fake
  external task, no-external-framework-imports AST guard over all of haplous/).

## Platform facts (verified at campaign start)
- Apple M4 Pro, 14 cores, 24 GB RAM, NO NVIDIA GPU, Docker present.
- Disk: 7.8 GiB free -> official REASSEMBLE (54.8 GiB) and FMB archives
  (70-545 GB) cannot fit; real-data track pivots to labeled HF derivative
  subsets (robot-lev/reassemble, DAVIAN/fmb-insert-1MS-4cam), documented.
- Main venv .venv: python, mujoco, pytest (278-test baseline).
- robosuite venv (tmp): robosuite 1.5.2 + mujoco 3.9.0 (pin REQUIRED:
  robosuite declares mujoco<3.10; uv's default resolution pulled mujoco
  3.13.0 which breaks robosuite env construction with
  AssertionError in binding_utils.get_joint_qpos_addr).

## robosuite API facts discovered (X20 pre-work, verified by smoke)
- Composite controller configs are required: pass
  suite.load_composite_controller_config(robot="Panda") -> {"type":"BASIC",
  "body_parts":{"right":{OSC_POSE kp=150..., "gripper":{"type":"GRIP"}}}}.
- Headless: has_renderer=False, has_offscreen_renderer=False,
  use_camera_obs=False, seed=N. macOS CPU verified: 200 control steps ~3 s,
  56 contacts, ee_force sensor present, env.is_success() for grading.
- Contact-rich envs: NutAssemblySquare/NutAssemblyRound/TwoArmPegInHole.

## X10 Isaac Lab: REJECTED (results/x10_isaac_disposition.json)
No darwin wheels (PyPI evidence), uv resolution failure captured, no NVIDIA
GPU, Docker-on-macOS cannot pass CUDA. X11-X14 blocked downstream; robosuite
+ solver-variation/Drake covers the independent-backend axis on this platform.

## Parallel campaign tracks (launched 2026-09-09)
- bg_e1e5a0f7 X02 CLI + registry (haplous/cli/, pyproject scripts entry)
- bg_6e66f986 X20/X21 robosuite adapter + paired comparison
- bg_3c68a825 X30/X31/X70 governor G1 + jitter microbench
- bg_54beee52 X41 solver sensitivity + ContactBench/Drake dispositions
- bg_0e28f8e1 X50/X51/X52 real-data observer track

## Visualization decision (X10-viz)
Rerun NOT adopted as a campaign deliverable: structured evidence traces +
JSONL rows are already produced per run; native MuJoCo/robosuite viewers
remain the visual path. Rerun stays optional-future; no campaign stall.

## Findings appended per node below as work completes.
