# Track B v1.1 results (generated)

Evidence class: **software-semantics correctness against fixture truth**. Not physical safety, not a protective stop, not OEM portability, not VLA performance.

- engine_sha256: `eb6fdafd5a635d155f04eddc7e134009bfc256dc1885d2cee49b35a01e3543d8`
- fixture_sha256: `1474f97cd5fa392c7e080e7f021b6d8afc8bef2aacfde65416e5f6f6f580ea6d`
- fixture_version: `1.1`

## A. Candidate correctness vs independent fixture truth

Passed **38 / 38** cases (decision + reason + required details).
Decision correct 38/38; reason correct 38/38.

| mismatch class | count |
|---|---:|
| false_admission | 0 |
| false_refusal | 0 |
| failed_terminal_transition | 0 |
| wrong_decision | 0 |
| wrong_reason | 0 |
| wrong_details | 0 |

Failed case ids: none

Confusion matrix (rows expected, columns observed):

| expected \ observed | ADMIT | REFUSE | SUSPEND | TERMINATE |
|---|---:|---:|---:|---:|
| ADMIT | 8 | 0 | 0 | 0 |
| REFUSE | 0 | 23 | 0 | 0 |
| SUSPEND | 0 | 0 | 4 | 0 |
| TERMINATE | 0 | 0 | 0 | 3 |

Interactions: 8 / 8 multi-fault cases correct.

## B. G0 mechanism-isolation comparison (NOT a competitive comparison)

baseline_g0_minimal_guards: deliberately limited component ablation, NOT an industrial incumbent.

G0 implementation matches its own spec (`expected_baseline_g0`): 38/38.
G0 aligned with truth under mapping {'ADMIT': 'ALLOW', 'REFUSE': 'BLOCK', 'SUSPEND': 'BLOCK', 'TERMINATE': 'STOP'}: 19/38.

G0 ALLOW where truth is not ADMIT (omissions by construction): ack_for_revoked_epoch, active_authority_revoked, active_lease_expires, calibration_invalid, conflicting_owner, fresh_command_but_stale_observation, future_three_ms_rejected, future_timestamp_invalid, inactive_lease_invalid, mode_dependent_freshness, mode_switch_unconfirmed, obs_too_old, observation_clock_mismatch, old_epoch_replay, precedence_epoch_over_stale, sensor_required_absent, stale_ack_over_obs_stale, wrong_resource_with_stale_obs

G0 BLOCK where truth is ADMIT: behavioral_native_force_approved_and_verified

Checks G0 implements (3): command_deadline, exact_mode_availability, external_stop_state

Checks G0 does not implement (17): ack_epoch, authority_epoch, authority_owner, authority_resource, calibration, clock_domain, future_skew, input_completeness, lease_validity, lifecycle_lease_terminate, lifecycle_revoke_terminate, mode_specific_freshness, mode_switch_confirmation, non_substitutable_control_authority, observation_freshness, required_sensors, verified_and_authorized_substitution

## Per-case

