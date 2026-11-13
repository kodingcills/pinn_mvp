# Claude launch: Haplous Eval foundation — autonomous program execution

You are the **accountable engineering program lead**, not a one-shot code generator. Your mission is to complete the CPU-based Haplous generalized physical-execution evaluation foundation by using the vendored pstack verification/orchestration methods, specialist coding agents where warranted, and falsifiable evidence gates.

**EXECUTE THIS PROGRAM.** Inspect → design only the missing boundary → implement → operate the real application → verify → deliver reviewable PR(s) → continue along the dependency graph. Do not stop after another plan. Don't inflate scope into building every possible robotics feature. Do not assume permission to run background jobs beyond your actual host/session lifetime: commit progress and resume state so another session can continue.

## 0. Source of truth and starting point

Repo: `kodingcills/pinn_mvp`. Start from latest **`main`**, not the now-deleted `claude/magical-curie-si1vjr` branch: PR #6 was merged. Create your own work branch and verify the actual HEAD/working tree. Read `AGENTS.md`, `CONTEXT.md`, `docs/README.md`, `docs/HES.md`, `docs/HES_V0_FINAL_REPORT.md`, `docs/HES_CAUSAL_DIAGNOSIS.md`, `docs/HES_BOUNDARY_CALIBRATION.md`, `docs/HES_REACH_CAPABILITY_ENVELOPE.md`, `docs/HAPLOUS_PRODUCT_SCALE_EVIDENCE.md`, `docs/eval_v1/ENGINEERING_STATUS.md`, and the canonical v1.1 spec. Then READ THE CODE and current results; documents are evidence of historical claims, not authority over code.

Existing canonical owners: `haplous/` (execution semantics), `pegsim/` (MuJoCo physics/control), `benchmarks/heb/` (scenario/faults/ground truth/scoring), `evaluation/experiment.py` (sharded experiment lifecycle and canonical rows), `hes/` (evaluation/attribution/report/gates), `evaluation/run_registry.py` (M0 provenance), `evaluation/track_b.py` (Track B semantics and intentionally weak G0). DO NOT recreate any of these modules. Resolve semantic overlap between Track B and Haplous runtime by differential tests and a documented ownership decision, not by silently inventing a new runtime.

The repo already contains peg-insertion AND constrained-slide task families. Use both before inventing a second task. Known HES historical qualification and reporting corrections must be reconciled by scoring epoch; do not treat the initial HES v0 `HARMFUL` count or early reach-cap calibration as the current last word. Core `MachineObservation` and `ServoCommand` are tailored to current research; do not assume they are a universal OEM robot API. The historical `AGENTS.md` test counts are stale. Establish a fresh baseline yourself.

If `HAPLOUS_EVAL_PROGRAM_v2.md` is present, read it for detailed milestone acceptance criteria. If it is absent, the executable mission and the milestones below are sufficient; **do not stop solely because an optional attached brief is unavailable**.

## 1. Activate and repair the ENGINEERING WORKFLOW, not the product

The repo vendors pstack in `.claude/skills/` and `.claude/agents/`. Read:
- `.claude/skills/poteto-mode/SKILL.md`
- `.claude/skills/poteto-mode/playbooks/orchestrate.md`
- `.claude/skills/create-verification-skill/SKILL.md`
- `.claude/skills/maintain-verification-skill/SKILL.md`
- `create-formal-verification-skill` only if it earns its cost for a concrete state/concurrency property.

**This is a Claude Code port, NOT Cursor.** Audit unsupported references BEFORE spawning workers: `Task` parameter names, Cursor-specific `/loop`, `cursor-team-kit` control/deslop skills, hardcoded unavailable Grok/Opus model slugs, `~/.cursor/projects`, `.cursor/rules`, Bun/Node dependencies. Use supported Claude Code subagents/team tools, native models and paths; replace missing workflows with small real scripts or a documented fallback. No loops trying invalid model names. Don't install a redundant pstack plugin; it is already vendored. Respect `disable-model-invocation` behavior when invoking skills. If there is no hosted continuous process, preserve the ledger and stop when your allocated session ends rather than claiming ghost background execution.

**Pilot trust before scaling**: first one implementing owner and one independent verifier must deliver a real replay vertical slice with passing verification. THEN expand to a maximum of three nonoverlapping implementation owners and one verifier if the dependency graph supports it. One owner/PR; don't have writers edit the same core file concurrently. A verifier must read the current diff AND run the real artifact at the current head SHA. No automatic merge to `main` without human authorization.

Immediately create/maintain `docs/eval_program/PROGRAM_STATE.md`, a compact `TASKS.json`, `DECISIONS.md`, `KNOWN_ISSUES.md`, `REPRODUCE.md`. Ledger rows require: owner, dependencies, exact exit check, commit SHA, proof paths, retry/blocked state, next task. Keep raw traces/logs out of agent context; give workers file paths and concise acceptance contracts. Archive verifiable evidence rather than summaries.

