# I22: machine hold semantics as a capability

Issue #22 (runtime, Gate C candidate mechanism B of
`docs/engineering/experiments/E19_stale_resume_dev.md`). Base `73db19d`.

## Problem

During a temporal hold the runtime withholds commands and assumes the machine "compliantly
holds its last accepted command". The MuJoCo machine does not. On a tick with no newly
accepted command it replays the last joint wrench open loop
(`haplous/adapters/mujoco_adapter.py:319-345`), so the peg coasts, tilts and, in
`m4s_stale_long_00116`, hits 296.7 N inside the stale window (`results/e19/diagnosis/`).
Real controllers differ the same way. Some servo the last setpoint, some replay the last
output, and some trip unless commanded every period. The runtime must not assume one of
these. The machine declares it, and the runtime picks its hold action from the declaration.

## Types

`haplous/capabilities.py`, one field and its enum:

- `HoldSemantics`: `CLOSED_LOOP_HOLD` (the machine servos its last accepted setpoint on live
  state with no further commands), `ZERO_ORDER_WRENCH` (it replays its last actuation output
  open loop), `REQUIRES_HEARTBEAT` (it faults or stops unless commanded every period),
  `UNDECLARED`.
- `MachineCapabilities.hold_semantics: HoldSemantics = HoldSemantics.UNDECLARED`.

What each value means, precisely:

- `CLOSED_LOOP_HOLD` means a hold **at the last accepted target**, not a hold in place. The
  servo keeps tracking that target on live state. If the controller lags its setpoint, the
  machine keeps moving until it settles there. #18 measured this on robosuite's
  joint-position controller: 13 mm end-effector drift over a 0.45 s hold, at kp = 50
  (`results/b4/joint_hold_stop.json` on the #18 branch). A machine that holds where it
  physically is, ignoring the last target, would be a different value. No machine here does
  that, so none is added.
- `ACTIVE_HOLD` re-asserts the **last accepted target**, unchanged. It does not re-anchor at
  the measured pose with zero velocity. Re-anchoring would be a new setpoint built from
  untrusted state, which is exactly what a temporal hold forbids. So on a lagging controller
  an active hold drifts to the last target too.
- `hold_semantics` is orthogonal to #18's `stop_response` (`ACTUATION_OFF` or
  `HOLD_POSITION`). `stop_response` says what `stop()` does. `hold_semantics` says what
  happens on a period with no new command while the session continues. The policy's STOP
  action calls `machine.stop()`, and what that does physically is `stop_response`'s to
  declare. The two fields sit side by side in `MachineCapabilities`. Expect a small textual
  merge with #18, with no shared concept.

`haplous/runtime.py`:

- `HoldAction`: `HOLD` (withhold commands), `ACTIVE_HOLD` (re-assert the last accepted
  setpoint every period), `STOP`.
- `HOLD_POLICY: Mapping[HoldSemantics, Mapping[str, HoldAction]]`, the table below.

`haplous/lifecycle.py`: `CommandLedger.newest_accepted`, the envelope of the newest accepted
command (or `None`). It is the single source for what "last accepted setpoint" means.

## Policy table

Hold causes are the four the runtime already has. Three mean the state is untrusted while
the machine still takes commands. One means the machine refuses commands while the state is
fresh.

| hold semantics | `observation_loss`, `freshness_gap`, `stale_state` | `command_rejection` |
|---|---|---|
| `closed_loop_hold` | HOLD | HOLD |
| `zero_order_wrench` | ACTIVE_HOLD | STOP |
| `requires_heartbeat` | ACTIVE_HOLD | STOP |
| `undeclared` | STOP | STOP |

Reasons, row by row:

- A closed-loop machine holds safely by itself, so the runtime withholds commands, exactly as
  before #22.
- A zero-order machine coasts when nothing is commanded, and a heartbeat machine trips. Both
  stay safe while the runtime re-asserts the setpoint it last accepted. A re-assertion is no
  new decision on untrusted state. The payload is the accepted one, unchanged. When commands
  are rejected nothing can be re-asserted, and M4 shows that a supervised open-loop hold
  still exceeds the force limit (57 of 60 temporal-stratum exceedances fall inside the fault
  window, including rejection windows). So the runtime stops.
