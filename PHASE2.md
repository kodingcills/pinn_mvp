# Phase 2 notes carried over from Phase 1

## Wrench clamp: RESOLVED for nominal operation, keep as backstop

Original finding (Milestone 4 Stage A, first pass): the old z_des ramp
(z_start=0.005, z_end=-0.045, z_rate=0.01, fixed for the whole episode)
commanded depth past the blind-hole floor with nothing capping it, driven by
a stale constant (z_end=-45mm predates the 10mm blind floor). Terminal axial
force reached ~160N at 3.0-3.5mm offset against a stated F_yield=25N -- 6.4x
over, and the modal outcome, not a tail case.

Fixed with `pegsim/rollout.py`'s `InsertionProfile`: a rate-limited,
force-leashed two-phase setpoint (gentle search-phase reference, ramped
seating approach, `z_des` clamped to never command more than
`f_target/K_z` ahead of the live tip position). Measured after the fix,
across every offset that still seats (0-1.5mm):

| offset | peak F_z | settled F_z | impact velocity |
|---|---|---|---|
| 0.0mm | 14.95N | 14.95N | 10.0 mm/s |
| 0.5mm | 14.95N | 14.95N | 10.0 mm/s |
| 1.0mm | 14.95N | 14.95N | 10.3 mm/s |
| 1.5mm | 14.95N | 14.95N | 10.6 mm/s |

Peak == settled in every case (no impact spike at all) -- both governors
were required: the rate limit (bounds impact velocity, which is what
produced the 160N case at a fast, tip-and-lever entry) and the leash
(bounds steady-state force independent of the rate limit). Targets from the
spec (settled ~15N, peak<20N, impact<=12mm/s) are met with margin.

Keep the wrench clamp as a Milestone 5 backstop for a policy mistake, not
because nominal operation needs it -- nominal operation no longer does.

## Terminal force ringing -- re-measure once Stage B's config lands

The original ringing note (std=22.9N on mean=51.0N at 1mm offset, etc.) was
measured under the pre-A1-fix 70N-overdrive regime and is stale -- settled
force is now flat at f_target with no oscillation observed in the cases
re-measured (peak==settled exactly). Re-check the dF_z/dt reward design
against real data once Stage B's benchmark configuration is running,
rather than trusting this superseded number.

## F_wedge: still deferred, now literally inf in code

`ground_truth_seated` hardcodes `f_wedge = math.inf` (Stage A item A4) --
the analytic value computed previously (~87-112N) was itself derived from
the buggy ~70-90N F_axial and is invalid along with everything else from
that regime. None of the Stage A sweeps produced genuine diagonal wedging
(failures are flat-table non-engagements); calibrating F_wedge needs a
scenario built specifically to produce two-point wedging -- flagged for
Stage B item B2, where tight clearance should produce it.

## R_passive is force-regime-dependent -- a live finding, not settled

First measurement (pre-A1-fix, buggy ~70N overdrive during search):
R_passive = 3.5mm, ratio 2.33x (clearance+chamfer), attributed to a
tip-and-lever mechanism verified independent of gains/dt/geometry at the
time.

Post-A1-fix measurement (gentle f_search=4N reference during search):
R_passive = 1.5mm, ratio 1.00x -- exactly the rigid non-tipping bound. Same
task geometry, same peg, only the axial force regime changed. The
tip-and-lever mechanism was real (not a geometry or gains artifact -- both
were independently ruled out) but its STRENGTH was substantially inflated
by the same force-control bug being fixed here: the old uncapped ramp
happened to press hard enough during search to drive a real tipping
mechanism that a correctly-bounded 4N reference force does not reproduce.

This means the capture_radius formula (clearance+chamfer) was the right
answer for the right reason once force control is honest -- not because
the ridge-fulcrum geometric hypothesis was right (it was falsified by
direct raycast) but because the excess tipping authority came from
commanding more force than a real fixture-mounted system safely could.
