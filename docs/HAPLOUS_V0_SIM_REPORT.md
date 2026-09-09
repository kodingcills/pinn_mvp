# Haplous v0-sim report

**Status:** HAPLOUS V0-SIM COMPLETE. Next evidence gate: first physical
machine (see Section 4).

**Config:** VALIDATED_LOOSE_V1 bundle (peg r 5 mm, clearance 0.5 mm, chamfer
1 mm, plate 10 mm, n=16 blind hole, dt 0.5 ms / 2000 Hz, canonical gains and
force profile, canonical spiral v_t 5 mm/s), observation decimation 10
(200 Hz), state_max_age 0.02 s, max_initial_offset 4.35 mm. All 12 benchmark
scenarios are deterministic (no randomness, no seeds).

---

## 1. What exists (Phase III)

A clean `haplous/` package built after the research freeze
(`research-rseries-complete`), separated from the validated research
substrate by a machine boundary:

- `haplous` core (842 LOC): `ExecutionIntent` with semantic `Requirement`s
  (hard/soft, typed kinds) — distinct from machine-owned `ControlParameters`;
  `MachineCapabilities` + `negotiate()` returning structured
  SUPPORTED / DEGRADED / REFUSED with reason codes; explicit `ExecutionState`
  table (IDLE, VALIDATING, READY, EXECUTING, CONSTRAINED, RECOVERING,
  DEGRADED, REFUSED, ABORTED, COMPLETE) with exhaustive legal transitions;
  constraint monitoring (process force, tilt, state freshness) with value /
  limit / margin / breached; first-class refusal before execution; recovery
  gated by a `RecoveryPolicy` (permitted conditions, bounded action budget,
  named action); temporal semantics (source/received stamps, age, freshness
  bounds, measurement-loss windows); append-only `ExecutionEvidence` with
  reconstructable feasibility decisions.
- `haplous/adapters` (167 LOC): `MuJoCoMachine` implementing the Machine
  protocol (`capabilities`, `observe`, `execute`, `stop`, `health`) by
  wrapping the validated pegsim `run_episode` substrate — the physics is
  unwritten.
- `haplous/behaviors` (126 LOC): the insertion behavior (first behavior);
  all task-specific knowledge lives here.
- Core is AST-guarded: no simulator imports, no insertion vocabulary
  (`tests/test_haplous_structure.py`).

## 2. Benchmark (Phase IV)

Driver: `scripts/measure_haplous_v0_sim.py` (one-shot, deterministic).
Raw rows: `results/haplous_v0_sim_rows.jsonl` (18: 12 mediated + 6 direct).
Per-scenario evidence: `results/haplous_v0_sim_evidence/*.json`.
Summary: `results/haplous_v0_sim_summary.json`.

Scenario matrix and semantic conformance (expected vs actual):

| Scenario | Expected | Actual | Pass |
|---|---|---|---|
| nominal | COMPLETE | COMPLETE | yes |
| recoverable_lateral_1mm | COMPLETE | COMPLETE | yes |
| recoverable_lateral_2mm | COMPLETE | COMPLETE | yes |
| geometry_variation_dc_plus10um | COMPLETE | COMPLETE | yes |
| unrecoverable_lateral_5mm | REFUSED | REFUSED | yes |
| constraint_violation_tight_force | ABORTED | ABORTED | yes |
| measurement_dropout_small | COMPLETE | COMPLETE | yes |
| measurement_dropout_large | ABORTED | ABORTED | yes |
| stale_observation | REFUSED | REFUSED | yes |
| unsupported_requirement_force | REFUSED | REFUSED | yes |
| deadline_unachievable | REFUSED | REFUSED | yes |
| missing_success_condition | REFUSED | REFUSED | yes |

Conformance: **12/12**.

### Metrics

- Physical task success (mediated): 4/6 COMPLETE — the other two are the
  *intended* refusal and abort. Direct baseline (raw `run_episode`, graded by
  ground truth): identical physics everywhere — nominal/1 mm/2 mm/dc+10 um
  seat; 5 mm fails; the tight-force scenario seats physically (the direct
  system has no notion of a 12 N envelope and would silently process outside
  the stated limit). Semantics bought **correct refusal and abort behavior,
  not different physics**.
