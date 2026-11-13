# Haplous Eval Foundation — Agentic Engineering Program v2
**Decision draft · 2026-11-12 · CPU-first · engineering implementation mandate, not a claim of validation**

## 0. Outcome / negative space

**Outcome**: Extend the EXISTING `haplous/`, `pegsim/`, `benchmarks/heb/`, `evaluation/`, `hes/`, and M0/Track B code into one reusable, independently scored evaluation system. It must (a) run physical scenarios through direct, competent incumbent, and actual Haplous paths; (b) persist original trajectories and execution decisions; (c) reopen and scrub 3D simulation history after the original job exits; (d) compare matched episodes; (e) generate statistically defensible evidence and failure attribution; (f) demonstrate different tasks and machine/capability profiles; and (g) support later frozen-policy, GPU, hardware and OEM integrations without claiming those were executed.

**Anti-goals**: another registry, a second runtime, third scoring library, universal servo API, new physics solver, full JAX/MJX port, custom browser 3D engine, safety certification, false equivalence with OEM force mode, seeded Tier 3 runs, paid GPUs, commercial/physical ROI claims. No work on a generic insertion-only benchmark as the backbone.

**Evidence status**: Current `main` was inspected through GitHub on 2026-11-12. PR #6 was merged. Vendored `.claude/skills/poteto-mode`, `.claude/skills/create-verification-skill`, `.claude/skills/maintain-verification-skill` are present. Existing HES is operational on committed historical evidence, but its historical reports contain attribution revisions; latest fixes and frozen populations must be reconciled from code before new claims. Historic test counts in `AGENTS.md` are stale. Runtime capabilities are simulated/software semantics, not real OEM support.

## 1. Existing code is the architecture baseline

| Existing owner | Keep as canonical | Extension required / audit question |
|---|---|---|
| `haplous/` | execution intent, requirements, capability negotiation, `Machine`/`MachineSession`, runtime, evidence | compare `evaluation/track_b.py` semantics; do not maintain two conflicting decision authorities; verify stop/hold/recovery on physical path |
| `pegsim/` | MJCF, control, real MuJoCo episodes, estimators, known physics work | extract real state frames without changing physics or loop scheduling; obey known fidelity gates |
| `benchmarks/heb/` | scenario construction, incumbent and direct arms, grader, counterfactuals | preserve grading independent of Haplous; use two existing families: insertion and constrained slide; add richer traces/recordings through minimally invasive hooks |
| `evaluation/experiment.py` | manifest, deterministic shards, resume, canonical episode rows, Parquet | extend only with metadata and artifact references; preserve existing row compatibility and frozen epochs |
| `hes/` | outcome axes, attainment, attribution, gates, statistics, report and claim ledger | score fresh physical runs, validate historical report corrections, add replay and trace references, never duplicate logic |
| `evaluation/run_registry.py` (M0) | integrity/provenance, hash-chain, manifest checks | link per-run evidence and existing `evaluation/experiment.py` IDs via canonical cross-reference; do not create competing run identity systems |
| `evaluation/track_b.py` | standalone decision-semantics prototype and G0 test baseline | differential semantic tests against `haplous/` where meanings overlap; decide explicit ownership before any integration |
| `.claude/skills/` | pstack orchestration/verification skills | adapt invalid Cursor-only commands, model slugs and optional dependencies to real Claude Code; don't install redundant copies |

**Known hazard**: `MachineObservation` currently exposes a fixed xyz pose, z-force and tilt, and `ServoCommand` contains target xyz and axial stiffness. These are a working seam for current research, NOT evidence of a fully task-/OEM-general control abstraction. Map which commands/observations are truly shared; use capability-typed extension only when a second task proves a need. Do not equate different command modes by string substitution.

## 2. Technical decisions (decide now / change only on measured failure)

