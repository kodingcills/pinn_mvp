# HAPLOUS — AUTONOMOUS PRODUCT ENGINEERING PROGRAM

**Program version:** 1.0 · **Prepared:** 2026-11-17 · **Destination repository:** `kodingcills/pinn_mvp` · **Execution model:** pstack-enabled Claude Code engineering lead with bounded subagents and independent verification.

**This is an implementation mandate, not a request for another design document.** Start from the actual current `main`, inspect existing implementations, create a manageable issue graph, then implement and verify the product milestones. Work through ordinary programming decisions autonomously. Do not stop after an architectural sketch.

## 0. Mission, scope, and truth conditions

Haplous is an emerging **generalized physical-execution runtime** for embodied AI and robotic software. It sits between high-level physical intent (VLA, planner, scripted policy, teleoperation) and the machine-specific controllers that actually execute motion. Its proposed responsibilities are explicit execution contracts, capability-aware admission, temporal/state validity, authority, bounded supervision, recovery/refusal, and reconstructable execution evidence. Haplous does **not** need to own model inference, task planning, the inner servo, or safety-rated machinery protection.

The company hypothesis is **not proven**: there may be a valuable, reusable execution boundary beyond OEM controllers, `ros2_control`, common middleware, system integrators, and application-specific engineering. The engineering program must discover whether this software can be useful, portable, economically repeatable, and worth buying. Do not protect Haplous from competent incumbent comparisons.

**Two simultaneous product north stars**:

1. Ship a genuinely installable, operable **Haplous execution runtime** whose command, timing, authority, capability and recovery semantics can work through multiple materially different supported control interfaces.
2. Ship the **Haplous evaluation + development engine** that creates experiments, tests correctness, executes physics, finds counterexamples, compares incumbents, measures transfer effort, and drives evidence-backed runtime changes. Studio is the human-facing workbench of that engine, not its substitute.

Do **not** wait for the evaluator to be finished to improve the runtime; do **not** wait for the runtime to be finished to improve the evaluator. The systems advance in paired vertical slices. Agents may refactor and delete unfinished code when tests and evidence justify doing so.

### Authority and source precedence

Read, when available in the actual checkout or supplied input: the Haplous Manifesto, Hypothesis Ledger, Decision & Evidence Log, November 3 technical report, `README.md`, `AGENTS.md`, `docs/eval_program/PROGRAM_STATE.md`, `KNOWN_ISSUES.md`, `M4_REPORT.md`, design decisions, and relevant source/tests. The September 8 Current State is historical, not the current implementation status. Distinguish *product mission* (normative), *design proposal* (unverified), *repository code* (implemented), and *run artifacts* (empirical evidence). Existing decisions can be superseded by new documented evidence, not silently rewritten. If a cited source is absent, record the absence; do not invent its contents. **Important instruction conflict to resolve explicitly:** the current `AGENTS.md` has a behavior-freeze rule requiring a locked design and parity tests before changing capability/refusal/hold/recovery semantics, and it names an old integration branch. This mandate authorizes substantive refactoring, but *not* bypassing tests or silently violating repo instructions: create a short issue-level design and migration/parity checks, then update `AGENTS.md` in the first productization PR to reflect the approved refactoring workflow and current branch state. Preserve historical decisions; document supersessions.

### Verified starting facts to recheck, not assumptions to make permanent

