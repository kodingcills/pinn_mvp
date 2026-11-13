# G1: competent configured incumbent (M4.1)

Status: implemented as `benchmarks/incumbent.py` (arm name `g1_incumbent`). Tests are in
`tests/test_g1_incumbent.py`. The structural truth-leak matrix in
`tests/test_negative_truth_leak.py` covers this arm.

## 1. Purpose

M4 asks whether Haplous adds value over a competent execution stack. BASELINE_SPEC says G0 is
only an ablation and that competitive claims need G1. G1 is the strongest incumbent this
repository can build honestly. It runs the same task behaviour under the same information,
task and physical authority as Haplous, behind the guards a competent integrator would
configure. If G1 matches or beats Haplous, M4.2 reports that result
(HAPLOUS_EVAL_PROGRAM_v2 section 10). G1 must not be weakened to produce a positive delta.
Any change that removes or loosens a guard needs a new design revision and has to state why.

## 2. Audit: is `competent_supervisor` a competent incumbent?

The audit covers `SupervisedSystem` in `benchmarks/heb/systems.py:182-277`. It was run on all
27 HEB scenarios at faedc63, using the driver behind the M4.1 report. The dispositions below
are measured.

| Guard | competent_supervisor | Evidence |
|---|---|---|
| Force ceiling | Yes. A breach stops at once. | `systems.py:243-250`. On `insert_force_limit_2n` it ABORTS with 1 violation. bare_behavior records 29370. |
| Tilt | Yes. | `systems.py:251-255`. No HEB scenario declares `max_tilt_deg`, so the guard never acts on HEB. |
| Deadline | Mid-run only. There is no pre-motion cycle-time check. | `systems.py:256-260`. On `slide_deadline_infeasible` it moves for 1 s, then ABORTS with a graded `deadline` violation. |
| Stale observation | One stale sample aborts. There is no hold. | `systems.py:238-242`. It ABORTS `insert_stale_mid_motion` and `slide_stale_mid_slide`. Haplous and bare complete both. |
| Dropout | One missing sample aborts. | `systems.py:230-236`. It ABORTS `insert_dropout_small`, a single 5 ms window, and `insert_dropout_large`. |
| Pre-start sensor readiness | None. | It opens a session on `insert_stale_persistent`, moves, then ABORTS on freshness. |
| Command rejection | None. The `session.apply` outcome is discarded. | `systems.py:225`. It completes only because the machine holds the last command. A rejection that never ends is never detected. |
| Machine fault | Diagnosed after the session ends. There is no `health()` check. | `systems.py:267-268`. |
| Recovery / retry | None. The declared `RecoveryPolicy` is ignored. | It ABORTS `slide_disturbance_recoverable_ceiling` on the first tap. That scenario is mesh-excluded (KNOWN_ISSUES). |
| Start-envelope (reach) check | None. | It runs `insert_offset_5mm_infeasible` to the 16.3 s horizon (`horizon_exhausted`). |
| Command-mode admission | None. `caps.command_modes` is never read. | `systems.py:198` reads caps only for the horizon. |

On information, it reads the declared `Scenario` fields directly (`scenario.max_force_n` and
the like) and never reads `caps` beyond the horizon. All of these are `DECLARED` fields, so
nothing leaks. It simply uses less than Haplous has.

**Verdict, peg insertion.** It is not a competent incumbent. It is a strict-abort watchdog.
It fails 2 dropout and 1 stale-motion scenario that a hold-capable stack completes. It has no
pre-motion checks (reach, readiness). It cannot see command rejection.

**Verdict, constrained slide.** It is not a competent incumbent, for the same structural
reasons. It also aborts `slide_stale_mid_slide`, and it moves before failing an impossible
deadline.

**M3.1 question.** It aborts on stale observation, on ceiling-with-recovery and on an
impossible deadline, where Haplous completes or refuses. Each case is judged below.

