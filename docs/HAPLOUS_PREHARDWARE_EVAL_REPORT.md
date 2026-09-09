# Haplous Pre-Hardware Evaluation Report (v0.5-sim)

System tag: `haplous-prehardware-eval-v0` (commit `5ae48fb`, tagged after the
final code change and before the held-out evaluation ran). Research lineage
frozen separately at `research-rseries-complete`; untouched by this
campaign.

Status: SOFTWARE / SIMULATION EVIDENCE ONLY. Nothing in this report
constitutes a hardware, safety, or production claim.

---

## 1. What this release is

Haplous v0.5-sim is a physical-execution runtime layer: intents with typed
hard/soft requirements, capability negotiation with structured refusals, a
live supervision loop (observe -> validate constraints -> decide -> command
-> step), episode-based recovery authorization, degradation, refusal,
abort, and append-only reconstructable evidence — all defined against a
Machine protocol that supports true mid-motion intervention.

Before this campaign, monitoring was replay-only: `Machine.execute()` ran
the entire episode and the runtime replayed its observation stream
afterward. S01 replaced that with stepping sessions; task logic moved from
the substrate into behavior classes; the runtime owns only admissibility
and lifecycle semantics.

## 2. Architecture

| Layer | Contents | LOC (non-blank, non-comment) |
|---|---|---|
| `haplous/` core (machine-free) | intent, capabilities, constraints, behavior protocol, evidence, state machine, runtime | 1,143 |
| `haplous/adapters/` | MuJoCo stepping-session machine (validated servo, corruption/fault schedules) | 302 |
| `haplous/behaviors/` | insertion + constrained-slide task logic | 411 |
| `benchmarks/heb/` | HEB 0.1.0: protocol, registry, systems, grading, runner, two task families | 1,173 |

Public API: 25 exported symbols (`haplous/__init__.py`), AST-guarded
simulator-free core (`tests/test_haplous_structure.py`), HEB independence
guards (`tests/test_heb_structure.py`).

## 3. Benchmark version

HEB 0.1.0. Grading is benchmark-owned: hard requirements are re-evaluated
over the full observation stream and task truth is computed from
observation geometry (insertion: settled-at-depth with lateral containment
in the true bore; slide: endpoint + declared force band over the
persistence window). No grader reads Haplous's own success decision.

## 4. Task families

- **Task A — peg-in-bore insertion** (18 development scenarios): offsets to
  2 mm, semantic infeasibility, force-limit negotiation vs violation,
  measurement dropout, stale delivery, command rejection (transient and
  persistent), machine fault, geometry variants (facet count, tighter
  clearance, deeper plate).
- **Task B — constrained-contact slide** (10 development scenarios): a
  1 mm-radius, 2 g part slides a tangential arc on the fixture annulus
  maintaining a declared force band; vertical-pop and facet-tap
  disturbances, tilt-guard kicks, stale/rejection/fault schedules,
  deadline refusal.

## 5. Simulator backend

MuJoCo only (the validated pegsim substrate behind the adapter). **The
second-backend node (S10/S11) was NOT completed** — see "Known gaps".

## 6. Systems compared

- **B0** `direct_minimal`: behavior over the machine session, no
  completion evaluation (runs to horizon).
- **B1** `bare_behavior`: same behavior factory and the same task success
  condition as B2, driven directly over the live session — no
  negotiation, no constraint monitoring, no recovery authorization.
- **B2** `haplous_mediated`: the same behavior factory behind the Haplous
  runtime.

Every scenario runs on one shared machine realization (same physics,
corruption schedule, fault schedule, backend) for all systems.

## 7. Perturbation envelope (development sweeps, `results/heb_sweeps_dev.json`)

Measured failure boundaries (paired B1/B2, deterministic physics):