- Existing modules: `haplous/`, `pegsim/`, `benchmarks/heb/`, `hes/`, `evaluation/`, M0 registry, `studio/` and CLI. Do not rebuild them as separate frameworks.
- `studio/` already implements a local browser release with run launch, 3D replay, comparisons, investigation creation and evidence; improve existing functions rather than reskinning them.
- Current `pyproject.toml` still distributes as `pegsim` and requires `mujoco` and `numpy` in core dependencies, even though a runtime-only install should not need physics simulation.
- The runtime's `ServoCommand`/`MachineObservation` assume current contact tasks; `MachineSession.step()` couples product execution to simulation stepping. This needs architectural evaluation and likely refactoring.
- `studio/jobs.py` uses `/proc/<pid>` for restarted worker liveness, which is not cross-platform and does not prove process identity.
- `haplous/adapters/external.py` has `health()` forwarding `ExternalTask.ground_truth()`; verify and correct the privileged-information boundary if still present.
- The M4 scripted-policy MuJoCo campaign reports **no bounded-success difference** between Haplous and the competent G1 incumbent on 501 paired scenarios; the stress population also fails the non-interference threshold (0.625 versus 0.95). Other evidence includes force-limit overshoots after stale-state holds. These are product problems, not results to conceal.
- `KNOWN_ISSUES.md` previously reported four pre-existing test failures and several research/scoring limitations. Reproduce on HEAD and classify. Do not assume a historical test count still matches HEAD.
- There is **no GPU available**. Do CPU-first development. Full-sized VLA benchmarks, cloud GPU purchases, physical robot deployments and Tier 3 sealed evaluations are not authorized by this mandate.

## 1. Operating principle: build a *product*, not a pile of features

The product output is an integration a robotics engineer could install and operate **without modifying Haplous internals**. The value output is a result showing that Haplous enables a requirement, preserves a useful behavior, reduces a failure, or reduces incremental integration work **relative to a competent existing implementation**. A feature is not useful because it has tests or a UI tile.

For each significant task, answer before coding:

1. Which real execution problem and user workflow does it address?
2. Why is Haplous—not the policy, native controller, or evaluator—the appropriate owner?
3. Which machine capabilities and timing assumptions are required?
4. What is the smallest integrated implementation that can prove it?
5. What observable runtime and physical tests would falsify correctness?
6. What would an experienced integrator use instead?
7. How does this affect installation, support, adapter effort and repeated deployments?
8. What outcome would cause us to revert it or deprioritize the hypothesis?

A clean abstraction with no new executable capability, fewer integration steps, or newly proven property is **not** a product increment.

## 2. Engineering orchestration: pstack with evidence, not free-form swarming

### Coordinator role

You are the single accountable integration and scientific-integrity owner. Inspect current HEAD, existing GitHub issues/PRs, tests and evidence. Build a dependency graph. Own architecture boundaries and sequencing. Spawn **at most three active implementation workers and one verifier**; start with fewer. The coordinator alone creates/triages GitHub issues to prevent duplication. An implementation worker may propose findings but does not independently spawn projects or change the scientific claim.

Use the vendored `.claude/skills/` pstack playbooks when they actually work in Claude Code: `/poteto-mode`, `/how`, `/why`, `/architect`, appropriate test/verification workflows, and the maintained Haplous verification skill. Determine the available names and actual mechanisms rather than relying on Cursor-only model slugs, a Cursor transcript path, Bun-only scripts, or nonfunctional `/loop` commands. Ordinary subagents and worktrees are acceptable fallbacks. No extra agents for mechanical edits that are cheaper to do directly.

### Role assignment

- **Runtime / control-interface owner:** action, clock, authority, machine adapter, lifecycle and migration; no benchmark scoring changes.
- **Evaluation / physics owner:** closed-loop scheduler, scenario operations, benchmark populations, truth isolation and numerical measurement; no secret scoring changes to favor a candidate.
- **Product integration / Studio owner:** installable artifacts, CLI, Studio operations, durable jobs and developer workflows; no runtime-semantic changes without agreement.
- **Independent verifier:** does not author the implementation under review; reproduces required checks from a clean checkout and tries deliberate defects. A verifier's verdict is bound to the exact commit SHA.

Create a worktree/branch per substantial issue. Assign one owner per file group and PR. Before parallel edits to shared types, the coordinator writes a short decision in the issue defining signatures and migration; this can be done *during* engineering, without awaiting a large upfront ADR.

### Work unit loop