- **Stale observation and dropout.** This is a weakness, not a design choice. Production motion
  stacks use a timeout watchdog: the drive holds its last setpoint, or a safe stop is
  triggered, only after the signal has been absent for a configured time. No competent
  integrator trips on a single late or missing sample. The machine here also declares a
  compliant hold capability (`MachineCapabilities.max_hold_unobserved_s`, 0.5 s), which the
  supervisor ignores.
- **Ceiling with recovery.** This is a weakness *when the task declares a recovery
  permission*. Using the declared, budgeted retry is configuration, not invention. In every
  HEB case where a retry acts, the scenario is mesh-excluded. So this weakness carries no
  scientific weight in M4.
- **Impossible deadline.** Trying anyway is a defensible design. It is still weaker than a
  pre-motion cycle-time check, because the cell spends motion and contact on a request already
  known not to fit. The validated cycle time is a known quantity for any qualified production
  program. G1 checks it before motion.

`competent_supervisor` stays unchanged, because historical HES epochs depend on it. G1 is a
new arm.

## 3. Information: G1 sees exactly what Haplous sees

| Input | Haplous (`HaplousSystem`) | G1 |
|---|---|---|
| `ExecutionIntent` from `systems.build_intent` (requirements, recovery policy, success condition) | yes | yes, the same function and call |
| `machine.capabilities()` | yes | yes |
| `machine.observe()` before motion, then session observations | yes | yes |
| `machine.health()` per tick | yes | yes |
| `session.apply` outcome | yes | yes |
| behaviour's declared command contract | yes (OD-1) | yes |
| scenario evaluator-only fields | no | no (enforced by two checks, below) |

G1 reads every limit from `intent.requirements`, never from the `Scenario`. The `Scenario` is
passed only to `build_intent` and to the task's condition factory, the same path Haplous uses.

Two checks enforce the last row, and neither covers the other's case.

- **Dynamic detector** (`tests/test_negative_truth_leak.py`). Runs real episodes and walks every
  value that crosses the machine boundary (observations, health, command outcomes) and the
  `ExecutionIntent`, closures included, for canary values of evaluator-only fields. It matches
  values, not names. It does NOT see a system reading a `Scenario` field inside its own code:
  that crosses no boundary. It also misses transformed secrets (scaled or formatted into a string).
- **Static guard** (`tests/test_negative_static_leak.py`). Parses G1, `HaplousSystem`,
  `SupervisedSystem`, `BareSystem` (also the direct arm) and `PassThroughSystem`. In their code
  every use of `scenario` must be `scenario.<declared field>` or a bare argument to
  `build_intent`, `systems.build_intent`, `realization.condition_factory` or `super().run`.
  Any evaluator-only attribute read, `getattr`/`vars`, alias or other call fails. It is
  syntactic: it does not follow data through other objects such as `realization.*`.

## 4. Guards

| Guard id | Stage | Rule | Outcome / code |
|---|---|---|---|
| `command_mode` | pre-motion | every behaviour `required_command_modes` ∈ `caps.command_modes` by exact name. An undeclared contract is refused | REFUSED `controller_interface_unavailable` / `command_contract_undeclared` |
| `reach` | pre-motion | `INITIAL_OFFSET_M` ≤ `caps.max_initial_offset_m` | REFUSED `start_outside_validated_envelope` |
| `cycle_time` | pre-motion | `DEADLINE_S` ≥ `caps.expected_duration_s` | REFUSED `cycle_time_infeasible` |
| `sensor_ready` | pre-motion | `machine.observe()` present and age ≤ `STATE_MAX_AGE_S` | REFUSED `sensor_not_ready` |
| `machine_fault` | live | `health()["status"] != "ok"` → stop. A session fault is diagnosed | ABORTED `machine_unhealthy` / `machine_fault_<fault>` |
| `capability_loss` | live | a required mode disappears from `capabilities()` → stop | ABORTED `controller_interface_lost` |
| `dropout` | live | no sample: withhold new commands while the machine holds its last setpoint. More than `max_hold_unobserved_s / state_period_s` consecutive untrusted ticks → stop | ABORTED `watchdog_timeout` |
| `stale` | live | sample age > `STATE_MAX_AGE_S`: same hold and timeout as `dropout` | ABORTED `watchdog_timeout` |
| `force_ceiling` | live | \|Fz\| > `MAX_PROCESS_FORCE_N` → stop, unless `recovery` applies | ABORTED `force_limit` |
| `recovery` | live | on a ceiling trip with `process_force` permitted: restart the behaviour from the live pose (`behavior.begin`) and skip that tick's command. Each new trip (after the previous one cleared) consumes one retry, up to `max_recovery_actions`. An attempt still breached after `settle_s` → stop. No completion while an attempt is open | ABORTED `recovery_budget_exhausted` / `recovery_timeout` |
| `tilt` | live | tilt > `MAX_TILT_DEG` → stop | ABORTED `tilt_limit` |
| `deadline` | live | sample source time > `DEADLINE_S` → stop | ABORTED `deadline_exceeded` |
| `command_rejection` | live | the machine keeps the last accepted command. G1 resends each tick. More consecutive rejections than the hold budget → stop | ABORTED `command_rejection_timeout` |

