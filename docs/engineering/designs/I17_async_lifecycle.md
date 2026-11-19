# I17: asynchronous command lifecycle and authority lease

Issue #17 (runtime, Gate B). Implements ARCH-001 decisions 4 and 5 on top of the #16
execution boundary. Base `6a4de75`.

## Problem

Real controllers answer asynchronously. They can delay, duplicate, reorder or drop acks.
They can disconnect, and they share physical authority with the host. Before this change
the runtime treated each synchronous ack as final, and every envelope carried
`lease_epoch=0` with nothing enforcing it. The runtime needs one rule that decides when an
ack authorizes a command, and a defined path for losing authority.

## Types

`haplous/machine.py`:

- `AckStatus` gains `ACCEPTED_FOR_DISPATCH`, `EXECUTING`, `COMPLETED`, `CANCELED`,
  `UNKNOWN` and `FAULTED`. It already had `CONTROLLER_ACCEPTED` and `REJECTED`.
- `Ack(command_id, status, reason)` loses its `accepted` property. Whether an ack
  authorizes anything depends on the command's history, so a single ack cannot say.
- `MachineSession` gains `poll_acks() -> list[Ack]`. It drains the acks produced since
  the last call. `submit(envelope) -> Ack` still answers at once. On a disconnect the
  session answers `UNKNOWN` for its outstanding commands and for every submit made while
  disconnected.
- New `LeasedSession(MachineSession)` protocol, with `acquire_lease() -> int` and a
  `lease_epoch: int | None` property. Each grant returns an epoch greater than any the
  session granted before. `lease_epoch` is the epoch the session honours now, and it is
  `None` once authority is lost. The session rejects envelopes whose epoch is not the
  current one.

`haplous/capabilities.py`: `MachineCapabilities.leased_authority: bool = False`. With
`False`, the host owns authority outright and every envelope carries the constant epoch 0.

`haplous/lifecycle.py` (new, stdlib only): `CommandLedger`, `Verdict` (`ACCEPTED`,
`NOT_ACCEPTED`, `PROGRESS`, `IGNORED`) and `Judgement(command_id, verdict, reason)`.

## State machine (CommandLedger)

The ledger is built per execution with the granted epoch. `issued(envelope)` records each
command. `judge(ack, now_s, clock)` returns one judgement, applying these rules in order.
The first rule that matches decides.

| Rule | Verdict | Reason |
|---|---|---|
| The ledger never issued the id | IGNORED | `unknown_command` |
| The id was issued at or before the last disconnect | IGNORED | `post_disconnect` |
| An `UNKNOWN` ack closes every command issued so far (a disconnect) | | |
| The envelope's epoch is not the ledger's epoch | IGNORED | `stale_epoch` |
| The status does not move the command strictly forward | IGNORED | `duplicate` or `out_of_order` |
| The status is `UNKNOWN` | NOT_ACCEPTED | ack reason, else `unknown` |
| A newer command has already been accepted | IGNORED | `superseded` |
| `now_s` is past `valid_until_s`, or the clock differs from the envelope's | IGNORED | `late` |
| The command is closed (it expired) | IGNORED | `closed` |
| First `CONTROLLER_ACCEPTED`, `EXECUTING` or `COMPLETED` | ACCEPTED | |
| A later accepting status, or `ACCEPTED_FOR_DISPATCH` | PROGRESS | |
| `REJECTED`, `CANCELED` or `FAULTED` | NOT_ACCEPTED (closes) | ack reason |

The forward order is `ACCEPTED_FOR_DISPATCH` < `CONTROLLER_ACCEPTED` < `EXECUTING` < any
terminal status (`COMPLETED`, `REJECTED`, `CANCELED`, `FAULTED`, `UNKNOWN`). A
`COMPLETED` that arrives before its `CONTROLLER_ACCEPTED` counts as the acceptance. The
`CONTROLLER_ACCEPTED` that follows it is ignored as out of order.

`expire(now_s, clock)` closes every command still waiting for acceptance once its
validity horizon has passed. Each one yields `NOT_ACCEPTED` with reason `expired`. So an
ack that never arrives in time counts like a rejection.

The invariant is that an ACCEPTED judgement only ever goes to a command that meets all
of these conditions:

- It was issued under the current epoch.
- It is inside its validity horizon on its own clock.
- It was issued after the last disconnect.
- It is newer than every command accepted before.
- It was never rejected, failed or expired.

A command is authorized at most once. Clocks are never compared across domains.