| Axis | Boundary (mediated = bare unless noted) |
|---|---|
| initial offset | capture passes to 3.0 mm, fails at 3.5 mm |
| clearance delta | mechanism envelope [−0.4, +0.0] mm: looser holes fail BOTH systems (commit-gate constants are validated only at canonical geometry) |
| facet count | n=8 fails both; n≥16 converges |
| stale delivery | mediated enforces the 20 ms state-age bound strictly (aborts ≥20 ms); bare ignores staleness (graded unbounded) |
| dropout | mediated aborts ≥20 ms blackouts; bare continues (delivered-stream ages grade clean — the semantic difference is measured) |
| slide kick | tilt-guard boundary between 0.3 and 0.4 m/s lateral impulse |
| slide stale | same 20 ms boundary as insertion |

## 8. Held-out protocol

Systems, benchmark, baselines, and metrics frozen at tag
`haplous-prehardware-eval-v0`. The final driver
(`scripts/measure_heb_final.py`, committed after the tag) drew a fresh OS
seed (9975048060650219579) and generated 38 held-out scenarios
(sha256 `3eb33ab5…`), 76 paired trials. Deviation from a fully blind
protocol (single-operator campaign) is documented in the script header:
the seed is recorded at generation time, after the freeze; the evaluated
code never reads the manifest.

## 9. Sample-size rationale

Physics is deterministic; variance comes from scenario mix, not
repetition. 38 cells cover both families' pass regions plus semantic and
fault cells; 27 paired COMPLETE trials give the wall-time ratio a tight
empirical distribution. Zero-failure claims use the rule-of-three bound
(3/N) under deterministic-repetition assumptions, never as a physical
reliability probability.

## 10. Primary results (held-out, 38 scenarios)

| Metric | B2 Haplous | B1 bare |
|---|---|---|
| coverage | 0.84 | 1.00 |
| bounded success (among accepted) | **0.844** | 0.816 |
| hard-violation trials (accepted, completed) | **0** | 5 |
| refusal precision / recall | **1.0 / 1.0** | — / 0.0 |
| recovery success (designated) | 1.0 | 1.0 |
| completion P50 / P95 | 2.00 s / 4.16 s | (same order) |
| wall-time ratio on paired completes | 0.81 (mediated/bare, median) |

Development evaluation (27 scenarios): mediated 27/27 semantic
conformance; bare 16/27; direct_minimal 10/27.

## 11. Operating envelope (HEB simulation results, not hardware limits)

- Insertion: offsets ≤ 3.0 mm; clearance 0.1–0.5 mm (mechanism envelope);
  n_segments ≥ 16; stale tolerance exactly 20 ms; blackout tolerance 20 ms.
- Slide: corridors at r ∈ [3.55, 4.25] mm verified for ≤ 80° arcs except
  the held-out `heldout_slide_07` failure near corridor-minimum radius
  with a long arc (tilt guard, both systems — a genuine mechanism
  boundary found by the held-out evaluation).
- Facet-tap force excursions 1.73–1.74 N (3 per 60° arc); establishment
  tap 1.74 N; kick response 0.47 N.

## 12. Failure taxonomy (held-out, mediated)

27 COMPLETE; 6 REFUSED (negotiation: unreachable goal, unsensed limits,
infeasible deadlines); 3 ABORTED state_freshness; 1 ABORTED
command_rejection_persistent; 1 ABORTED machine_fault_tilt_guard. No
trial collapsed into an unexplained FAILED.

## 13. Fault / recovery results

Recovery is episode-based: one budgeted re-reference per breach episode,
RECOVERING held while the condition persists, closure when the constraint
clears, `recovery_timeout` for failed attempts. Recovery permission flips
the disposition on identical physics
(`slide_disturbance_recoverable_ceiling` COMPLETE vs
`slide_disturbance_no_recovery` ABORTED). Modeled fault timing: detection
0–1 supervision tick, stop latency 0 ticks (logical timing only).

## 14. Reliability results

10,000 runtime cycles (no leaks, complete evidence), 2,000 aborted cycles
(lineage preserved), 100 repeated adapter sessions (bit-identical streams,
deterministic), 500 randomized fuzz executions + 400 targeted property
cases (refusal/permission invariants). All in
`tests/test_haplous_reliability.py`.

## 15. Runtime overhead

Mediated wall-time on paired completes is 0.81x bare (median, 27 pairs) —
Haplous early-exits at task completion where the replay-era substrate ran
to its ramp end; per-tick supervision adds no measurable wall cost in this
deterministic setting. Feasibility latency ~22 µs median (v0-sim).