`TRACE REAL CODE → RECOVER WHY → DEFINE FAILURE / OUTCOME → SKETCH TYPES + ACCEPTANCE → IMPLEMENT → RUN REAL TESTS → INDEPENDENT VERIFY → COMMIT / PR → INTEGRATE OR REVERT → UPDATE BACKLOG`.

`/create-verification-skill` and `/maintain-verification-skill` must maintain testable **Launch, Doctor, Drive, Evidence, Cleanup** procedures for runtime-only installation, MuJoCo physical runs, Studio, and the reference integration. Verification must exercise the actual user-facing path; mock endpoints and screenshots alone do not suffice. Existing working verification skills should be extended rather than duplicated.

### No fake background progress

A Claude cloud session may continue remotely only as long as its execution environment is actually running. A persisted issue list or task ledger does not itself continue work. Store current branch SHA, active issue, exact next command and evidence paths at coherent checkpoints. If hosting stops, report it honestly and make resumption one command/instruction, not a restart of the project.

## 3. GitHub issue system — required, disciplined and bounded

**GitHub Issues is the authoritative action backlog.** GitHub PRs are the implementation/evidence trail. `docs/eval_program/` keeps historical research context; do not create a competing exhaustive task tracker. A compact `docs/engineering/PROGRAM_STATUS.md` may summarize the live issue graph and next action only.

### First action: audit before creating

Run `gh issue list -R kodingcills/pinn_mvp --state all --limit 100` or the equivalent authenticated GitHub integration, then inspect relevant titles/bodies and active PRs. The repository previously had broad open issues `#1 Find More Data`, `#2 Further Validation`, `#3 UI`, `#4 Fix Current Bugs`, `#5 DEVIBE CODE`. Do **not** use them as acceptance criteria; inspect, refine/link or supersede them appropriately without deleting history. Search existing issues and code before filing a new one.

Create at most **four program epics** (or reuse coherent existing epics) and **at most eight immediately actionable child issues** at kickoff. Add more only as work demonstrates a dependency or failure. Do not file 50 speculative ideas. Only one to three implementation issues should be in progress simultaneously. Use GitHub native parent/sub-issues and dependency links where enabled.

### Discovery protocol: what happens when an agent researches and finds a problem?

1. **Capture:** identify a precise observed mismatch, source version, experiment/command, expected versus actual, smallest available reproduction, severity and affected user/product claim.
2. **Classify:** `REPRODUCED DEFECT`, `VALIDATED LIMITATION`, `INVESTIGATION NEEDED`, `FEATURE OPPORTUNITY`, `EVALUATOR DEFECT`, `EXTERNAL DEPENDENCY`, or `OUT OF SCOPE`. Do not label an unverified hypothesis a bug.
3. **Check duplication:** search existing issues, previous decisions, documented known issues, and related PRs. Add evidence to the existing issue when applicable.
4. **Decide priority using the rubric below.** If it blocks the active dependency or invalidates measurements, work it now. If it is not critical path, file one bounded issue (if concrete) and return to active work. Do not follow a chain of adjacent curiosities.
5. **Make the issue actionable:** include a falsifiable acceptance test, an owner, dependencies, a reproducible artifact and a scope exclusion. Link to a test or code location and the relevant product hypothesis. The issue must be finishable without rereading the entire chat.
6. **Close with proof:** a linked PR, specific commit, executed test/result, baseline and verification verdict. If evidence says the issue is not worth building, close `not planned` with rationale; no fake completion.

### Priority rubric

| Priority | Action | Examples |
|---|---|---|
| **P0 — trust/security/correctness blocker** | Stop the affected path, create/update issue immediately, isolate and fix before claiming results | Grader truth leaking to Haplous; malformed score counted as valid; wrong machine command dispatched; unbounded authoritative actuation |
| **P1 — product critical path** | Include in current milestone and dependency graph | Core package requires simulator; async command ack/lifecycle unsupported; installed Studio cannot locate assets; actual integration test broken |
| **P2 — useful but nonblocking** | File or update a scoped backlog issue; do not implement during current critical path | Extra visualization, new benchmark family, another machine brand, optimizer experiment not needed for current proof |
| **P3 — polish/speculation** | Record a short note only if useful; no issue unless user requests | Rewording, aesthetic polish, speculative framework rewrite, convenience abstractions |

