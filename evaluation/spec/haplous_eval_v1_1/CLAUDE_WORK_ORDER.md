# Haplous Evaluation System — Build Order v1.1

**Status:** v1.1 engineering work order and validated fixture/schema handoff, not a completed experiment or security certification.  
**Date:** 2026-11-03.  
**Objective:** Generate falsifiable evidence about execution improvements by reproducing a dynamic frozen-policy benchmark while independently testing execution-contract semantics. Preserve human authorization for future confirmatory holdouts without delaying baseline research.

## Operating principles

1. Never claim Haplous performance while a matching Haplous mechanism is not implemented and evaluated.
2. Keep model, execution, environment, evaluator, and independent ground truth logically separate. In closed-loop A/B testing, matched runs have the same exogenous initial scenario but diverge naturally after interventions.
3. Run LIBERO-Dynamic **natively**, pinned to its specific source revision and compatible Python/dependency environment. Do not combine with vla-eval 0.9 unless a separate integration experiment earns that cost.
4. Published best-K results were selected on their evaluation population. Reproduce for engineering comparison, but tune any incumbent K and Haplous parameter on a separate development split before a new confirmatory claim. Do not describe shared phase offsets as unseen trajectories.
5. A/A and pass-through are mandatory. Separate (a) same-seed replay/trace consistency, (b) stochastic-policy variability across seeds, and (c) direct-native versus instrumented or sandboxed execution. Do not conflate them.
6. Use CPU-only contract/fault tests while waiting for GPU access. GPU availability and resources are NOT verified by this work order.
7. Every run must produce a manifest, immutable raw-output directory, checksum inventory, concise machine-readable result, and failure diagnostic.
8. Tier 3 never runs from an agent prompt alone. An independently deployed trusted runner, sealed datasets, permission enforcement and signed one-time approval must exist and pass adversarial checks before any Tier 3 job.
9. For reporting, numerical metrics come from deterministic code, explanations from the report agent. Evidence labels are VERIFIED, INTERNAL RESULT, INFERENCE, HYPOTHESIS, UNKNOWN.
10. Do not silently convert failure to `PASS`, skip missing runs, tune on holdout, or create a polished dashboard before evidence validity is demonstrated.

## Track A — Campaign 0A (manual or supervised, native LIBERO-Dynamic)

**Source:** https://github.com/renshaojie233/LIBERO-Dynamic . Its README describes Python 3.10, policy-free preflight, PyTorch pi0.5 async and RTC launchers, physical carrier motion and 1,800 public benchmark conditions. Source HEAD can change; pin commit SHA and inspect actual README, requirements, formal protocol, scripts and implementations at that SHA.

### Stage 0 — Inventory and compute feasibility

- Capture source SHA, license, Python version, dependency lock/requirements hash, MuJoCo/robosuite versions, GPU model/VRAM/driver/CUDA, available disk, checkpoint sizes and SHA256, checkpoint licensing, wall-clock and hourly cost estimates.
- Inspect `scripts/preflight_dynamic_configs.py`, `scripts/eval_dynamic_dummy_policy.py`, `examples/evaluate_pi05_pytorch_async.py`, `examples/evaluate_pi05_pytorch_rtc.py`, `docs/formal_dynamic_eval.md`, `docs/reproducibility_notes.md` (verify paths after pinning).
- Capture the exact environment and command/argument used for every reproducibility run. Do not fabricate GPU availability; if absent, output `GPU_BLOCKED` and execute policy-free smoke and CPU Track B if feasible.
- Decide whether checkpoint runtime/VRAM requirements are feasible on available infrastructure. Do not claim a successful reproduction until executed.

**Exit:** `source_inventory.md`, `gpu_inventory.json`, pinned environment spec and `run_manifest.json`.

### Stage 1 — Policy-free smoke + timing correctness

- Follow native setup from pinned revision; run `python scripts/preflight_dynamic_configs.py` and inspect `python scripts/eval_dynamic_dummy_policy.py --help` before choosing an invocation.
- Verify the benchmark's physics carrier moves through contact, not via target teleportation. Inject or configure two distinct calibrated model-latency values, and record changes in simulation step count, simulation time, target displacement, and action queue behavior. Distinguish simulated inference latency from actual wall-clock inference time.
- Test nominal and moving-target conditions; ensure the same defined episode termination, time budget, task success scorer and reset semantics.
- Fail the venue if observed target motion and injected latency do not match configured semantics within preregistered tolerances.

