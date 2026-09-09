# Haplous Pre-Hardware Campaign State

HEAD: see HEAD.txt (post-S01 commit). Freeze tag `research-rseries-complete`
verified in ancestry at campaign start; untouched.

## Baseline (verified 2026-09-09)
- tests: 244 passed, 1 xfailed, 113.6s (pre-S01)
- tests after S01: 259 passed, 1 xfailed, ~105s

## Nodes
- S01 live interruptible execution: DONE (72802b2)
  - Machine protocol: open_session() stepping sessions; MotionRecord + replay
    monitoring path deleted
  - InsertionExecutionBehavior: run_episode task logic ported verbatim;
    equivalence test: live poses bit-identical to run_episode, force reads
    within 2N (solver warmstart observer-path bound, documented)
  - Mid-motion fault tests: stale delivery, dropout (small/large), command
    rejection (transient/persistent), machine fault, tilt guard — modeled
    logical timing, detection latency 0-1 tick, stop latency 0 ticks
  - v0-sim re-run: 13/13 conformance, refusal recall/precision 5/5
  - FINDING: success condition fired during zero-force coast-to-floor
    (false seat). Fixed: seated requires stationary tip (10um z-span over
    persistence window). v0-sim scenario expectations updated to live
    semantics; 2N-force intent now genuinely aborts mid-motion, 5/12N
    intents complete in-bounds before substrate force ramps past them.
- S02 HEB core: DONE (HEB 0.1.0, commit after S01)
  - protocol: Scenario/Realization/SystemUnderTest/TrialRecord;
    benchmark-owned grading; summarize separates coverage, BSR among
    accepted, refusal precision/recall (risk-vs-coverage shape)
  - systems: B2 haplous_mediated, B1 bare_behavior, B0 direct_minimal
  - independence guards: tests/test_heb_structure.py (no private haplous
    imports; grading modules may only use haplous.machine data types)
- S03 insertion task family: DONE — 18 dev scenarios (nominal/offset,
  infeasible, force-limit negotiate-vs-violate, dropout, stale, command
  rejection transient/persistent, machine fault, 3 geometry variants)
  - dev evaluation (results/heb_dev_rows.jsonl, heb_dev_summary.json):
    haplous conformance 18/18, coverage 0.78, refusal P/R 1.0/1.0, zero
    hard violations; bare BSR 0.72 with 2 hard-violation trials and
    refusal recall 0.0; direct 0.61/3 violations
  - baseline honesty note: on force-limit scenarios Haplous aborts at the
    limit while bare pushes to 15N (24s wall vs 0.24s) — runtime value is
    real but BSR-among-accepted is LOWER than bare (0.64 vs 0.72) because
    Haplous additionally aborts on modeled timing faults the bare system
    ignores. Report as-is; do not tune against it.
- S04 Task B constrained contact: DONE (commit after d2a4f17)
  - VALIDATED_SLIDE_V1: 1mm/2g part, gains scaled to insertion omega*dt
    margins (first attempt diverged in tilt — R4 signature reproduced)
  - behaviors/sliding.py 174 LOC; force-regulated slide, measured-angle
    snap (angle-wrap false-snap found+fixed), pop re-acquisition
  - core delta: +177/-59 (runtime.py +132/-58 recovery-episode lifecycle;
    slide-specific core delta = generic MIN_PROCESS_FORCE_N, +45)
  - runtime recovery now episode-based: RECOVERING held, closure on clean
    constraint, recovery_timeout, budget only on NEW episodes; completion
    gated on fresh state (stale-observation false-completion fixed)
  - adapter: benchmark-owned disturbance schedule (exactly-once, tested)
  - HEB: tasks.py registry (generic dispatch, no per-task branches),
    Scenario.disturbances/recoverable/recovery_settle_s, grade_slide_task,
    sustained force-floor grading (50ms grace), RSR metric
  - findings: facet taps 1.73-1.74N (3 per 60-degree arc), establishment
    tap 1.74N, kick spike 0.47N, tilt-guard kicks unrecoverable; recovery
    permission flips disposition on identical physics
  - dev eval 27 scenarios B0/B1/B2: mediated 27/27 conformance, coverage
    0.81, refusal P/R 1.0, recovery 1.0, 1 honest hard-violation trial;
    bare 16/27, 6 violations; direct 10/27
- S06 single-axis sweeps: DONE (results/heb_sweeps_dev.json)
  - offset: capture boundary 3.0->3.5mm (both systems)
  - clearance: mechanism envelope [0.1, 0.5]mm — looser fails BOTH
    (commit-gate constants valid only at canonical geometry: stale-
    constant class, mechanism-level not runtime)
  - facets: n=8 fails both; n>=16 converges (matches N4)
  - stale delivery: mediated enforces 20ms strictly; bare ignores
    (bounded=False when graded) — risk/coverage visible
  - dropout: mediated aborts >=20ms blackout; bare barrels through and
    the delivered-stream ages grade clean — semantic difference measured
  - slide kick: tilt-guard boundary 0.3->0.4 m/s
  - benchmark-validity fix: clearance cells derive tolerance from
    realized geometry
- S12 reliability/property tests: NEXT
- S07..S11, S13..S18: pending

## Key interfaces (current)
- haplous.machine: Machine{capabilities, observe, open_session, stop, health};
  MachineSession{observe, apply(ServoCommand)->CommandOutcome, step, stop,
  ended, fault}; ServoCommand{target_pose_m, k_z_n_per_m}
- haplous.behavior: ExecutionBehavior{begin(intent, caps, obs), step(obs)}
- HaplousRuntime(machine, behaviors={name: factory})
- MuJoCoMachineConfig fault schedules: dropout_windows_s,
  stale_windows_s=(start,end,extra_delay), command_rejection_windows_s,
  fault_at_s; observation_decimation default 1 (2000 Hz live control)
- Adapter observation extras: mouth_x, mouth_y, in_contact

## Lessons / traps
- mj_step leaves site_xpos at the PRE-integration state: run_episode
  telemetry and live observations index-align at k (no off-by-one), but
  force readings differ by solver warmstart path (<=1.6N at impact)
- observation_decimation must equal control_decimation for validated
  dynamics; decim 10 = 200Hz sampled loop = R04 instability (reproduced:
  tilt blowup 107 deg at 0.51s)
- Healthy episode seats ~1.7s (early exit) — fault windows must target
  the descent/search phase (~0.3-1.6s), not 2.0s+

## Artifacts
- results/haplous_v0_sim_rows.jsonl, _summary.json, evidence/ (13 scenarios)

## Next highest-information action
S06 single-axis perturbation sweeps via HEB: for each major axis
(clearance, initial offset, facet count, stale delay, dropout width, kick
magnitude), generate scenario grids, run B1/B2 paired, and locate failure
boundaries (transition regions). Use coarse envelope + densify near the
boundary. These feed S07 (curves) and S08 (dev evaluation). The systems,
graders, and runner need no changes — sweeps are scenario generators.