1. **Physics source of truth**: CPU `mujoco` (C engine via Python bindings), pinned per scene/campaign; leave separate LIBERO-Dynamic environment untouched. JAX may be used for an estimator/policy module if it already exists. MJX-JAX is optional later for large batched jobs, not the canonical contact/physics baseline; its collision and single-scene performance differ.
2. **Language partition**: Python as evaluation/control orchestration prototype, NumPy arrays for physics/state, Arrow/Parquet for batched evidence, JSONL for semantic events, Rerun `.rrd` as derived interactive view. C++/Rust only for later production timing-critical edge components following measured deadline and OEM-interface requirements, not a speculative rewrite now.
3. **Time semantics**: every record has simulation time (integer tick + physics dt), independent host monotonic timestamp for measured runtime, producer observation stamp, receive stamp, policy decision time, dispatch and acknowledgment if observable. Never mix simulated elapsed time and wall-clock performance. No invented actuator time from dispatch only.
4. **Sampling and memory**: collect high-rate telemetry into fixed-size preallocated buffers, flush chunk-wise with bounded memory and backpressure; event log all discrete transitions without dropping them. Visualization frames are decimated but original recorded numerical samples needed by metrics and fault inspection are retained. Compression and a size budget are part of verification; don't keep millions of Python dicts in RAM.
5. **Authority**: simulator/benchmark owns privileged truth and grading. Runtime and controller receive only declared normal observations. Test absence of leaked seed, ground truth, fault identity, and evaluator-only fields at the execution boundary. Machine adapter explicitly distinguishes simulation stepping and production actuator control.
6. **Model/control rates**: decouple policy inference, execution supervision, native controller, physics step and renderer clocks. Sim advances in fixed steps; lower-rate updates occur on explicit schedule. Never claim a 1 kHz control rate merely from 1 kHz Python iteration. Measure deadline/jitter with host clock and actual hardware later.
7. **Replay**: store model/assets and a versioned numeric state stream (`mjtState` signature + dimensions + metadata, including appropriate `qpos`, `qvel`, `act`, `ctrl`, mocap, plugin/userdata when relevant), plus independent actual sensor/contact samples and event logs. Use `mj_setState`/`mj_forward` ONLY to render captured state; never re-simulate and claim it is historical evidence. Derived geometry from replay may differ if versions/assets change; validate version/asset digests. Original measured contact/force samples remain the source of truth.
8. **Visualization**: prefer Rerun's time-indexed Transform3D/mesh/plot/event `.rrd` plus headless MP4 export. The canonical state stream is authoritative, RRD and MP4 are derived. Viewer can be tested by CLI inspection/frames in headless cloud; macOS GUI usability remains UNKNOWN until tested there.
9. **Baselines**: G0 is an intentionally weak ablation only. Real result comparisons use a competent tuned incumbent with force/mode/time/fault guards, and current HEB `competent_supervisor` when relevant; task-specific accepted alternative if it is stronger. Keep equal information, task and physical authority, with documented limits; if matching capabilities is impossible, disclose asymmetry.
10. **Claims**: no single Haplous score. Preserve independent task success, bounded success, coverage, false-refusal, violation conditional on acceptance, noninterference, latency, recovery, severity, and cost/reuse metrics with proper denominators. A qualification fail stays a fail; no hiding regressions in averages.
11. **Hardware path**: machine profiles record supported command interface, controller ownership, rate, latency/jitter, limits, sensing and freshness, stop behavior, mode transition, calibration uncertainty. Unsupported modes fail closed; ROS 2 Control or OEM endpoints are integrations to validate, not assumed torque access. Machine-level stop does not certify physical safety; keep independent safety systems and human hardware gate.
12. **Frozen prior evidence**: do not modify committed locked designs or historical data. Later evaluator repairs create new scoring epochs, lineage records and regression tests. Any contradictory historical statement must be corrected in an addendum instead of silent rewrite.

## 3. Architectural data flow