### Research time / relevance guard

Investigations begin from the concrete failing code path or unmet product requirement. Prefer primary source/OEM docs and minimal reproduction. An exploration should normally produce an answer or decisive experiment within **one bounded research pass** (e.g. 2–4 authoritative sources and one spike), followed by a decision. Extend research only for a P0, blocked P1, or a high-stakes interface compatibility question. Unresolved = `UNKNOWN`, with precise unblock action. Do not spawn a new explorer for every referenced framework, research paper or alternative.

### Issue content standard

Every engineering issue must contain:

- Product outcome / why this matters (one concise paragraph)
- Observed evidence, version and reproducible command/trace (or explicit `UNKNOWN`)
- Expected behavior / violated execution property
- Affected files/interfaces and owner
- Minimal proposed solution, alternatives, scope exclusions
- Dependencies / blockers / relation to hypothesis
- Unit, integration, negative and real execution acceptance tests
- Independent verification plan and evidence artifacts
- Priority and stop/revert rule
- Done checkbox (only after evidence exists)

Use the issue template in `ISSUE_TEMPLATE.md`. Do not open an issue for a generic aspiration like “Make Haplous generalized.” Break it into one testable machine interface, command semantic or integration outcome.

### GitHub mechanics

If authenticated `gh` CLI is present:

```
gh issue list -R kodingcills/pinn_mvp --state all --limit 100
gh issue create -R kodingcills/pinn_mvp --title "runtime: ..." --body-file issue.md --parent <EPIC_NUMBER>
gh issue edit <ISSUE_NUMBER> -R kodingcills/pinn_mvp --add-blocked-by <BLOCKER_NUMBER>
gh pr create -R kodingcills/pinn_mvp --title "..." --body-file pr.md
```

Replace angle-bracket placeholders with real issue numbers. Labels must exist before use; do not assume they do. If `gh` is unavailable, use the connected GitHub API/integration only if it provides authorized issue creation. If neither can write, produce a ready-to-paste issue body at `docs/engineering/issue_proposals/` and mark `ISSUE_SYNC_BLOCKED`; do not falsely claim an issue was filed. Do not force or auto-merge without operator authorization.

### Duplicates and dependency hygiene

One canonical issue per defect/hypothesis. Link follow-on discoveries rather than cloning the same problem. A sub-issue must have its own independently executable acceptance test; otherwise leave it as a checklist item in the parent. A blocked issue is not active WIP. An issue closed by a failed/negative result is legitimate if it satisfied the predefined decision criterion.

## 4. Architecture choices to implement or validate in code

These are hypotheses and intended boundaries, not instructions to preserve old APIs uncritically.

### 4.1 Product packaging boundary

Monorepo remains appropriate. Produce a runtime-only installable **Haplous** distribution with minimal dependencies and a demonstrably clean import/install without MuJoCo, Rerun or FastAPI. Retain the research `pegsim` module as an optional simulation integration rather than deleting research. Separate extras for `sim`, `eval`, `studio`, `dev`; test built wheels in fresh environments and verify packaged static assets/evidence handling. Provide `haplous doctor`, docs, upgrade and uninstall paths. Do not publish to a public registry without approval. Test both macOS and Linux where execution environments actually exist; do not claim untested platform coverage.

### 4.2 Machine runtime — no simulator-shaped interface

Refactor `haplous/machine.py`, `runtime.py`, `intent.py`, `capabilities.py` and adapters as necessary. Explicitly separate **world stepping** (simulation driver) from **execution authority** (Haplous) and **actuation** (machine/controller). Existing `step()` paths may be adapted during migration but must not remain a mandatory universal real-machine concept.