Completion is the task's success condition, evaluated on fresh state only. A final check also
runs when the session ends.

### Command-mode admission against OD-1

The incumbent analogue is a controller manager that activates a controller only when every
command interface it claims is available. A missing interface fails activation. It does not
reject the machine for other tasks. G1 implements that as exact-name matching, with no
substitution path.

- **Same as OD-1.** It refuses before any session opens (tests check `open_session_calls == []`
  on the machine). It refuses an undeclared contract. It stops when a required interface is
  lost mid-run. It does not reject the machine.
- **Different from OD-1.** G1 does not admit a verified, task-authorized adapter (an OD-1
  rule 3 substitution). It refuses instead. On every HEB machine (no adapters) the decisions
  are identical. `test_g1_admission_matches_od1_without_adapters_and_refuses_adapters` checks
  all mode subsets differentially against `haplous.capabilities.command_mode_admission` and
  pins the adapter difference.

## 5. Why each choice is competent practice

- **Pre-motion checks (reach, cycle time, sensor readiness, controller interfaces).** Production
  cells validate the program before releasing motion: the part must be inside the qualified
  process window, the cycle time must fit, sensors must report healthy and the controller must
  be loaded. The thresholds come from the same machine capability record Haplous uses. The
  0.0035 m reach envelope was itself calibrated from measured capture
  (`benchmarks/heb/tasks.py:86-96`). A real integrator holds a qualification record of the same
  kind.
- **Watchdog with hold and timeout.** This is the standard shape of a communication or sensor
  watchdog. Brief loss holds the last safe setpoint, and loss beyond a configured timeout
  triggers a stop. The timeout is the machine's declared compliant-hold time.
- **Force/torque limit monitor with stop.** This is the standard configuration of an F/T limit
  monitor. A declared, budgeted back-off-and-retry is common in force-controlled assembly when
  the process spec permits it.
- **Command-acceptance monitoring.** A controller that silently ignores rejected setpoints is
  the failure mode that controller-error reporting exists to prevent.
- **ROS 2 `controller_manager`.** The program references it (fallback controllers, interface
  claiming). Its documentation could not be fetched in this session (host not allowed), so no
  specific parameter or default is claimed here. G1 follows only the general semantics stated
  above.

## 6. Disclosed asymmetries against Haplous

1. **No adapter substitution.** G1 refuses where OD-1 admits a verified, authorized adapter
   (section 4). This makes G1 stricter. No HEB machine has adapters.
2. **No sensor-ceiling clamp.** Haplous enforces a force limit above `max_sensed_force_n` at the
   ceiling (`haplous/constraints.py:52-63`). G1 enforces the declared limit. The only HEB case,
   `insert_force_limit_150n`, peaks at about 2 N, so the outcome is the same.
3. **Requirement-feasibility refusals not implemented.** These are missing sensing channel,
   tolerance below position resolution, and state-age bound below the state period
   (`haplous/capabilities.py:186-339`). No HEB scenario triggers them, so they cannot be tested
   here. M4.2 must not add scenarios that trigger them without first adding them to G1.
