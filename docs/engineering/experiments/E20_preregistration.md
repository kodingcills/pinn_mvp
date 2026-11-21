# E20 pre-registration: incremental integration effort, Haplous vs a reusable G1

Status: **FROZEN** at the commit that adds this file; no arm has started. Gate C, epic #11.
Feasibility: `E20_feasibility.md` (gate PASS).

## 1. Question

Does the existing Haplous runtime reduce the incremental work needed to implement equivalent
execution semantics on a new robot model and command surface, compared with an independently
reusable supervisor (G1 extracted into a library)? Upfront costs are recorded separately for both;
Haplous's historical development cost cannot be reconstructed and is not claimed.

A win provisionally supports T03 (bounded machine-specific adaptation). It does not establish
real-hardware portability or commercial value. The result is internal evidence until a real
robotics developer reviews the workflow and the effort accounting.

## 2. Frozen inputs (both arms identical; neither may modify them)

| Item | Value |
|---|---|
| Task | robosuite 1.5.2 Lift, UR5e, `JOINT_VELOCITY` controller, 20 Hz |
| Dependency | robosuite 1.5.2 + `patches/robosuite-1.5.2-joint-velocity.patch` (sha256 prefix `4879288126b228d2`), enforced by `assert_patched_robosuite()` |
| Machine harness | `benchmarks/e20_shared/driver.py` (`c623c1ef98f307dc`): neutral vendor-style driver with asynchronous acks, exclusive control token, latched stop, fault injection |
| Task policy | `benchmarks/e20_shared/lift_policy.py` (`cb9398e8a25359e9`), 9/10 nominal |
| Success predicate on observed state | `acceptance.lifted()` (cube z > 0.84 m); the grader stays harness-only |
| Acceptance suite | `benchmarks/e20_shared/acceptance.py` (`18eca2710b36f0d8`), runner `scripts/e20_acceptance.py` (`8fa86e7f64f57964`) |
| Seeds | nominal 0–7 and 9; faults on seed 0 |

Acceptance cases (hard gate, all 15 must pass), graded from the driver's own logs:
A1 nominal ×9 (COMPLETE with grader success and plant commands identical to the unsupervised
reference: no interference), A2 refusal before any command when the machine lacks joint velocity,
A3 no motion commands on stale state, A4 persistent late acknowledgements never authorize further
motion and end ABORTED, A5 duplicate acks change nothing, A6 disconnect and A7 authority
revocation each end ABORTED with `stop()` within 0.25 s and no motion commands afterwards. Every
record must carry time-ordered evidence events and a reason for any non-COMPLETE outcome; command
validity horizons may not exceed 0.20 s. Negative control: the unsupervised reference passes A1 and
A5 and fails A2, A3, A4, A6 and A7 (`results/e20/negative_control_direct.json`).

## 3. Arms

Both builders: same model and effort level, same tools, the same brief except the section naming
their foundation, separate worktrees from the frozen commit, and no access to the other arm's
files or branch. Each delivers `run(driver, policy, max_steps) -> record`.

- **H (Haplous).** `benchmarks/e20_haplous/arm.py` on the existing runtime (machine/session adapter
  over the driver, a joint-velocity command surface, the frozen policy as the behaviour, a
  contract). It may change `haplous/`; such changes are counted as core modifications and must
  keep the existing test suite green.
- **G (reusable G1).** Phase 1, one-time and recorded separately: extract G1's supervision from
  `benchmarks/incumbent.py` into a reusable library `benchmarks/g1lib/` with a machine-adapter
  interface, with existing G1 behaviour unchanged (`tests/test_g1_incumbent.py` and HEB G1 results
  identical). Phase 2, marginal: `benchmarks/e20_g1/arm.py` on `g1lib` plus a UR5e driver adapter.
  The library must be generic, not shaped around this task.

## 4. Effort accounting

Builders mark phases with `scripts/e20_effort.py ARM PHASE start|end` and run acceptance only via
`scripts/e20_acceptance.py ... results/e20/<arm>/effort.jsonl` (runtime logged, excluded).

- **Primary:** active implementation time for the marginal phase (H: integration; G: phase 2),
  from its start mark to the first 15/15 acceptance run, minus logged acceptance runtime and minus
  usage-limit pauses (taken from session notifications by the coordinator).
- **Explanatory:** output tokens and tool calls (session metadata); acceptance runs before passing;
  non-test lines added or changed and files touched (`git diff --numstat`); tests written; core
  modifications (`haplous/` for H, library internals for G); G phase-1 effort reported separately.
- Agent effort is a proxy for engineering effort, not a measurement of it.

## 5. Decision rule (applied mechanically)

Caps: an arm that has not passed 15/15 after 3 hours of active time or 8 acceptance runs did not
reach equivalence; that is reported as the result for that arm.

With both arms at 15/15, R = H primary / G phase-2 primary:

1. R ≤ 0.67: provisional support for T03 (Haplous reduces marginal integration effort).
2. 0.67 < R < 1.5: no material difference. Record it; no architecture expansion follows.
3. R ≥ 1.5: the reusable G1 approach beats Haplous on marginal effort.

Totals including G phase 1 are reported for context only. One builder per arm is a pilot: the
result is indicative, with no confidence interval.

## 6. Verification

One combined independent verifier at the arms' head SHAs: reproduces both acceptance runs from
clean worktrees, audits the effort ledgers against git history and timestamps, checks isolation
(no cross-references), checks G phase-1 parity, and checks that no frozen file changed.