Minimum product-facing concepts:

- Versioned **execution contract**: requirements, permitted commands, time validity, machine capability dependencies, failure/recovery policy, outcome verification references. Keep runtime contract serializable; task-specific scoring remains in the evaluator.
- Typed **action envelopes**: command identity, authority/lease epoch, origin observation, clock domain, validity horizon, payload semantics, correlation IDs. At least two genuinely different command surfaces exercised end-to-end (e.g. legacy Cartesian/impedance and joint-position chunk/trajectory); choose the second using the concrete machine/control implementation.
- Explicit **capability profile**: real control modes, sensing, command acceptance and interrupt semantics, calibrated/verified adapters, version and evidence. No silent semantic downgrade. Never equate `native_force` with `joint_torque` without demonstrable behavioral equivalence and authorization.
- **Async dispatch + acknowledgement**: distinct accepted-for-dispatch, controller-accepted, executing, completed, rejected, canceled, unknown, faulted; duplicate/out-of-order acknowledgements and disconnects do not silently authorize new actuation.
- **Authority model**: exclusive resource lease where appropriate, epoch monotonicity, revocation, replay rejection and recovery after restart. The host platform may already manage exclusive physical resources; integrate rather than claim to supersede OEM/ROS ownership.
- **Clock model**: distinguish sim, controller, producer, monotonic/wall; handle unknown synchronization bounds. Measure decision-to-dispatch and request-to-physical-effect separately; do not conflate CPU wall time and simulated cycle time.
- **Nonblocking evidence**: bounded memory and structured append-only events. Recording failures are visible and may disqualify a claim; don't silently lose authoritative state.

Product runtime must never import HES, benchmark ground truth or MuJoCo; adapters can import their respective SDKs. OEM/safety-rated claims require separately verified hardware interfaces and permissions; do not infer them from a simulated adapter.

### 4.3 Physical control and credible integrations

Use native MuJoCo for CPU physics; keep JAX an optional numerical/policy backend, not a mandatory physics rewrite. Reuse existing insertion/sliding and physics validity tests. Preserve controller-specific details outside the generalized runtime. Build one meaningful second control interface and test its interrupt, timing and failure semantics. Research `ros2_control` or a selected OEM interface using pinned docs before implementing an adapter; an interface that compiles but lacks real supported control authority is not a proven integration. A mock ROS controller can validate protocol semantics; it is not an actual physical machine integration.

### 4.4 HES / HEB / M0 evaluation

Do not create a new evaluator, metric engine or experiment registry. Extend the existing owners:

- **M0:** run identity/provenance, manifests, immutable evidence, artifact integrity.
- **HEB:** task physical realization, scenario validity, controlled fault injection, independent ground truth, paired episode execution and replay.
- **HES:** claims, test selection, falsification, boundary and metamorphic tests, confidence/uncertainty, non-interference, qualification, metric/version semantics.
- **Studio:** interactive experiment launcher, benchmark/dataset/robot/policy descriptors, Performance Atlas, counterexample-to-investigation workflow, comparison and historical evidence; backend numbers always from official artifacts.

Introduce a small deterministic **claim-to-experiment compiler** when the first two real claim examples demonstrate need. Inputs: property, requirements, supported machine profile, benchmark/task family, comparators, scenario operators, evaluation protocol. Outputs: valid executable development plans or `UNSUPPORTED` with explanation. Start with existing HES operators; no standalone DSL or new simulation platform without a demonstrated gap.

Separate development search (stratified, boundary, counterexample minimizing, fault interactions) from frozen confirmatory populations. Development population failure rates are not real-world rates. Do not re-use adaptive development data as unseen confirmatory data. All Tier 3 held-out runs require human authorization via separate enforced trust boundaries; current mandate does not activate Tier 3.

### 4.5 Studio as a working engineering workbench, not a second product