**Exit:** smoke-test logs; timing trace; observed-vs-expected table; `VALID`/`BLOCKED_WITH_REASON` verdict.

### Stage 2 — Policy reproduction and baseline instrumentation

- Reproduce a compatible pi0.5 PyTorch asynchronous checkpoint on a **small development pilot first**, then the official reference protocol only when compute budget allows. Published 48.4% is a best-measured-K reference, NOT a validation-selected head-to-head benchmark result; report any reproduction delta without tuning secretly on final results.
- Read the exact launcher implementation and confirm checkpoint action representation, model evaluation mode, asynchronous action buffer, observation cadence, action K, controller frequency, and command interpretation.
- Add non-invasive observation-to-command trace capture: observation ID/time; inference request/completion; action chunk ID/horizon; queue state; command dispatch and simulated application; scene time; task outcome. Maintain separate sim and wall clocks.
- Run (1) direct native incumbent, (2) instrumented pass-through, (3) same-seed repeated incumbent, (4) seed-varying A/A. Establish per-task, per-condition variability and whether instrumentation changes action semantics or latency.
- Only then determine a competent K tuning procedure on an independent development population; do not use published best-K as if selected on a disjoint validation split.

**Exit:** reproducibility report; A/A baseline; pass-through equivalence report; example linked traces; deterministic aggregate script.

### Stage 3 — Opportunity audit (design after Stage 2, not a prerequisite for Stage 0)

- Against the locally rerun tuned incumbent: enumerate baseline failures and independent truth labels, runtime-visible observations, fixed detector performance, temporal lead time, available actuator/control response, and whether incumbent guards already handled the fault.
- Distinguish observed failure / detectable / timely / actionable / incrementally improved. No oracle is a universal performance upper bound. Do not call a detection a rescue without a controlled continuation experiment.
- If full simulator/policy snapshot restoration fails, mark branching `NOT_SUPPORTED` and continue valid paired full rollouts.
- No new Haplous implementation unless the opportunity audit justifies an explicit execution mechanism.

**Exit:** headroom report, ranked first mechanism, and evidence-limited go/no-go for the VLA-specific track.

## Track B — CPU-only execution-contract experiment

**Question:** Can an independently specified execution contract correctly admit, refuse, revoke, or recover actions when capability, timing and authority constraints interact? First measure functional correctness, not physical performance or cross-OEM portability.

Implement a minimal pure deterministic evaluator with:
- `Contract`: required capabilities, max observation age, active mode, authority epoch, resource identity, permitted transitions.
- `MachineProfile`: verified supported modes/capabilities, sensor/timestamp semantics, authority availability and explicit unsupported behavior.
- `Observation`/`Command`: source timestamp with clock-domain assumptions, execution epoch, request ID and command deadline.
- `Decision`: `ADMIT`, `REFUSE`, `SUSPEND`, `TERMINATE` (and reason code), with explanation trace.
- An **independent truth-side predicate** and fixtures in `fixtures/track_b_scenarios.json` (38 cases with complete defaults and independent expected labels). Correctness judge MUST NOT use the candidate's own decision as truth. See `BASELINE_SPEC.md` for the required precedence and combined-mode constraints.

The fixture set includes nominal acceptance, multiple simultaneous faults with priority rules, stale observation, expired command, obsolete lease epoch, unsupported torque authority, separately verified AND authorized behavioral native-force alternative, conflicting authority, lease expiry/revocation during active execution, stale ACK after epoch change, clock-skew boundaries, missing sensor, and stale calibration. Add false-refusal control cases. Implement the explicitly limited G0 baseline from `BASELINE_SPEC.md` as a feature-ablation comparator, NOT as an industrial incumbent. Any competitive claim additionally requires a strong, independently specified G1 incumbent. If G1 is not implemented, report logical correctness and G0 coverage only, not industrial superiority.

**Exit:** deterministic tests, reason-code confusion matrix, false admissions/refusals, minimal event trace, and a report clearly distinguishing implementation tests from real robot verification.