| case | expected | observed | pass | G0 | faults (decisive first) | trace id |
|---|---|---|---|---|---|---|
| nominal_admission | ADMIT/OK | ADMIT/OK | yes | ALLOW | - | `fe823b9072ac` |
| obs_too_old | SUSPEND/STATE_STALE | SUSPEND/STATE_STALE | yes | ALLOW | STATE_STALE | `b2e2e85adc24` |
| command_expired | REFUSE/COMMAND_EXPIRED | REFUSE/COMMAND_EXPIRED | yes | BLOCK | COMMAND_EXPIRED | `1aaf9326b2df` |
| old_epoch_replay | REFUSE/AUTHORITY_REVOKED | REFUSE/AUTHORITY_REVOKED | yes | ALLOW | AUTHORITY_REVOKED | `8237ef55f265` |
| unsupported_force_mode | REFUSE/CAPABILITY_UNSUPPORTED | REFUSE/CAPABILITY_UNSUPPORTED | yes | BLOCK | CAPABILITY_UNSUPPORTED | `f8a2fefc60d0` |
| native_force_eligible | ADMIT/OK | ADMIT/OK | yes | ALLOW | - | `dbd8bf0658c0` |
| conflicting_owner | REFUSE/AUTHORITY_CONFLICT | REFUSE/AUTHORITY_CONFLICT | yes | ALLOW | AUTHORITY_CONFLICT | `9b8862c45663` |
| ack_for_revoked_epoch | REFUSE/STALE_ACK | REFUSE/STALE_ACK | yes | ALLOW | STALE_ACK | `50f5f4a63bd3` |
| sensor_required_absent | REFUSE/OBSERVATION_UNAVAILABLE | REFUSE/OBSERVATION_UNAVAILABLE | yes | ALLOW | OBSERVATION_UNAVAILABLE | `5926c9b04811` |
| calibration_invalid | REFUSE/CALIBRATION_INVALID | REFUSE/CALIBRATION_INVALID | yes | ALLOW | CALIBRATION_INVALID | `79445703d732` |
| fresh_boundary_accepted | ADMIT/OK | ADMIT/OK | yes | ALLOW | - | `5bec765fd54e` |
| future_timestamp_invalid | REFUSE/CLOCK_INVALID | REFUSE/CLOCK_INVALID | yes | ALLOW | CLOCK_INVALID | `4e0001df437d` |
| precedence_epoch_over_stale | REFUSE/AUTHORITY_REVOKED | REFUSE/AUTHORITY_REVOKED | yes | ALLOW | AUTHORITY_REVOKED, STATE_STALE | `d967ebc0d94c` |
| precedence_epoch_over_capability_and_clock | REFUSE/AUTHORITY_REVOKED | REFUSE/AUTHORITY_REVOKED | yes | BLOCK | AUTHORITY_REVOKED, CAPABILITY_UNSUPPORTED, CLOCK_INVALID | `904b219bc47c` |
| precedence_capability_over_clock | REFUSE/CAPABILITY_UNSUPPORTED | REFUSE/CAPABILITY_UNSUPPORTED | yes | BLOCK | CAPABILITY_UNSUPPORTED, CLOCK_INVALID | `6d4a1becdba1` |
| precedence_clock_over_freshness | REFUSE/CLOCK_INVALID | REFUSE/CLOCK_INVALID | yes | BLOCK | CLOCK_INVALID, COMMAND_EXPIRED | `1546ae6a784f` |
| precedence_command_deadline_over_obs_stale | REFUSE/COMMAND_EXPIRED | REFUSE/COMMAND_EXPIRED | yes | BLOCK | COMMAND_EXPIRED, STATE_STALE | `bd517f5e1f14` |
| joint_torque_not_silently_native_force | REFUSE/CAPABILITY_UNSUPPORTED | REFUSE/CAPABILITY_UNSUPPORTED | yes | BLOCK | CAPABILITY_UNSUPPORTED | `979f81ed9e2c` |
| claimed_equivalence_not_verified | REFUSE/CAPABILITY_UNSUPPORTED | REFUSE/CAPABILITY_UNSUPPORTED | yes | BLOCK | CAPABILITY_UNSUPPORTED | `de8292aa32b9` |
| behavioral_native_force_unapproved | REFUSE/CAPABILITY_UNSUPPORTED | REFUSE/CAPABILITY_UNSUPPORTED | yes | BLOCK | CAPABILITY_UNSUPPORTED | `5438b3972204` |
| behavioral_native_force_approved_and_verified | ADMIT/AUTHORIZED_EQUIVALENCE | ADMIT/AUTHORIZED_EQUIVALENCE | yes | BLOCK | - | `f7ac44f2ad66` |
| mode_dependent_freshness | SUSPEND/STATE_STALE_FOR_MODE | SUSPEND/STATE_STALE_FOR_MODE | yes | ALLOW | STATE_STALE_FOR_MODE | `fad51627d64c` |
| active_lease_expires | TERMINATE/AUTHORITY_LEASE_EXPIRED | TERMINATE/AUTHORITY_LEASE_EXPIRED | yes | ALLOW | AUTHORITY_LEASE_EXPIRED | `f9f44664047d` |
| active_authority_revoked | TERMINATE/AUTHORITY_REVOKED | TERMINATE/AUTHORITY_REVOKED | yes | ALLOW | AUTHORITY_REVOKED | `5de83a042564` |
| inactive_lease_invalid | REFUSE/AUTHORITY_LEASE_EXPIRED | REFUSE/AUTHORITY_LEASE_EXPIRED | yes | ALLOW | AUTHORITY_LEASE_EXPIRED | `aeec1f639d40` |
| stale_ack_over_obs_stale | REFUSE/STALE_ACK | REFUSE/STALE_ACK | yes | ALLOW | STALE_ACK, STATE_STALE | `d6a223c426fc` |
| future_one_ms_allowed | ADMIT/OK | ADMIT/OK | yes | ALLOW | - | `5f9bfab7015e` |
| future_three_ms_rejected | REFUSE/CLOCK_INVALID | REFUSE/CLOCK_INVALID | yes | ALLOW | CLOCK_INVALID | `81beb319e4ea` |
| command_deadline_boundary_allowed | ADMIT/OK | ADMIT/OK | yes | ALLOW | - | `a2a0f87cf06a` |
| observation_clock_mismatch | REFUSE/CLOCK_DOMAIN_MISMATCH | REFUSE/CLOCK_DOMAIN_MISMATCH | yes | ALLOW | CLOCK_DOMAIN_MISMATCH | `f067f0b5d31b` |
| mode_switch_unconfirmed | SUSPEND/MODE_SWITCH_UNCONFIRMED | SUSPEND/MODE_SWITCH_UNCONFIRMED | yes | ALLOW | MODE_SWITCH_UNCONFIRMED | `326a9d87f276` |
| unrequested_sensor_absent_still_valid | ADMIT/OK | ADMIT/OK | yes | ALLOW | - | `2a916328c4c3` |
| wrong_resource_with_stale_obs | REFUSE/AUTHORITY_RESOURCE_MISMATCH | REFUSE/AUTHORITY_RESOURCE_MISMATCH | yes | ALLOW | AUTHORITY_RESOURCE_MISMATCH, STATE_STALE | `c7ec185c6e10` |
| external_stop_over_authority | TERMINATE/OEM_STOP_ACTIVE | TERMINATE/OEM_STOP_ACTIVE | yes | STOP | OEM_STOP_ACTIVE, AUTHORITY_REVOKED | `02eaf160bb00` |
| valid_sensor_for_force | ADMIT/OK | ADMIT/OK | yes | ALLOW | - | `ca174ed1fbdb` |
| fresh_command_but_stale_observation | SUSPEND/STATE_STALE | SUSPEND/STATE_STALE | yes | ALLOW | STATE_STALE | `7b8d601a3e4a` |
| expired_command_but_fresh_observation | REFUSE/COMMAND_EXPIRED | REFUSE/COMMAND_EXPIRED | yes | BLOCK | COMMAND_EXPIRED | `a41c64023157` |
| direct_torque_cannot_be_declared_equivalent | REFUSE/CONTROL_AUTHORITY_NOT_EQUIVALENT | REFUSE/CONTROL_AUTHORITY_NOT_EQUIVALENT | yes | BLOCK | CONTROL_AUTHORITY_NOT_EQUIVALENT | `dc1c30844fe0` |