The existing Studio release is real code. Extend it only when a backend feature needs a human workflow:

- Show exact **machine/robot**, **policy**, **dataset or scenario family**, **benchmark version**, **incumbent** and **Haplous build** for every comparison. Explicitly distinguish not-run, unavailable and zero effect.
- Performance Atlas: helped/harmed/unchanged, bounded success, coverage, false refusals, recoveries, physical violations, sim cycle time vs CPU overhead, CIs and slice counts; show missing coverage rather than filling heatmap cells with estimates.
- Capability Lab: create an engineering investigation linked to a GitHub issue, start a verifiable development workflow through supported agent access, track PR and qualification results. Do not execute arbitrary shell supplied by a browser; don't store Claude credentials in the frontend. Start with issue/PR integration rather than one-click unsupervised code execution.
- Preserve actual MuJoCo numerical recording and Rerun playback; derived visualization cannot be the scientific truth store.
- Make jobs durable across Studio/server restart and usable on macOS and Linux; do not mistake a stale PID for a live worker. A wheel-only install must have a usable run library, not require the original repo's `results/` layout.
- Maintain one CLI/API backend for scripts and Studio; no separate simulation logic in frontend.

## 5. Milestone DAG with testable delivery gates

These are integrated product milestones, not sequential documentation deliverables. Run independent tasks concurrently only after cross-cutting interfaces are agreed and a vertical slice passes.

### Gate A — Developer Alpha: installable, operable product

Ship: runtime-only package, clean reproducible install, CLI/doctor, external example showing correct admission/refusal and structured evidence, working optional sim + Studio extras, durable jobs/artifact root, package and platform checks, clean migration from existing API. Fix known defects that threaten evidence trust or this user path.

**Done when** a fresh checkout/build creates a wheel, a fresh environment without MuJoCo imports `haplous` and executes a deterministic stub-machine contract, optional `sim` environment runs a real MuJoCo episode, `haplous studio` opens the actual verified run, and a process restart does not lose or misidentify active/completed jobs. An independent verifier reproduces the journey and plants a dependency/corruption fault.

### Gate B — Generalized execution: real second command surface and async lifecycle

Ship: typed and versioned commands/observations, clock/identity semantics, machine capability mapping, authority and dispatch lifecycle, two materially different control/command surfaces, compatibility migration, predictable restart/reconnection behavior. The actual `haplous/` runtime changes; don't hide a deficient runtime behind benchmark wrappers.

**Done when** scripted or frozen policy actions pass through two real end-to-end adapter implementations with different capabilities; old semantics pass appropriate parity tests; unsupported/unauthorized command substitution is refused; command duplicate/revocation/expiry/disconnect/late acknowledgment behavior is tested; physical response to hold/stop is measured separately; simulator advancement is not owned by Haplous core; no grader truth crosses into runtime observations. The verifier deliberately injects a delayed ack, stale observation and authority conflict.

### Gate C — Differential value: find a mechanism worth shipping

Inspect the **historical frozen M4** G1/Haplous comparison as evidence (tie, non-interference loss, stale-hold force exceedances and collider fidelity limits). **Do not retune against or reuse its confirmatory population as an unseen holdout.** Construct fresh development scenarios for candidate engineering and separately authorize any new Tier 3 claim. Form **at most two product hypotheses at a time** (e.g. capability-envelope semantics; bounded cancellation/recovery without unnecessary interruption). Build a candidate mechanism inside Haplous, independently design matched cases, run ablation and physical tests, quantify added overhead, accepted work, unnecessary stops, bounded success and actual physical violations.

**Done when** one clearly defined product mechanism has an evidence-backed **KEEP / REVISE / REVERT / NO INCREMENTAL VALUE** decision relative to a competent incumbent, with replicated raw trials and independent grading. A negative finding is acceptable completion. Do not weaken G1 or add easy synthetic faults solely to create wins. If no addressable difference appears, stop polishing that mechanism and test the next commercially plausible hypothesis.