Validity limits when an envelope may *take effect*. Once a command is accepted in time,
the machine holds it compliantly until a newer command supersedes it or until stop. This
is today's hold semantics, unchanged.

## Runtime policy (`HaplousRuntime._execute_live`)

- **Lease.** When `caps.leased_authority` is true, the runtime calls
  `session.acquire_lease()` right after the session opens. It records an
  `authority_granted {epoch}` evidence event and stamps every envelope with that epoch.
  Otherwise the epoch is 0, as before.
- **Revocation.** On every tick, before anything else that could actuate, the runtime
  checks `session.lease_epoch != epoch`. A mismatch takes the existing stop/abort path:
  `machine.stop()`, then evidence `authority_revoked {held_epoch, session_epoch}`, then
  outcome `ABORTED` with reason `authority_revoked`.
- **Acks are judged only on ticks that may authorize.** These are ticks with a fresh
  observation where a command will be submitted. On such a tick the runtime judges
  `poll_acks()` at the observation's `received_s` on its clock, then `expire()`, then
  submits. It judges the immediate ack from `submit` on the decision's own tick. On a
  stale, gap, dropout or recovery tick the acks wait in the session, so an acceptance
  can never restore authority from stale state.
- **Effect of each verdict.** ACCEPTED and NOT_ACCEPTED drive the existing rejection
  streak and `command_rejection` temporal hold exactly as the synchronous accept and
  reject did. Three consecutive NOT_ACCEPTED verdicts enter the hold. The hold budget
  bounds it. An ACCEPTED verdict during the hold resumes authority with
  `temporal_revalidation_resumed`. `UNKNOWN` and `expired` are both NOT_ACCEPTED.
  IGNORED records `ack_ignored {command_id, status, reason}`. PROGRESS changes nothing.

## Migration and parity

- **Legacy.** `LegacySession` (MuJoCo, external, benchmark wrappers) answers
  synchronously as before, returns `[]` from `poll_acks()`, and rejects any epoch other
  than 0 with `stale_lease_epoch`. Its machines do not declare `leased_authority`. For
  these machines the ledger sees exactly one ack per envelope at dispatch, so the
  verdicts equal the old accept and reject.
- **Other product sessions.** `examples/stub_machine_contract.py` and the
  `ClockedMachine` test session gain a one-line `poll_acks()`. Nothing else
  implemented `MachineSession`.
- **Parity.** `scripts/od1_parity.py --before 6a4de75 --out results/b2/parity.json`
  covers every insertion and slide HEB scenario × 4 systems on the standard
  MuJoCoMachine. `tests/test_b1_parity.py` stays green.
- **Scoring epoch.** No new epoch. HEB parity is identical, and no synchronous machine
  can reach a new code path.

## Tests

`tests/test_async_lifecycle.py` contains these:

- `AsyncFakeSession`, a leased session with scripted per-envelope ack plans. The plans
  inject delay, duplication and reordering. The session also takes a disconnect window,
  a revocation tick and a stale-state window.
- Runtime fault tests:
  - A late ack is ignored, the command expires, the runtime holds, and it resumes only
    on the first timely acceptance.
  - A duplicate rejection counts once.
  - A `CONTROLLER_ACCEPTED` after a `REJECTED` never resumes.
  - `COMPLETED` before `CONTROLLER_ACCEPTED` counts as one acceptance.
  - Revocation mid-motion calls stop, aborts with `authority_revoked`, and submits
    nothing more.
  - A disconnect yields `UNKNOWN`, which counts as not accepted. Acks held over the
    disconnect are ignored, and the runtime resumes only on a post-reconnect command.
  - An acceptance during stale state does not restore authority.
  - Stale-epoch envelopes are rejected by the fake and by `LegacySession`.
- Ledger unit tests, one per rule.
- A hypothesis property over random orderings of issue, ack (any status, any id, either
  clock), time advance with expiry, and epoch re-grant. It runs 3000 examples. Every
  ACCEPTED judgement is checked against an independent oracle for epoch, horizon,
  disconnect, supersession and closure.
- Each guard was shown red by disabling it in turn and green with it restored.

## Out of scope

- `cancel(command_id)`. No capability declares it yet, and expiry already closes
  commands on the Haplous side. The machine contract forbids starting an envelope past
  `valid_until_s`.
- Lease persistence across restarts. A new process gets a new epoch from the session.
- A real ROS 2 action server.
- Dispatch-time validity, meaning a decision made on an observation older than the
  machine's `state_max_age_s` while the intent's freshness limit is looser. Behaviour is
  unchanged here and left for a follow-up.