```text
Upstream task / policy (scripted CPU now; model adapters later)
    │  PolicyOutput / intended behavior + observation/action chunk provenance
    ▼
Existing Haplous intent + feasibility, or incumbent execution stack
    │  admitted/refused/recovered/held + reason + timing/authority
    ▼
Typed Machine/Execution Adapter (explicit capabilities, no fake equivalence)
    │  low-level commands / acknowledgments
    ▼
MuJoCo backend (physics time, real contact, disturbances)
    │  runtime-visible observations                │ evaluator-only ground truth
    └─────────────► closed-loop policy           └──► independent HEB grading
                     │                                    │
                   event trace + bounded state recorder + M0 provenance
                                                          │
                       evaluation/experiment canonical episodes + HES analysis
                                                          │
                   original trajectory | RRD | MP4 | comparisons | report
```

**Minimal typed boundaries to inspect and map (NOT a mandate to create 9 abstract classes)**:
`ScenarioIdentity`, `TaskSpec`, `MachineCapabilities`, `Observation`, `PolicyProposal`, `ExecutionDecision`, `DispatchedCommand`, `StateSample`, `ScoredOutcome`. Reuse existing types where possible. Every translation retains an ID, units, frames, timestamp domain and provenance. No implicit torque/velocity/position substitutions.

## 4. Milestone 2 — Vertical slice: capture → playback → verification

### Build
- Make one REAL existing HEB/pegsim physical episode run with bounded numerical recording, M0 and experiment identity, and original Haplous event IDs.
- Generate one archived run containing the scene model/asset manifest, compressed state stream, timestamped telemetry + control + contract/fault events, derived `.rrd` timeline, and MP4 preview.
- Implement `replay/inspect` through existing CLI conventions; prefer extending `hes` or `haplous` CLI over competing interfaces.
- Build `.claude/skills/verify-haplous-eval/` using `/create-verification-skill`, with launch / doctor / drive / evidence / cleanup and feature map; actually run it.

### Gate (not docs-only)
- Run physical episode, let process exit, reopen FROM ARTIFACTS without the policy/controller, scrub back/forward, validate 3D pose and timestamp against original numeric samples; show at least one synchronized fault/decision; MP4 has changing frames and correct duration; RRD validates (`rerun rrd verify` if installed); every artifact hash matches M0. Repeat on a clean temporary directory. Memory does not grow with rollout length beyond configured buffers. No drawing fake states. HEADLESS CPU PASS.

## 5. Milestone 3 — Generalization without parallel evaluators

### Build
- Reuse HEB's **peg insertion** and **constrained slide** as two truly different task packs; first determine whether their physical execution and grading are valid. Avoid inventing new geometry.
- Use existing direct, competent, mediated systems. Add a pass-through path that must preserve outcome under controlled determinism/A-A. Support a scripted CPU policy adapter with model-like timing/action outputs (clearly labeled scripted). Physical supervisor effects and task success are independently scored.
- Make pack-2 integration possible without editing HES metric core, M0 registry, or recorder internals; task-specific graders are allowed.
- Add explicit role-dependent observations, capability-mismatch tests and simple, repeatable disturbances. Command/clock semantics must be consistent across packs.

### Gate
- Two physical tasks + >=2 distinct execution strategies run through same episode and evidence path; both produce replays, statistics and reports. New task-specific metrics are registered via documented extension, not hidden if/else on task name. Manifest and grader reject intentionally mismatched task IDs, units, timestamps and truth leakage. Independent verification proves core doesn't change to add pack 2. If generality is impossible because the existing `ServoCommand` is too task-specific, make smallest typed extension backed by the second-task need and compare API alternatives before committing.

## 6. Milestone 4 — Actual incremental-value experiment