### Gate D — Evaluator as active engineering system

Ship: claim-to-experiment selection for at least two nontrivial existing/real properties; scenario validity checking; boundary/failure-directed generation; counterexample minimization; clear separation of development versus confirmatory evidence; HES qualification; provenance; Studio Performance Atlas and issues from investigations. Reuse current HES, HEB, M0 and visualization.

**Done when** a developer inputs an execution claim and specified machine/benchmark population, the system generates supported tests or reports why unsupported, runs valid paired CPU experiments, finds/reduces at least one counterexample (or documents no discovered counterexample under bounded search), exports raw artifacts and plotted results, and can create a reproducible issue from a replay. Independent tests catch a swapped-pairing bug, a truth leak and a modified scoring artifact.

### Gate E — Integration Beta and cold-reader handoff

Ship: documented integration using a second realistic controller interface (simulated or test-controller environment when no real robot is accessible), configuration and capability verification, operational restart/fault handling, limits and support docs, versioned release notes, example app, developer onboarding and migration guide. Demonstrate bounded adaptation effort across a second task and different command surface; record human/agent engineering time and modifications, not just lines of code.

**Done when** an agent/engineer unfamiliar with prior conversations installs it from a clean environment, plugs a policy or scripted planner into each supported control surface, runs a physical experiment, inspects an archived replay and comparative report, reproduces a known negative case, and can add one small supported contract feature using the verification skill and create a reviewed PR. A second verifier reproduces the journey at the exact commit. Label anything requiring hardware `HARDWARE_NOT_VALIDATED`.

### Program success / completion

Program is **SHIPPED (Developer Alpha + Integration Beta in simulation)** only if Gates A–E are proved by artifacts. It is **NOT** a safety-certified production industrial platform or a proven high-margin business. It must not require GPU compute for the accepted CPU milestone. A real-design-partner/hardware pilot is a separately authorized subsequent milestone with machine-specific qualification and buyer evidence.

## 6. Test, evidence and review gates

**Baseline:** run all relevant test suites once on the starting commit; record pass/fail/xpass/skip, platform and hashes. Work in a separate branch; preserve earlier evidence. Known failures are not automatically ignored—classify whether they undermine current claims or user journeys.

**During development:** targeted tests after coherent changes; type/lint when applicable; no full-suite reruns for every tiny edit. Formal verification/property testing only for discrete authority and lifecycle models that benefit from bounded exhaustive exploration. Physics validity needs contact/sensor/mesh convergence and independent outcomes, not a TLA+ proof.

**At merge-ready milestones:** clean install smoke, contract tests, integration tests, physical scenario + replay, deterministic metrics and paired results, end-to-end Studio path, full relevant suite and independent verifier review. Verification includes planted bad inputs, false refusal, delayed acknowledgement, corrupted artifact and scorer leakage where relevant.

**Evidence format:** source SHA; Python/MuJoCo/OS versions; scenario ids and seeds; immutable plan/config hash; comparator and controller configs; available observations; clock/timing semantics; raw episode files; independent scoring algorithm version; exact unit/denominator; error bars; failure/retry accounting; replay location; verifier identity/verdict. Nothing becomes a product claim because an agent summarizes it elegantly.

A PR may close an issue only after its exact acceptance checks have been run and linked. The independent reviewer must name what it reproduced and what remains unverified. Every source-code change that can alter historic scores creates a new scoring version and an impact note; do not rewrite sealed historical evidence.

## 7. Budget, side-quest and stop rules

**Hard rule: maximize verified engineering progress per unit of agent usage, not number of agents, issues, files, or tokens.**