## 16. Cross-simulator results

Not performed (see Known gaps).

## 17. Engineering leverage

- Adding Task B: **core delta +177/−59 LOC**, of which +132/−58 is
  recovery-episode runtime hardening (task-agnostic); the slide-specific
  core delta is the generic `MIN_PROCESS_FORCE_N` requirement (+45 LOC
  across intent/constraints/capabilities). No task-specific branches in
  core (AST-guarded).
- Task B behavior: 174 LOC; HEB family + registry: 297 LOC.
- Scenario variants: 0 core LOC per variant (scenarios are data).
- Second backend: not measured (not performed).

## 18. Clean-room usability

Not performed (see Known gaps). The quickstart example
(`examples/heb_quickstart.py`) runs the full path in ~2 s and documents
the public-API flow.

## 19. Negative results

- **The bare baseline beats Haplous on bounded success in the development
  set** (0.72 vs 0.64 on Task A's 18 scenarios): Haplous's conservative
  timing semantics (abort on blackouts/staleness the bare system ignores)
  costs completions while preventing violations. On the held-out set the
  ordering reverses (0.844 vs 0.816) with zero mediated violations. Both
  results are reported as measured; neither was tuned against.
- **Looser holes fail the insertion mechanism in both systems**: the
  validated bundle's derived constants (commit gate, settling depths) do
  not re-derive off canonical geometry — the report's clearest
  demonstration that substrate constants, not runtime semantics, bound
  the mechanism.
- **Long-arc slides near corridor-minimum radius hit the tilt guard**
  (held-out, both systems).

## 20. Known gaps (honest)

- **No second simulator backend** (S10/S11 deferred with no integration):
  the Machine protocol, the HEB registry, and the session contract are the
  preparation, but MuJoCo-independence is UNMEASURED. This is the largest
  open software claim.
- **Clean-room developer proxy** not run.
- **No compound-stress scenarios** beyond the development fault set; the
  perturbation framework supports them but sweeps were single-axis.
- **Held-out adjudication labels 4 expectation errors post-hoc**:
  documented in `results/heb_final_adjudication.json`; the mediated
  behavior in those cells is correct per the declared bounds, but the
  protocol deviation (single operator, seed recorded at generation time)
  is real and disclosed.

## 21. What this evidence supports

- Haplous provides live interruptible execution with measured 0–1-tick
  fault detection and 0-tick stop latency in modeled logical timing.
- The abstraction survived a second task family with a small, generic
  core delta and zero task-specific core branches.
- Mediated execution refuses infeasible requests perfectly and never
  violates declared hard bounds where the bare baseline does, at equal
  task success within the validated envelope.
- The evaluation stack (scenarios, corruption schedules, graders) is
  independent of Haplous internals and reusable for future systems.

## 22. What this evidence does NOT support

- Any hardware, safety, timing, or sensor claim (all timing figures are
  modeled logical ticks).
- MuJoCo independence (no second backend ran).
- Real-world reliability of any magnitude (deterministic simulation grid;
  rule-of-three bounds only).
- Physical operating envelopes (the boundaries above are simulation
  boundaries of one validated substrate).

## 23. Exact remaining hardware questions

1. Does the 500 Hz sampled-loop instability (R04) appear on real RTDE
   control, and at what retuned gains does the coupled mode stabilize?
2. Is the 20 ms state-age bound achievable with real state streams and
   jitter, or must the freshness contract be renegotiated per OEM?
3. Do the modeled faults (dropout, stale delivery, command rejection,
   fault-at-time) correspond to real F/T-sensor dropouts, network jitter,
   and controller rejections at plausible rates?
4. Does the facet-tap force signature (1.74 N spikes) exist on a physical
   fixture, and does the 10° tilt guard trip there?
5. What is the physical establishment-tap magnitude vs the modeled
   contact solver's?

## 24. Next evidence gate

**PHYSICAL HARDWARE** — per the frozen R-series requirements (500 Hz
sampled-loop instability, ~4 N F/T accuracy, touch-off calibration,
watchdog, evidence instrumentation).
