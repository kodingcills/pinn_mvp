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

### R_passive is not a hard boundary -- it is the threshold of a marginal mechanism

The 1.5mm figure above was measured search-OFF, zero lateral bias: the peg
settles under pure axial force with no lateral spring pulling it anywhere.
The 16-angle cross-check at 1.8mm offset (Milestone 4, C2a/D1-D3) found
7/16 start angles capture past that boundary, direction-dependent (a
contiguous ~135deg arc, not scattered) -- with the spiral search ON, at
gains/f_search that turned out (see the force-budget finding below) to be
too weak to actually DRIVE the peg anywhere, but not too weak to apply a
sub-newton lateral BIAS to the same marginal tip-and-lever mechanism (D3
trajectory plot: the peg takes a near-straight path into the hole with the
commanded spiral setpoint barely moving, i.e. passive capture nudged by a
small static force, not active tracking).

Conclusion: R_passive=1.5mm is the threshold of a moment-limited mechanism
that a small lateral bias perturbs in either direction, not a fixed
geometric cliff. Any future use of "1.5mm" (or any R_passive number) as a
design constant must state the bias condition it was measured under --
zero bias, or a specific (f_search, k_xy) pair -- or it will silently
overstate how sharp the boundary really is.

## Search-phase force budget: k_xy*R_spiral must exceed mu*f_search

Found via the same C2a 16-angle cross-check: at f_search=4N, k_xy=500,
mu=0.4, the spiral's maximum deliverable lateral spring force at its own
maximum radius (k_xy*R_spiral = 500*0.002625 = 1.31N) never exceeded the
friction floor set by the search-phase axial preload (mu*f_search =
0.4*4.0 = 1.6N). Every failing start angle ran the spiral to full
exhaustion (all ~3.3 revolutions) while the actual tip never left a
~0.3mm tangle near its start -- the spiral was structurally too weak to
overcome friction and move the peg at ANY point in its own trajectory,
independent of starting angle.

`pegsim.search.assert_search_force_budget` now checks this (and the
related steady-state-lag-vs-R_cap_dyn constraint) at construction time,
given a measured R_cap_dyn. See scripts/measure_e3_search_phase_sweep.py
for the retuned (f_search, k_xy) sweep this motivated.

mu itself: confirmed via git history that friction=0.4 has been the value
since Milestone 1 (pegsim/scene.py's TaskSpec default) -- it was never
0.2 and never changed. 0.4 is dry steel-on-steel; if the target assembly
is lubricated or plastic-on-plastic (0.1-0.2 typical), this doubles the
friction floor above and should be revisited with the user before it's
relied on as a design constant.