### Build
- Define G1 as a competent incumbent for each task (current HEB supervisor may be a starting candidate, not automatic market SOTA); test direct vs G1 vs Haplous under common controller/physics/observation resources where possible.
- Execute pre-registered CPU **development** matched scenarios with nominal + faulted conditions. Faults include stale state, dropped command/observation, revoked authority, mode mismatch, physical obstruction/disturbance when physically valid. Ensure Haplous interventions are real acts on the simulated MachineSession, not labels.
- Retain separate estimands: policy-proposal errors; contract correctness; actual physical execution; end-to-end accepted outcomes; non-interference; safety/violation exposure; intervention overhead; engineering adaptation burden.
- Report paired wins AND regressions; compare historical HES reach-rejection and intervention-label corrections. Confirm which scoring epoch applies. Never use old erroneous HARMFUL numbers as current truth.
- Add minimal single-component ablation(s) only for mechanisms actually implemented; no fabricated estimator/VLA arm.

### Gate
- Matched comparison can be reproduced from frozen dev seeds. HES qualification and explicit trade-off vector produced. Non-interference and false refusals independently adjudicated, including attainability UNKNOWN. Full-time numerator includes failed episodes, waits and resets; not only successful trajectories. Planted evidence corruption, wrong-pair matching, simulator privileged truth leak, and bad denominator fail tests. A result can honestly report zero or negative Haplous advantage; a competent G1 is not weakened to produce positive deltas. Statistical CI shown only at supported cluster/resampling level.

## 7. Milestone 5 — Autonomous extension loop, proof and handoff

### Build
- Make pstack's Orchestrate + verification playbooks work inside actual Claude Code environment; use `.claude/skills/poteto-mode/playbooks/orchestrate.md` as process guidance but adapt Cursor-only Task APIs, `cursor-team-kit` commands, non-existent model slugs, `/loop`, and `~/.cursor` paths. No infinite retries on unavailable tools.
- Agent orchestrator creates an evidence-driven task DAG, not a standalone mega-implementation. One owner per branch/PR, disjoint file ownership. After one proof-carrying vertical slice, parallelize independent recorder/UI/benchmark/integration tracks. Separate reviewer must reproduce artifact on latest head SHA. Subagents have bounded outputs and context.
- Maintain `.claude/skills/verify-haplous-eval/` feature map and live journeys; run `/maintain-verification-skill` after changing user-visible commands. Use formal verification **selectively** on irreversible authority/lease/state transitions and M0 nonce/concurrency; compare model to real code and mutation-break deliberately. Don't formalize all physics.
- Agent can plan and run Tier 1/2 CPU development within scope, but never unseal/rerun Tier 3, buy GPU, deploy to hardware, claim external validation, merge to main, or modify frozen evidence without explicit human approval.
- Durable state: `docs/eval_program/PROGRAM_STATE.md` with milestone statuses; `TASKS.json` with owner, dependencies, target commit, expected evidence, artifact links; `DECISIONS.md`, `KNOWN_ISSUES.md`, `REPRODUCE.md`. Keep summaries concise; actual evidence stays in files.

### Gate
- Fresh-agent cold start can read state, choose next unblocked task, implement/verify one new task-pack case or execution mechanism, open reviewable PR and produce independent verification receipt; reproduce already recorded run locally or headlessly. All completed milestones have runnable proof; all STOPPED/UNKNOWNs have concrete reasons. No automatic Tier 3, no paid GPU. Final demo runs from a documented clean checkout with one-command smoke and archive replay.

## 8. Verification contract (applies to all milestones)

For each task define `{goal, repo paths, owner, dependencies, oracle/test commands, expected observable behavior, proof paths, no-go paths, resource budget}`.

**Prove all changes at the surface they change.** For CLI user journey, run CLI and inspect generated files; for replay, load archival files after original process exits; for physical behavior, run physics and inspect independent state/outcome; for statistics, recompute from raw rows; for safety semantics, test states and transitions including hostile interleavings. Unit-test-only proof is insufficient for end-to-end features.

**Mandatory adversarial probes**: (1) stale or corrupt asset; (2) clipped trajectory; (3) missing sample; (4) invalid frame/units; (5) wrong task scenario pairing; (6) absent privilege mask; (7) false admission/false refusal; (8) aged observation under a moving target (when that pack exists); (9) optimistic score based on accepted-only denominator; (10) runtime claiming stop before physical motion ends. At least one deliberately introduced defect must be caught by each new verifier.