- An undeclared machine gets no assumption about what it does when it is not commanded, open
  loop or otherwise. Every hold request stops it. This is the conservative default.

The action is chosen when a hold cause fires. A STOP cause stops even when a hold with
another cause is already active, for example a rejected re-assertion during an active stale
hold. The hold budget (`max_hold_unobserved_s`) bounds HOLD and ACTIVE_HOLD exactly as
before.

## Runtime mechanics

- **Hold entry.** One nested `enter_hold(cause, t_s, **data)` replaces the four inline
  entries. It records `temporal_hold_entered {cause, action, hold_semantics, ...}`, so the
  evidence says which action was taken and why. On STOP it calls `machine.stop()` and ends
  ABORTED with reason `hold_unsupported_by_machine` (payload: cause and semantics).
  `observation_loss` now sets `hold_cause` too. Before, it left it `None`, which behaved the
  same on every check.
- **Active hold.** On a tick where the runtime withholds the behaviour's command while
  holding, it submits a new `ActionEnvelope` through the normal path: next `command_id`,
  current epoch, `ledger.issued`, `session.submit`, the immediate ack judged by the ledger
  and settled. The payload is `ledger.newest_accepted.payload`. The origin is the tick's
  time: the observation's source time, or on a dropout tick
  `last_source + untrusted_ticks * period`. The validity horizon is origin plus
  `state_max_age_s`. Nothing is re-asserted before the first acceptance. A rejected
  re-assertion counts toward the rejection streak, so persistent rejection reaches the
  `command_rejection` cause and its STOP.
- **No resumption from stale state.** The accepted-after-rejection resume in `settle` now
  requires `hold_cause == "command_rejection"` instead of `rejected_streak > 0`. Before #22
  `settle` ran only on fresh ticks, where any other hold had already resumed, so the two
  conditions agreed. With re-assertions, `settle` also runs on stale ticks, and an accepted
  re-assertion must never resume authority.
- **Evidence.** The feasibility record's capability snapshot gains `hold_semantics`.

## Migration

- `MuJoCoMachine` declares `ZERO_ORDER_WRENCH`. That is its real behaviour. Its physics is
  unchanged.
- `std_caps()` in `tests/test_haplous_semantics.py` (used by `ScriptedMachine`, the async
  lifecycle fakes and the evidence-store tests) declares `CLOSED_LOOP_HOLD`, the compliant
  hold those fakes model. Their hold tests are unchanged. The other test fakes stay
  undeclared, and none of their tests reaches a hold.
- `tests/test_haplous_faults.py`: the persistent-rejection test on the MuJoCo machine now
  expects the STOP (`hold_unsupported_by_machine`). The stale-hold test on the same machine
  still completes, now through an active hold.
- `tests/test_b1_parity.py` runs its four trials on `PreI22AssumptionMachine` (below), which
  reproduces the pre-#16 digests exactly.
- `examples/stub_machine_contract.py` and the external adapters (`benchmarks/external/`)
  stay `UNDECLARED`. The robosuite and ManiSkill adapters repeat the last delta action when
  nothing is commanded, which is open loop, so the conservative default fits them. #18 adds a
  robosuite joint adapter in parallel. Its owner declares that adapter's semantics.
- Benchmark side, not a Haplous feature: `benchmarks/heb/hold_realizations.py`
  `ClosedLoopHoldMachine` re-applies the last accepted command on withheld ticks (the E19 step
  1 probe) and declares `CLOSED_LOOP_HOLD`. This is the E19 `closed_loop_hold` realization.
  `PreI22AssumptionMachine` is the standard zero-order machine declared `CLOSED_LOOP_HOLD`,
  so the runtime takes the pre-#22 plain-hold path on unchanged physics. It serves the
  parity checks and the E19 `haplous_mediated` arm.
- The G1 incumbent is unchanged. `G1IncumbentSystem(hold_semantics_aware=True)` (arm
  `g1_parity`) gives G1 the same table: it re-applies its last accepted command on untrusted
  ticks of a zero-order or heartbeat machine, stops on a rejected command there, and stops
  on any hold of an undeclared machine. It does not import the runtime.

## Parity