4. **No source-gap detection separate from age.** Haplous also flags a source-time gap between
   samples (`haplous/runtime.py:374-415`). G1 counts missing samples and stale ages. On HEB,
   gaps arise only from dropout ticks, which G1 sees as missing samples.
5. **Rejection hold entry.** Haplous enters a hold after 3 consecutive rejections
   (`runtime.py:658`). G1 counts every rejected tick toward the same budget.
6. **No evidence trail or structured refusal data.** G1 reports codes only. Evidence quality is
   not an outcome axis here, and M4.2 must not score it as a G1 failure without saying so.
7. **Shared mechanisms by design.** The hold budget, the recovery episode and the restart from
   the live pose rely on the same machine capability and behaviour hooks Haplous uses. Where
   Haplous and G1 coincide on temporal faults, that is the competent configuration. It is not
   evidence that Haplous adds value.
8. **Soft force floor.** Neither system stops on it. G1 does not monitor it at all. Haplous
   records a CONSTRAINED transition, which has no effect on the outcome.

## 7. Tests (each guard, with planted removal)

`G1IncumbentSystem(disabled=...)` removes one guard. Each test runs the guard and its removal
on the same scenario and asserts that the outcomes differ. That proves the test detects a
missing guard. Scenarios marked *derived* change a committed HEB scenario only where no
committed scenario exercises the guard.

| Guard | Scenario | G1 | Guard removed |
|---|---|---|---|
| reach | `insert_offset_5mm_infeasible` | REFUSED, 0 sessions | session opened |
| cycle_time | `slide_deadline_infeasible` | REFUSED, 0 sessions | session opened |
| sensor_ready | `insert_stale_persistent` | REFUSED, 0 sessions | session opened |
| force_ceiling | `insert_force_limit_2n` | ABORTED `force_limit` | more than 100 samples above 2 N |
| recovery | `slide_disturbance_recoverable_ceiling` (mesh-excluded, mechanism only) | COMPLETE | ABORTED `force_limit` |
| tilt | *derived* `insert_nominal` with `max_tilt_deg=5e-5` (measured peak 8.9e-5) | ABORTED `tilt_limit` | graded `tilt` violation |
| deadline | *derived* `insert_offset_2mm` with `deadline_s=2.0` | ABORTED `deadline_exceeded` | COMPLETE with graded `deadline` violation |
| stale | `insert_stale_mid_motion`, `slide_stale_mid_slide` | bounded COMPLETE | graded `state_freshness` violation |
| dropout | `insert_dropout_large`; *derived* 0.7 s dropout | bounded COMPLETE; `watchdog_timeout` | graded `state_freshness` violation |
| command_rejection | `insert_command_rejection_persistent`; *derived* 0.7 s window | COMPLETE; `command_rejection_timeout` | no timeout |
| machine_fault | `insert_machine_fault`; unhealthy-machine wrapper | `machine_fault_machine_fault`; `machine_unhealthy` | misreported `horizon_exhausted`; COMPLETE |
| command_mode | descent-only machine on `insert_nominal` and `slide_nominal` | REFUSED `controller_interface_unavailable`, 0 sessions, 0 stops | runs |
| capability_loss | *derived* machine losing CI after about 0.5 s | ABORTED `controller_interface_lost` | COMPLETE |

## 8. Scope limits

- Excluded from scientific comparison: `slide_disturbance_no_recovery` and
  `slide_disturbance_recoverable_ceiling`. Their outcomes depend on the 16-tile annulus mesh
  (KNOWN_ISSUES).
- `haplous/cli/commands.py` `SYSTEM_NAMES` does not list `g1_incumbent`. The CLI cannot select
  it until that list changes, which is outside M4.1's write scope. `evaluation/experiment.py`
  resolves arms through `system_by_name` and can use it.
- G1 is a simulated incumbent built from repository guards. It is not a verified configuration
  of any real product.