**Scientific semantics**: accepted outcomes graded by benchmark-owned truth, hard violations never hidden by nominal task completion, correct refusal vs successful execution distinct, uncertainty and confidence intervals reported with sample and clustering assumptions, attribution is not causation absent justified intervention or controlled comparison.

**Hard output check**: `EVIDENCE_BUNDLE` includes canonical episode rows, original state samples, events, manifest, checksums, scorer output, stats, RRD, MP4, human report, replay commands. LLM writes narrative only; scored data and calculations come from deterministic code.

## 9. Task and resource allocation

Use a **3-stage ramp**, not a free-for-all: first one trusted worker + independent verifier for Milestone 2 thin vertical slice; then up to three parallel implementation owners on disjoint files (recorder/viewer, task-adapter, analysis), plus an independent reviewer; then milestone-5 automation only after passing evidence gates. No more simultaneous agents than actual parallel tasks. Track token/compute and useful verified PRs per resource; parallelize based on demonstrated throughput, not number of agents.

**Time / memory**: bound RAM, disk and CPU per agent and simulation; prefer CPU and local renderers; if resources absent, complete independent work, don't request hardware. Large binary `.rrd`, `.npz`, `.mp4`, datasets should be stored in artifact storage outside ordinary Git history; `SHA-256` and stable relative references live in M0. Avoid storing full videos in main source control.

## 10. Decision triggers and non-goals beyond this program

- If recorder perturbs nominal success beyond A/A variability -> fix instrumentation before any Haplous A/B result.
- If replay cannot match captured physical state -> Milestone 2 not done. Do NOT use generated re-simulation as historical replay.
- If adding constrained-slide requires rewriting HES/recording core -> Milestone 3 not done, fix the boundary rather than handcode conditionals.
- If competent incumbent wins -> report it; do not retune Haplous on frozen qualifications to reverse result.
- If physical failure is due to invalid contact geometry -> reject that task pack's scientific result, not the whole evaluator.
- If source/solver or HES scoring epoch differs -> results must be partitioned; no unqualified pooling.
- If meaningful GPU-required VLA validation is blocked -> keep external VLA leg `ACCELERATOR_DEPENDENT`; CPU mock policy does not become model performance evidence.
- If new OEM/hardware becomes available -> separate approved integration milestone with OEM docs, actual supported command modes, safety case, real sensor timing and protected physical experiments. None are implied by two simulated tasks.

## 11. Recommended external technical references (primary)

- MuJoCo state and simulation: https://mujoco.readthedocs.io/en/latest/programming/simulation.html and https://mujoco.readthedocs.io/en/latest/APIreference/APIfunctions.html
- MuJoCo MJX-JAX/Warp tradeoffs: https://mujoco.readthedocs.io/en/latest/mjx.html
- Rerun timeline and `.rrd`: https://rerun.io/docs/getting-started/data-in ; https://rerun.io/docs/howto/logging-and-ingestion/manipulate-rrd-files
- ROS 2 Control controller and fallback semantics: https://control.ros.org/rolling/doc/ros2_control/controller_manager/doc/userdoc.html
- Claude Code division of CLAUDE.md/skills/subagents/hooks: https://claude.com/blog/steering-claude-code-skills-hooks-rules-subagents-and-more
- Claude guidance on long-running agents and restraining useless delegation: https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/prompt-templates-and-variables

## 12. Explicit scope of 'done'

**Done is a verified, CPU-based generalized evaluation foundation**: two genuine physics task packs, historical 3D replay and event/timing inspection, baseline vs Haplous comparisons under actual implemented mechanisms, independent scoring and analysis, persisted reproducibility, and a working Haplous-specific verification skill that a fresh coding agent can follow. **Not done**: real OEM/hardware portability, factory throughput, safety certification, production-grade realtime claims, or 3B+ VLA inference. Those remain separate evidence milestones.
