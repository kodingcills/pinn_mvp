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

## Parallel campaign tracks — RESULTS (verified 2026-09-09)

- **X02 CLI**: COMPLETE (commit 9318c3f). haplous/cli/ 596 LOC, 13 tests;
  doctor/list/run/eval/compare/envelope/replay/report all verified on the
  real surface; registry+manifests with sha256 conventions verified.
- **X20/X21 robosuite**: COMPLETE (commit 676170a). Adapter+policy+runner,
  651 LOC, haplous core delta 0. Verified conventions: 20 Hz control
  period, eef quat exported xyzw, compressive contact = positive force_z,
  OSC kp=150 machine-owned (k_z not mapped — documented deviation).
  Dev 5 seeds: policy 0/5 success after 2 permitted redesigns; mediation
  distinction measured: direct/strong identical 396-500N peaks, Haplous
  aborts 26-32N. Qualification block seeds 5-9: same pattern (437-462N
  vs 24-29N). Artifacts: results/external_eval/robosuite-1.5.2/*.
- **X30/X31/X70 governor + jitter**: COMPLETE (commit 0f962c3). G1 =
  NEGATIVE result, no promotion: BSR 11/22 -> 10/22, P95 2.48 -> 3.90 s,
  force integral 0.271 -> 0.421 N.s, jitter peak 5.56 -> 6.63 N, +1
  deadline invalidation; only gain = avoided one jitter tilt-guard fault.
  Timing/jitter stress delivered: random-period jitter +-10-100%, bursty
  dropout/stale, command delay 1/4/16/40 steps. haplous+HEB delta 0.
- **X41 solver sensitivity**: COMPLETE (commit 88e2bd9). 48/48 seat across
  6 numerical variants. N01: capture/force-plateau/settling/oscillation
  SURVIVE; penetration scale SOLVER-SENSITIVE to cone (elliptic 24.7um vs
  pyramidal 2.5um). ContactBench REJECTED with dry-run evidence; Drake
  1.46.0 PROBED_NOT_ADOPTED (hydroelastic sphere-plane: 744um penetration
  at 15N — formulations not apples-to-apples, documented).
- **X50/X51/X52 realdata**: COMPLETE (commit 216dbc8). Derivative HF
  subsets only (49 MB; official archives disk-BLOCKED 7.8GiB). O0 direct
  wrench: FMB precision 0.74 / recall 1.0, REASSEMBLE precision 0.19;
  O2 momentum residual on REASSEMBLE: precision 0.056 (model-mismatch
  negative); O1 N/A (no probe geometry); O3 not run. GT limitation:
  primitive-phase labels + force threshold, not physical contact truth.
- 4 subagent "failures" were reporting-stage failures only (X41 timeout,
  X52 usage-limit); all measurements verified present and complete.

## Freeze (X90)
- Tag haplous-external-eval-v0 = this commit (after qual run 20260909T213621Z).
- Runner got pre-qualification harness fix: --seed-start + --evidence-stage.
- Final eval seeds: 10..24 literal per PREREGISTER.md; qual seeds 5..9 done;
  dev seeds 0..4. Governor NOT promoted (negative) -> no HAPL+GOV column.

## Campaign CLOSE (2026-09-09)
- All X-nodes complete or evidence-backed-dispositioned.
- Subagent infrastructure failed systematically in the review phase:
  8 recorded failures (4x 30-min inactivity stalls with zero output,
  2x model-config errors, 1 start failure). X92/X93 executed as
  orchestrator-run mechanical audits, independence NOT achieved,
  disclosed in docs/X92_EVIDENCE_REVIEW.md and docs/X93_REDTEAM_REVIEW.md.
  Independent re-review REQUIRED before external use.
- Final report: docs/HAPLOUS_EXTERNAL_BENCHMARK_REPORT.md (FINAL with
  disclosed limitation). Figures: docs/figures/ (5 SVGs, all data-backed).
- Suite at close: 306 passed, 1 xfailed.
- X71 compound (multi-axis simultaneous) perturbation campaign: NOT
  PERFORMED this campaign. Boundaries measured are single-axis families
  (offset/clearance/facets from the prior S06 sweeps; this campaign adds
  jitter period, bursty dropout/stale, command delay, kick magnitude,
  force-limit cells). Compound cells remain future work, consistent with
  the prior campaign's disclosure.
- X52 registry resolution (post-close audit): results/external_eval/realdata/
  20260909T204553Z verified byte-identical to the committed canonical
  x52_observer_summary.json (canonical run); 20260909T204030Z is the
  earlier dev pass with superseded observer parameters (prec 0.993/0.0 —
  dev numbers, not evidence). Both committed; no inconsistency found.