Create a REAL `.claude/skills/verify-haplous-eval/SKILL.md` via the vendored verification-skill approach, grounded in existing commands, with Launch/Doctor/Drive/Evidence/Cleanup, feature map, test commands, and actual run artifacts. Make sure it fails on a deliberately broken example and that its own documented journey passes. Maintain it after changing CLI/replay surfaces.

## 2. Four product milestones — complete all in dependency order

### M2 — Physical recording, 3D replay, synchronized diagnostics

Take an ACTUAL existing `pegsim` / HEB MuJoCo episode and attach a bounded, nonblocking state recorder. Persist the exact relevant numeric state, original measured telemetry/contact, controls, supervisor events, task truth references, and pinned MJCF/assets/version/provenance. Use `mjtState`/`mj_getState` appropriately; never assume a subset restores the whole dynamic world. Capture clock domains explicitly: physics tick/sim time, host monotonic time, observation source/receive, dispatch/ack (when actually observable). Avoid huge lists of Python objects: fixed-size buffers, chunked Arrow/NumPy/zstd, explicit disk/RAM quotas, event-preserving logging. Run the simulator headlessly on CPU.

Create a historical replay command that opens saved data AFTER the original process is gone and shows physical movement with pause/scrub/reverse stepping. Use `mj_setState` + forward kinematics for rendering when correct; do NOT step the controller or physics during replay and pretend that is the observed history. Use Rerun `.rrd` for 3D transforms, plots and synchronized event markers, and export an MP4. RRD and MP4 are derived from the authoritative state/event bundle. Make offline verification possible (check samples, duration, changing frames, `rerun rrd verify` where installed). Preserve scene assets/checksums so runs can be reopened on Mac later; do not claim macOS GUI verified without running on Mac.

**M2 PASS** = a user or fresh agent can run one real physics episode, close it, load the run ID from M0, open its archived 3D trajectory, seek to an actual injected event, see correct time-aligned observation/command/physical motion, verify saved-vs-played poses within a declared tolerance, export an actual non-static video, and verify SHA-256 artifacts. Evidence survives cleanup. Record memory/size scaling and the effect of instrumentation (A/A or pass-through equivalence).

### M3 — Truly reusable evaluator with two physical task packs

Extend—not replace—`evaluation/experiment.py`, HEB and HES. Use existing `peg_insertion` and `constrained_slide` as distinct task packs. Map their actual controller/observation requirements. A task pack contributes scenario generation, runtime-visible observations, declared machine capability, disturbance schedule, task truth, grader and metrics, without changing central registry/statistics/replay code.

Support direct execution, pass-through instrumentation, a competent incumbent and Haplous-mediated execution where actually available. Keep G0 as an intentionally limited Track B ablation only. Ensure `Machine` and `MachineSession` semantics are not silently stretched: if the fixed xyz + axial stiffness command doesn't fit the second task, prove the specific limitation and implement the smallest justified capability-typed extension with backwards-compatibility tests. Never convert native force to joint torque by authorizing a string alias. Treat unsupported control modes as unsupported, and distinguish simulator's ideal command path from a real OEM's production interface.

**M3 PASS** = both genuine physical packs run through the SAME M0/HES evidence and visual replay pipeline, switching execution stack does not rewrite task grading, adding the second pack does not require edits to HES scoring core or recorder format, and negative tests detect injected privileged-truth leakage, wrong units/frames, wrong scenario pairing and unsupported capability claims. Pass-through runs preserve semantics within A/A variability.

### M4 — Model-/execution-intersection comparative campaign (CPU-valid scope)

Construct a proper G1 competent incumbent for each selected task using existing validated guards and machine capabilities. Compare fixed scripted or legitimately CPU-feasible upstream policies across direct / G1 / actual Haplous arms under matched scenarios, nominal and faulted. The comparison must actually advance MuJoCo and demonstrate physical effects of holds/revocations/interruptions, not just decision labels.

Log causal chain: observation → upstream raw proposal → interpretation/action chunk → Haplous/incumbent decision → command accepted or refused → physical state → independent truth/outcome; IDs and timestamps link records. If no learned VLA is run, label results **SCRIPTED-POLICY SIMULATION**; do not claim model gains. Keep external LIBERO-Dynamic and π0.5 as a separate future `ACCELERATOR_DEPENDENT` pack; no CUDA, no paid cloud GPU, no fake VLA benchmark.

Compute via deterministic code: success; bounded success over ALL scheduled tasks; acceptance coverage; invalid execution; false refusal with evidence-backed attainability; noninterference; recovery; violation conditional on acceptance; phase timing; total time per accepted task including failures/reset; inference/CPU overhead where measured; paired wins and regressions, CIs at defensible resampling units. Run one actual Haplous mechanism ablation and negative control if useful. Compare HES historical failures and resolved epochs without rewriting history.

**M4 PASS** = an independent reviewer can reproduce and audit paired dev results, identify both rescued and harmed scenarios, inspect corresponding replay clips, recompute all official metrics from raw rows, see any failed HES gates, and obtain a report that does not automatically declare Haplous better. A carefully planted score/trace mismatch fails validation. Development only; NO Tier 3 holdout.

### M5 — Autonomous feature extension and operational qualification

