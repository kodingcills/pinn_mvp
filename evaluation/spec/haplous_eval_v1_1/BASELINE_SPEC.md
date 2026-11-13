# Track B Baselines and Independent Truth (v1.1)

## Intent and limitations

Track B tests *logical execution-contract correctness*. It does not demonstrate motor control, machine safety certification, production OEM support, or customer ROI. Test fixtures describe an independently specified toy machine and clock model; the numeric thresholds are invented test parameters, not recommended machinery limits.

The comparison needs **two differently scoped baselines**:

- **G0 — Minimal guards** is a deliberately restricted *component-ablation* baseline to isolate what capabilities add. G0 is **not** a competitive industrial deployment, and no performance advantage over G0 may be advertised as an advantage over ROS 2, an OEM controller, or a competent system integrator.
- **G1 — Competent configured incumbent** must be specified from verified actual software, application safety/guard configuration, controller capabilities and any application code before making competitive or commercial claims. Until G1 is implemented, report only logical correctness against fixture truth plus G0's intentionally limited coverage.

## G0 exact executable rules

Input is each case's `defaults` merged with its `overrides` (override wins). G0 returns exactly one label:

1. If `oem_stop_active` is true, `STOP`. G0 **observes an externally authoritative controller stop state**; this is not an e-stop or safety function implemented by G0 or Haplous.
2. Else, if `required_mode` is not an exact element of `available_modes`, `BLOCK`.
3. Else, if `command_age_ms > command_deadline_ms`, `BLOCK`.
4. Else, `ALLOW`.

G0 deliberately ignores observation age, command owner/epoch, resource matching, clock domains, calibration, sensor requirements, phase-dependent bounds, and behavioral equivalent modes. G0's `ALLOW` on a test marked `REFUSE` or `SUSPEND` is therefore a known omission by construction, not a measured competitive advantage. Compare `G0` with the independent truth labels using the `expected_baseline_g0` field in each fixture; do not substitute the candidate engine's decision for that truth.

## Track B independent expected-decision precedence

0. **External authoritative stop:** `oem_stop_active` → `TERMINATE / OEM_STOP_ACTIVE`; stop authority dominates the rest.
1. **Authority and lifecycle:** `LEASE_EXPIRED` or `REVOKE` during `ACTIVE` → `TERMINATE`; invalid owner, resource, epoch, lease or `ACK` epoch → `REFUSE` with the corresponding authority reason. This precedence applies even when the observation is stale or the requested mode is unavailable.
2. **Capability and preconditions:** required mode and sensors, verified + explicitly permitted behavioral equivalence, and calibration state → `REFUSE` on unmet conditions.
3. **Clock validity:** observation/command clock must be the same declared `clock_domain`, or have an explicitly proven conversion; negative observation age below `-future_skew_tolerance_ms` → `REFUSE / CLOCK_INVALID`. Negative age within the tolerance is accepted as a bounded synchronization estimate only under this illustrative clock model.
4. **Command temporal validity:** command age exceeding its deadline → `REFUSE / COMMAND_EXPIRED`.
5. **Observation temporal validity:** age exceeding the selected mode's applicable maximum (or generic max when no mode override) → `SUSPEND`. An authorized behavioral substitute uses the *selected mode's* bound.
6. **Mode transition:** if mode switch is not acknowledged/confirmed, `SUSPEND / MODE_SWITCH_UNCONFIRMED`.
7. Otherwise `ADMIT / OK`, or `ADMIT / AUTHORIZED_EQUIVALENCE` with `selected_mode` and `BEHAVIORAL_SUBSTITUTION` trace if an alternate verified implementation is selected.

If a predicate is missing required data, it must not silently pass. Full compatibility and failure-response semantics will depend on the actual machine interface in later evaluations.

## Why a behavioral substitute is not a control-authority substitute

A contract literally requiring low-level `joint_torque` command access cannot be satisfied by a `native_force` mode merely through human authorization, even if an adapter falsely advertises such equivalence. `non_substitutable_modes` encodes these exact-authority requirements in the illustrative test profile. It requires that actual command authority. Conversely, a contract requiring a **bounded Cartesian compliance behavior** may be implementable by a verified native force mode if (1) the application explicitly permits that implementation and (2) the machine profile has independent evidence that the specified behavior can be achieved. The fixtures test both conditions separately. Even a passing fixture does not prove the claimed physical equivalence; that requires hardware-backed capability verification.

## Reporting

Produce a confusion matrix of `candidate_decision` against independently labeled `expected`, and separate counts of false admission, false refusal, wrong reason and failed terminal transitions. Also produce G0’s `ALLOW/BLOCK/STOP` alignment against the fixture truth, but label it as a **mechanism-isolation comparison**. Include exact fixture IDs and trace reasons for every mismatch.
