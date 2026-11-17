# ARCH-001: execution boundary, packaging, and migration (Gates A and B)

Status: accepted by the coordinator 2026-11-17 for implementation. Short by design; each issue
refines its own part. Supersedes nothing in `docs/eval_program/` (historical, kept).

## Trace (what the code does today, verified at `a8541bf`)

- `haplous` core already imports nothing outside the stdlib (`mujoco`, `numpy`, `rerun`,
  `fastapi`, `hes`, `benchmarks`, `pegsim` all absent from `sys.modules` after importing
  `haplous`, `haplous.runtime`, `haplous.capabilities`, `haplous.machine`). The packaging
  problem is the distribution, not the imports: `pyproject.toml` is named `pegsim` and makes
  `mujoco` + `numpy` core dependencies.
- `MachineSession` (`haplous/machine.py:80`) has `observe/apply/step/stop/ended/fault`. The runtime
  loop calls `session.step()` every tick: Haplous core owns world advancement. On a real machine
  there is nothing to "step"; time passes and the controller runs.
- `ServoCommand` is one shape (target pose + axial stiffness); `CommandOutcome` is a synchronous
  accepted/rejected. No command identity, lease, validity horizon, or asynchronous
  acknowledgement exists.
- `ExternalMachineAdapter.health()` (`haplous/adapters/external.py:108`) returns
  `ExternalTask.ground_truth()`. The runtime only reads `health["status"]` (`runtime.py:612`),
  so no score has been affected yet, but grader truth crosses the runtime boundary. P0.
- `studio/jobs.py:89` checks worker liveness with `/proc/<pid>` (Linux only, no identity check).

## Decisions

1. **Distribution.** The repository builds one distribution named `haplous` with **no core
   dependencies**. Extras: `sim` (mujoco, numpy: `pegsim`, HEB MuJoCo backend), `eval`
   (+ registry, replay deps), `studio`, `dev`, `all`. Packages stay in the monorepo; modules that
   need an extra fail with a clear message naming the extra. `haplous doctor` reports per-extra
   status. Built wheel tested in fresh venvs: bare (`import haplous`, stub-machine contract run,
   `haplous --help`, `haplous doctor`), `[sim]` (one MuJoCo episode), `[studio]` (server serves
   a run). No public publishing.
2. **World stepping leaves the core.** `MachineSession.step()` is removed from the product
   protocol. The runtime is driven by a `TickSource` supplied by the integration:
   - `SimTickSource(session)` advances the simulator one period per tick (adapter-owned);
   - `RealtimeTickSource(period_s, clock=time.monotonic)` waits for the next period.
   The runtime decides once per tick on fresh observations; it never assumes that issuing a
   command advanced the world.
3. **Action envelopes.** Commands reach the machine only as
   `ActionEnvelope(command_id, lease_epoch, origin_source_s, clock, valid_until_s, payload,
   correlation_id)`. Payloads are typed per command surface:
   `CartesianImpedanceTarget(target_pose_m, k_z_n_per_m)` (today's `ServoCommand`) and a second
   surface chosen in B3 (candidate: `JointPositionTarget`/trajectory chunk). A machine rejects a
   payload type it does not declare in its capability profile; no silent semantic substitution
   (OD-1 rules carry over unchanged).
4. **Asynchronous lifecycle.** `session.submit(envelope) -> Ack`; later acks via
   `session.poll_acks()`. Statuses: `ACCEPTED_FOR_DISPATCH`, `CONTROLLER_ACCEPTED`, `EXECUTING`,
   `COMPLETED`, `REJECTED`, `CANCELED`, `UNKNOWN`, `FAULTED`. Duplicate, out-of-order, late (after
   `valid_until_s`) and post-disconnect acks never authorize new actuation; `UNKNOWN` is treated
   as not-accepted for revalidation purposes.
5. **Authority.** The session grants a lease with a monotonically increasing epoch. Envelopes
   with a stale epoch are rejected by the session; revocation forces the existing stop/abort path
   with an evidence event. Haplous integrates with host ownership (ROS/OEM); it does not claim to
   replace it.
6. **Clocks.** `ClockDomain` in {`sim`, `controller`, `producer`, `monotonic`, `wall`} on
   stamps and envelopes. Evidence records decision-to-dispatch (host monotonic) separately from
   request-to-effect (machine/sim clock).
7. **Truth boundary.** `Machine.health()` returns machine health only (`status` + diagnostics the
   machine itself observes). Grader truth stays in the evaluator. A static test forbids
   `ground_truth`/`task_result` flowing into any `haplous/` runtime path.

## Migration and parity (AGENTS.md behaviour-freeze, as amended in this PR)

- A `LegacySession` adapter wraps today's sessions: `submit` with a `CartesianImpedanceTarget`
  calls `apply`, returns a synchronous `CONTROLLER_ACCEPTED`/`REJECTED` ack; its `step()` becomes
  the `SimTickSource`. Existing machines (MuJoCo, external) keep working unmodified during
  migration.
- Parity bar for B1 (as in OD-1): every insertion and slide HEB scenario × every system in
  `benchmarks.heb.runner.evaluate` gives identical disposition, reason, refusal codes, graded
  outcome and observations before/after; both committed recordings pass the M2 gate; HES outputs
  unchanged. Any scoring change opens a new scoring epoch with an impact note.

## Ownership

| Area | Owner role | Files |
|---|---|---|
| Packaging, doctor, Studio jobs | product integration | `pyproject.toml`, `haplous/cli/`, `studio/` |
| Envelope, lifecycle, lease, tick sources | runtime | `haplous/machine.py`, `runtime.py`, `capabilities.py`, `adapters/` |
| Truth boundary, scenario hardening | evaluation | `haplous/adapters/external.py` (health only), `tests/test_negative_*` |