- Behaviour where it should not change: a `CLOSED_LOOP_HOLD` machine takes exactly the
  pre-#22 path. Every existing runtime-semantics test passes unchanged against the fakes that
  declare it, and `tests/test_hold_semantics.py` checks that no command is applied during
  its stale and dropout holds. On real physics, `tests/test_b1_parity.py` reproduces the
  committed pre-#16 digests (rejection, dropout and stale-slide holds) on
  `PreI22AssumptionMachine`.
- HEB (`scripts/od1_parity.py --before 73db19d --out results/b5/parity.json`). Only
  `haplous_mediated` trials with a temporal hold on the MuJoCo machine may change. The parity
  report lists them. Every other trial must be identical.
- E19 `haplous_mediated` (Haplous on a machine that declares `CLOSED_LOOP_HOLD`, the pre-#22
  assumption) is checked trial by trial against `73db19d` (`scripts/e19_dev.py
  parity-mediated`).

## Scoring epoch and impact

Haplous-arm outcomes on the MuJoCo machine change wherever a temporal hold occurs.
`results/b5/parity.json` (`73db19d` to `60870d2`, 4 systems × 27 scenarios) has 102 of 108
trials identical. The 6 that differ are all `haplous_mediated` hold trials:

| Trial | Before | After |
|---|---|---|
| `insert_command_rejection_persistent` | COMPLETE, bounded | ABORTED `hold_unsupported_by_machine` |
| `slide_command_rejection_persistent` | COMPLETE, bounded | ABORTED `hold_unsupported_by_machine` |
| `insert_stale_mid_motion` | COMPLETE, bounded | COMPLETE, not bounded (`state_freshness`, re-assertion graded as stale actuation) |
| `slide_stale_mid_slide` | COMPLETE, bounded | COMPLETE, not bounded (`state_freshness`) |
| `insert_dropout_large` | COMPLETE, bounded | COMPLETE, not bounded (`state_freshness`, source gap actuated) |
| `insert_dropout_small` | COMPLETE, bounded | COMPLETE, bounded (applied commands and observations differ) |

So on HEB, Haplous loses bounded success on 5 of the 27 scenarios (it stops in 2, and in 1 stale insertion, 1 stale slide and 1 large dropout the re-assertion grades as `state_freshness`). New
scoring epoch `hes-i22-hold`, never pooled with `hes-m4-e3-simdiv` or earlier. HEB grading
(`HEB_VERSION` 0.3.0) is unchanged, because the system under test changed, not the grader.
The impact note is the HEB parity report plus the E19 results. Sealed M4 evidence is not
rewritten.

## Grading interaction (found while testing, before any E19 row)

HEB 0.3.0 grades a stale span as a `state_freshness` hard violation (`stale_span_actuated`)
when the machine records any command applied inside it (`benchmarks/heb/metrics.py`
`grade_requirements`). An active-hold re-assertion is such a command, so on
`m4s_stale_long_00116` both hold-aware arms (Haplous and `g1_parity`) remove the 296.7 N
exceedance (peak 2.41 N, the probe's physics exactly) and complete, yet grade as not bounded.
The benchmark-side `ClosedLoopHoldMachine` re-arms internally and records nothing, so the
same physics grades bounded there. This is a real trilemma for a zero-order machine under
this freshness rule: coast (force exceedance), re-assert (freshness violation) or stop (lost
success). The grader is not changed here. E19 is graded under HEB 0.3.0 as pre-registered,
and the E19 binding amendment declares a descriptive sensitivity before the run.

## Tests (red then green)

`tests/test_hold_semantics.py`, one case per semantics × cause class, plus:

- negative: an undeclared machine never withholds commands across a hold (it stops at hold
  entry, for every cause);
- an active hold re-asserts the last accepted payload once per withheld tick, through the
  ledger (increasing ids, current epoch);
- an accepted re-assertion never resumes authority from stale state;
- a rejected re-assertion escalates to STOP on a zero-order machine;
- the MuJoCo machine declares `ZERO_ORDER_WRENCH`, and `ClosedLoopHoldMachine` reproduces the
  step-1 probe on `m4s_stale_long_00116` (COMPLETE, peak 2.41 N);
- on that case, Haplous and `g1_parity` on the zero-order machine both reach the probe's peak
  and are graded `state_freshness` (the interaction above); `g1_parity` stops on rejection
  and `g1_incumbent` does not.