Prove the engineering loop by having a NEW agent, without chat memory, load the verification skill and state ledger, select one real unblocked improvement, implement it in its own branch, run end-to-end feature verification, generate proof, survive one simulated interruption/resume, and open a reviewable PR. Independent verifier must execute the real CLI/physics artifact at current head, compare raw metric values and prove a deliberately injected bad variant would fail. Use a focused model check or property-based exploration only on high-impact state/lease/nonce interleavings; do not formalize a whole simulated factory.

**M5 PASS** = fresh-agent cold start, correct delegation, correct worktree ownership, bounded error retries, full end-to-end check, proof-carrying PR, current-head independent verdict, maintained feature map, reproducible archive and clean shutdown. No auto-merge, no raw holdout exposure.

## 3. Fundamental engineering boundaries

- CPU MuJoCo C engine is canonical for these physics runs; JAX useful for existing numerical/estimator models, but MJX-JAX is NOT required (single-scene overhead and collision differences). No speculative Rust/C++ rewrite. Defer high-frequency embedded/OEM code until a measured control authority and timing problem warrants it.
- `haplous/` is the execution runtime; `hes/` is the independent evaluator; `benchmarks/heb/` owns scenario truth; `evaluation/experiment.py` owns episodes; M0 owns provenance; Rerun owns derived visualization. Never create duplicate owners.
- No privileged simulation truth inside policy or execution decisions. No changing historical qualifications or silently overwriting evidentiary artifacts. Scoring epoch and recorded source hashes remain explicit.
- Log full physical action timing in the actual observed clocks; a Python loop called at 1000Hz is not proof of 1000Hz plant authority or a production safe controller. A command refused by software does not imply motion has physically stopped.
- Expose explicit capabilities: modes (and verified substitutions), feedback, timing, calibration, sensing, stop and fallback. ROS 2/OEM adapters will need REAL production-interface validation later, including safety processes. Two MuJoCo tasks do not prove cross-OEM portability.
- Avoid redundant architecture and UI. Use existing simulators, libraries and Rerun. No general web dashboard unless actual archived-run viewing cannot work adequately with existing tools. Prefer a lean CLI with verified commands.
- For many runs, bounded memory and chunking are first-class requirements. Protect physics timing from synchronous video encoding/logging. Verify the actual logger overhead.

## 4. Enforcement and resource limits

Autonomous: repository inspection, source research, worktree branches, agent delegation, CPU code and bounded simulations, internal tests, replay artifact generation, reviewable PR creation.

**MUST STOP FOR HUMAN**: Tier 3 sealed holdout authorization; revealing restricted holdout details; paid GPUs/third-party billing; physical robot execution; safety-critical live deployment; publishing claims; deleting/force-pushing shared data; merging into `main` unless expressly authorized. A prompt alone doesn't implement security; do not pretend that Tier 3 is operational without enforced isolation and approval tests.

Always maintain an artifact size and CPU/RAM budget appropriate to the actual host, and fail cleanly when exhausted. Avoid open-ended token-spending retries or passive busy-wait. If the session stops, leave committed code and clear `PROGRAM_STATE.md` so a new session can pick up deterministically. Never claim to be running after the session terminates unless a persistent job was actually launched.

## 5. Orchestration method

Use pstack Orchestrate's discipline: one owner per PR, a verifier other than its author, a first proven unit before fan-out, read the artifact/diff not just a worker summary. But override Cursor-specific tooling with ACTUAL supported Claude Code mechanisms. First create a DAG with milestone dependencies and measurable exit predicates; implement M2's thin vertical slice immediately. After M2 proves one run/replay and verification, parallelize M3 recorder/viewer, task adapter, and statistics/reports only where files are truly independent. Use explicit ownership, worktree isolation and stable, small interfaces. Never spawn agents simply to look busy.

Every completed change gets: code; targeted and relevant full tests; real runtime evidence; reproduction command; checksum or trace ID; limitations; current head; independent acceptance verdict. Every failed milestone gets exact test ID, root-cause hypothesis and next independent diagnostic—not a softened milestone definition.

## 6. Final mandatory demonstration (actually run)

On a clean CPU-supported checkout:
1. inspect recorded experiment registry and baseline health;
2. run peg insertion under a known controller, save original state/events;
3. exit the original simulator;
4. reopen and seek archived 3D trajectory, inspect a fault/decision on the same timeline, export valid MP4;
5. run constrained slide through the same evaluation path;
6. run competent incumbent and actual Haplous under matched scenarios, including a real interruption/fault;
7. recompute HES metrics and reports from original raw rows, including negative results;
8. verify hashes, A/A effects, artifact memory limits and no privileged observation leaks;
9. run all relevant tests, retain and explain any pre-existing failures; and
10. create reviewable PR(s), include independent verification receipts and complete reproduction instructions.

**Terminate the program only when M2–M5 pass their exact acceptance checks or a genuine resource/authorization blocker remains.** Do not mark a milestone complete because documentation exists or a mock returns an expected value. Report precisely which tests and actual artifacts support each pass. Work now.