## M0 — Minimal registry now (not Tier 3 hardening)

Implement schemas in `schemas/` as source of truth:
- `manifest.schema.json`: immutable description of what is being run, including hashes, clock semantics, scenario population and command line. For development only, the manifest may identify readable development seeds; do NOT place sealed holdout seeds in any manifest exposed to agents.
- `authorization-payload.schema.json`: immutable Tier 3 proposal, including exact candidate/incumbent/model/runner/scorer/analysis digests, allocation **policy** (not a candidate-chosen holdout ID), test/guardrail policy, resource budget, release policy, nonce and expiry. No mutable state field.
- `approval-envelope.schema.json`: signature referencing a digest of the immutable authorization, signer key ID, decision and expiry. Signing private key MUST be held outside all agent processes.
- `ledger-event.schema.json`: append-only state transition event with actor, timestamp, prior-event hash, evidence digest and monotonically increasing sequence number. A hash chain detects changes but does not prevent truncation; use externally backed append-only storage for production.

Use RFC 8785 canonical JSON for signed content and RFC 8032 Ed25519 from maintained libraries; no homegrown crypto. Reject duplicate JSON keys and unknown critical schema fields. For now implement valid draft/manifest/ledger operations and unit tests. The manifest must support contract-only CPU runs WITHOUT VLA checkpoints or simulator fields (see the conditional schema and examples). **Tier 3 execution MUST remain disabled by default.** The signer and verifier can be tested only with dummy keys until protected runner/security work is complete.

M0 tests: RFC 8785 canonicalization stability (using a conformant library), schema rejection **with `FormatChecker`**, strict duplicate-key rejection using `json.loads(..., object_pairs_hook=...)`, hash mismatch, ledger order, duplicate run ID, manifest mismatch, attempted key access (dummy), invalid signature, tampering with the signed decision, expired authorization, altered digest and replayed nonce. Signing message is precisely defined in `SIGNING_PROTOCOL.md`. There is no automatic promotion to Tier 3.

## Tier 2/Tier 3 timing and governance

- Before autonomous Tier 2: split candidate and environment into a narrow observation/action interface, then run native-vs-isolated incumbent equivalence on dev scenarios under matched seeds; detect altered simulated time, latency, action conversion, or stochasticity.
- Before first Tier 3: sealed scenario generator and non-overlapping allocation; candidate sandbox with no shared secret mounts or arbitrary egress; adversarial dummy-secret exercises; one-time human signature verified by independent runner; frozen deterministic decision rule and statistical plan; restricted aggregate-only reporting by default. Do not approve with incomplete gates.
- Human checkpoint 1 before **every** Tier 3 run; human checkpoint 2 for claim acceptance. Investigative release retires a set from pristine confirmation; adapted candidates need a fresh population.
- External claim needs independent methodological review and truthful labeling of real-world validation status.

## Outputs expected from Claude on each assignment

1. Exact SHA/reproduction command and actual machine/resource findings.
2. Files changed and why; test names/counts; precise failing test and root-cause hypothesis.
3. Run manifest and checksums; raw evidence path; deterministic metrics path.
4. Findings separated into measured facts, inferences, and unknowns.
5. Proposed next action with a concise stop/continue recommendation.

**Work discipline:** inspect before editing; quiet targeted tests after meaningful changes; full suite at baseline and final; no patch-and-rerun loops without root-cause diagnosis; preserve upstream benchmark semantics; do not overbuild. When blocked, return an exact unblock requirement.

## v1.1 executable handoffs

- **CPU assignment:** `CPU_ASSIGNMENT.md` — M0 + Track B together on any suitable CPU machine; run fixture and schema test suite first.
- **GPU assignment:** `GPU_ASSIGNMENT.md` — Campaign 0A stages 0–1 only, executed in a separate suitable GPU environment.
- **Package validation:** `python tools/validate_package.py && pytest -q`. The supplied tests validate schemas, fixtures and adversarial input checks, NOT a finished Track B execution engine, signing CLI, simulator reproduction, or Tier 3 gate.
- **Independent experimental control:** Use G0 strictly for unit-level mechanism isolation; no claim against competent industrial stacks without a documented G1 configuration.