1. Default **one coordinator + one owner**, add a second/third owner only for proven independent critical-path work. Use a verifier for integration, correctness and evidence-sensitive changes, not trivial lint.
2. **Work in progress ≤3 substantive issues.** Keep only the next 1–2 active product hypotheses. Do not design more than four program epics at kickoff.
3. **Research bounded to decision.** Start with source trace and primary docs; avoid repetitive broad searches. If answer requires a new tool/framework, run one minimal compatibility spike before committing the architecture. Stop researching once decision quality is sufficient.
4. **No speculative infrastructure.** No Kubernetes, service mesh, universal scenario language, new controller stack, rewrite in Rust, or full user-management/authentication system without an actual measured requirement.
5. **No gratuitous compatibility.** Refactor and migrate callers when old APIs are wrong; preserve benchmark and historical evidence validity, not every experimental private interface.
6. **Three diagnostic approaches maximum before escalating** a hard unresolved failure to the issue with evidence. You may resume a new path only after learning something materially new.
7. **No vanity milestones.** A feature is done only when a non-author can use it in the real runtime/UI/integration path and prove the product property. A mocked screenshot, schema or compile-only integration is incomplete.
8. **Don't optimize bad metrics.** Separate physics simulated seconds, wall/CPU compute, acceptance/success, constraint violations, false refusals and engineering effort. Never pool incompatible scoring epochs.
9. **Kill/rollback allowed.** If a proposed mechanism adds no incremental value after a credible bounded evaluation, close it as negative evidence or revert it; avoid indefinite threshold tuning.
10. **No hidden spend.** No GPU purchase, paid services, hardware control, Tier 3 sealed tests, main-branch merge or external public claims without explicit human approval.
11. **Stop at the program definition of done.** No extra benchmark suites, machine brands or UI redesigns after A–E pass unless they fix a demonstrated release-blocker. New ideas enter backlog, not active execution.

### Token/agent-efficiency reporting

At each integrated checkpoint report only: completed issues and exact SHA, passing gates and linked evidence, current critical blocker, next 1–3 independent tasks, and a short resource summary if available. Avoid re-narrating the entire project or repeatedly generating new architecture docs. Use durable files so fresh sessions don't consume tokens rediscovering work. If actual credits/usage are not observable, mark `USAGE_NOT_MEASURED`; do not invent costs.

## 8. Starting sequence (execute immediately)

1. Verify repo and current `main` HEAD, permissions, open issues, merged PRs and branch state. Never assume the deleted or old `claude/magical-curie-si1vjr` branch exists.
2. Read mission and current evidence; inspect `pyproject.toml`, runtime `machine.py`/`runtime.py`/`capabilities.py`, HES/HEB/M0, `studio/jobs.py`, existing adapter boundary, known issues.
3. Run the minimal product smoke + baseline tests; record exact failures. Verify whether the three concrete packaging/job/runtime boundaries and evaluator truth-leak still exist.
4. Produce **one short trace/why/architect note** for the cross-cutting machine/clock/command and packaging refactor, with types, ownership, migration and tests. Do not write a 50-page architecture report before coding.
5. Search GitHub issues, create/reuse no more than four epics and eight actionable issues. Put actual dependencies in GitHub. Post initial issue URLs in program status. If issue creation is blocked, save proposal bodies and continue code.
6. Assign Gate A/B work (packaging + first real asynchronous machine vertical slice); independently start only compatible evaluation hardening work. Prove a vertical slice before increasing parallelism.
7. Implement, test, review, integrate, and continue through Gates C–E using the issue graph and stop rules. At every milestone deliver actual code, verified evidence and a reviewable PR; do not merge to main unapproved.
8. On successful delivery, produce `PRODUCT_RELEASE_READOUT.md`: supported machines/modes, tasks, policies, tested properties, known limitations, install and operational commands, numeric comparisons, failures/regressions, engineering adaptation time, customer-ready gaps, linked issues and PRs. Explicitly differentiate software-ready, simulation-tested, hardware-unvalidated and commercially-unproven.

**Now begin. Do not end after planning. Ship the next useful runtime increment and the experiments that tell us whether it is good. Continue through the authorized acceptance gates or report precisely why a gate cannot be completed.**