- Bounded success: 4/6 (same; no constraint breach on the completing
  scenarios).
- Refusal recall: **5/5** (all infeasible requests refused before motion).
  Refusal precision: **5/5** (no false refusals).
- Constraint violations: 1 total (the intended tight-force scenario); max
  excess 0.076 N over its 12 N limit, recorded with time, value, limit.
- Temporal validity: 2 measurement-loss windows (the two dropout scenarios),
  each transitioned DEGRADED (small, recovered) or ABORTED (large,
  terminal); zero state-age violations elsewhere; zero deadline misses.
- Execution time: wall 3.2-6.2 s for completing scenarios (dominated by
  physics at ~850 steps/s); REFUSED scenarios cost ~30 us.
- Contact outcomes: peak |Fz| and max tilt recorded per scenario from the
  observation stream.

### Runtime overhead (Section 46)

- Feasibility-check latency: median 21 us, max 32 us.
- End-to-end overhead vs direct execution on the same scenario: within
  +-2 percent wall-clock (measurement noise); no material optimization
  warranted at v0.
- The 5 mm infeasible request: mediated refuses at 27 us; direct burns
  8.9 s wall to fail. Refusal is also cheaper.

### Engineering leverage (Section 48)

- core 842 LOC, behaviors 126, adapters 167.
- 12 scenarios are pure intent data: **0 core LOC changed per scenario
  variant**, 0 tuned parameters per scenario. New scenario classes require
  behavior-layer data, not runtime code.

## 3. Fault simulation (Phase V)

`tests/test_haplous_faults.py` (8 tests), per the hardware-contract fault
plan: state latency (refusal before motion), command rejection (ABORTED with
structured `machine_error_*` reason + stop issued), machine fault during
motion (ABORTED), limited control mode (refusal), stop request on fault,
adapter-level latency (50 ms channel vs 20 ms bound -> refusal),
packet loss small window (DEGRADED then COMPLETE) and large window (ABORTED)
on the real adapter. These establish **software behavior under modeled
interface faults**; they do not establish hardware performance.

Test status: full suite 244 passed, 1 xfailed.

## 4. Physical gate (Section 53)

HAPLOUS V0-SIM COMPLETE. The next proof genuinely requires hardware.

### Known v0-sim limitations (honest scope)

1. Monitoring is replay-based within `execute()`: the runtime enforces
   constraints over the observation stream the adapter returns, but cannot
   physically interrupt a `run_episode` call mid-motion. True mid-motion
   abort requires a streaming command interface (hardware adapter work).
2. The success condition's lateral term is evaluated against the COMMANDED
   datum (task semantics), not measured truth; the K6 ancestry (truth-
   triggered capture in the substrate) is inherited from the research
   lineage and documented in the freeze.
3. One adapter, one behavior, motion-level (not tick-level) runtime.

### Requirements for the machine campaign

- Supported controller interface: UR5e-class RTDE at 500 Hz. KNOWN BLOCKER
  (R04): the sampled coupled impedance is unstable at 500 Hz (spectral
  radius ~7.25); retune/replace the sampled loop before hardware, or
  contract the controller to the vendor's force-mode at its native rate.
- Required sensing: joint/pose state (FK), axial F/T at the tool. A UR5e
  F/T sensor is ~4 N accurate: the 1 N search cue and ~15 N seating force
  are measurable, sub-N chamfer cues are not; FK-depth-based triggers are
  mandatory (per the sim campaign's own finding).
- Touch-off/calibration: establish `z_flat` by contact at `f_search`
  (currently CAD-seeded in sim); this is on the sim-to-real critical path.
- Safety boundary: independent watchdog process, hard force limit, emergency
  stop regardless of commanded state; `f_yield` is a configuration
  invariant, not a clamp. pegsim has no safety layer; hardware must.
- Tooling: pinned fixture + gauge part; the product behavior for shifts
  beyond ~0.5 mm is salvage-and-latch with a published deviation vector
  (PHASE2.md).
- Benchmark: the 12-scenario matrix re-expressed as physical scenarios
  (nominal, offset family, stale/measurement-loss via network fault
  injection, unsupported requirements via capability stubs).
- Instrumentation: synchronized command/observation logging to reconstruct
  Haplous evidence on hardware (timestamps from the controller clock).
