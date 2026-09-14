# Development Wave 3 Hypothesis: Temporal Hold / Revalidate / Resume

## Failure class
TEMPORAL.FALSE_ABORT (taxonomy: EXECUTION_SEMANTICS / temporal validity)

## Current frequency (measured)
Counterfactual fork over the internal insertion family (results/
counterfactual_internal_summary.json): 4 genuine runtime stop-
interventions, 3 HARMFUL - all three are temporal-integrity stops
(insert_dropout_large, insert_stale_mid_motion,
insert_command_rejection_persistent) where the unmediated arm completes
within contract (peak 2.18 N) while Haplous aborts. In the three-arm dev
family these stops cost Haplous 3 task successes versus the supervisor
(dropout_large: supervisor also aborts; stale/rejection: supervisor
attempts through).

## Current severity
S1/S2 (false abort -> task failure on feasible work).

## Proposed mechanism (minimal)
Reclassify temporal-integrity faults as TEMPORARY UNCERTAINTY rather
than definitive invalidity. The declared contract (STATE_MAX_AGE_S hard)
is about AUTHORIZATION: never act on stale state. It is not a demand to
terminate if state becomes stale. New semantics in _execute_live:

1. Temporal-integrity faults (freshness gap after dropout, stale-age
   breach, command-rejection streak limit) SUSPEND authority instead of
   aborting: transition to DEGRADED, skip new commands, machine holds
   its last accepted command (compliant hold - the impedance leash
   bounds the held force by construction).
2. On restoration (fresh observation arrives / command accepted):
   REVALIDATE - the normal constraint evaluation on the fresh
   observation. Clean -> resume (evidence event
   temporal_revalidation_resumed, back to EXECUTING, no behavior
   re-reference: physics evolved continuously under hold). Hard breach
   -> abort exactly as today.
3. Bounded: max_hold_unobserved_s on MachineCapabilities (machine
   property: how long it can safely hold unobserved). Hold beyond the
   budget aborts with temporal_hold_budget_exhausted. Default 0.5 s =
   25x the 20 ms freshness contract; measured safe hold 200 ms
   (counterfactual). Placeholder pending hardware hold statistics.
4. Pre-execution stale-state refusal (execute() entry check) UNCHANGED:
   an execution that starts stale has no fresh state to revalidate from.
5. Machine faults stay definitive (abort) - untouched.
6. Constraint breaches on trustworthy (fresh) data during rejection-hold
   abort as today; stale observations during hold are not trusted data
   and do not abort (the fresh revalidation decides).

## Why the mechanism should help
The three HARMFUL stops all fired on restoration of a temporary fault.
Revalidation preserves the contract's authorization semantics (no new
motion on stale state) while not destroying executions the fault did not
actually damage. The bare arm's counterfactual completions prove the
held-through faults were physically benign in the measured cases; the
revalidation gate protects the unmeasured ones.

## Metrics expected to improve
- task success (Haplous): +3 scenarios (dropout_large, stale_mid_motion,
  command_rejection_persistent) -> 17/27, exceeding the supervisor
- harmful intervention rate: 3/4 -> ~0/4 in the forked family
- system BSR: +3 bounded successes
- false abort rate: down

## Metrics at risk
- slide temporal scenarios (stale_mid_slide, command_rejection_persistent
  slide variant): if revalidation fails there, they abort as today
  (no regression, no improvement)
- stale-DELECTION windows now cost hold time: completion times rise on
  affected scenarios (honest cost, reported)
- semantic conformance: 4-5 scenario expectations (expected ABORTED)
  encode the old abort semantics; each is counterfactually classified
  HARMFUL - expectations updated with justification, same pattern as
  Wave 2
- the S06 sweep finding "mediated aborts >= 20 ms dropout" changes
  meaning: the boundary moves from abort-at-20ms to
  hold-and-revalidate-with-budget; the semantic difference vs bare
  (revalidate vs ignore) remains and is stronger

## Keep criterion
Paired dev rerun: task success and system BSR improve; no new hard
violations; refusal precision/recall unchanged; the forked HARMFUL
stops convert to resumed completions or revalidation-gated aborts; no
regression on any scenario that previously completed.

## Revert criterion
Any new hard violation on a previously-completing scenario, any
completion lost, or revalidation passing while the physical state
actually breached (fork evidence contradicting revalidation decisions).
