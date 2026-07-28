# Phase 2 notes carried over from Phase 1

## Wrench clamp is a correctness requirement, not just a safety layer

Measured (Milestone 4 Stage A, `scripts/stage_a_close.py`): the z_des ramp
keeps commanding depth past the blind-hole floor with nothing clamping it.
Terminal (post-bottom-out) axial force grows unbounded and was measured at
**up to ~160N** at 3.0-3.5mm offset (peak_term_Fz column), against a stated
F_yield = 25N fixture limit -- roughly 6.4x over. Even offsets that never
reach the floor (resting on the flat table instead, pressed by the same
uncapped ramp) showed ~90N of axial force. On real hardware with a printed
fixture this is a broken part on the first episode, not a tail-risk edge
case -- it is the modal outcome of running the current insertion profile
(z_start=0.005, z_end=-0.045, z_rate=0.01, K_z=2000) to completion.

Milestone 5's wrench clamp will bound the commanded force, but the insertion
profile itself (ramp target depth, K_z, approach velocity) needs revisiting
so the *nominal* terminal force lands under F_yield without depending on the
clamp to save it every episode -- the clamp should be the backstop for a
policy mistake, not the thing standing between every rollout and a broken
fixture.

## Terminal force ringing and the dF_z/dt reward signal

Terminal F_z is not a clean step to a plateau -- measured std/mean during the
bottomed-out window ranges from std=22.9N on mean=51.0N (1mm offset, low
ratio, mostly monotonic climb -- 1 crossing of the window mean) up to
std~1-2N on mean~70N at 3.0-3.5mm (looks cleaner, 3 crossings). The number of
threshold-crossings during the terminal window is nonzero in every measured
case. A dF_z/dt sequence check for the Phase 2 reward needs to be verified
against this ringing directly, not assumed to see a single clean rise --
otherwise a decaying oscillation can register as several false rise-events
per episode.

## F_wedge (jamming threshold) -- inconclusive with this offset sweep

Analytic value (F_axial/(2*mu)) came out to ~87-112N across the measured
offsets. Empirical lateral force at the final step was 0.05-1.52N in BOTH
the seated and the not-seated group -- no separation was observed, because
none of the "jammed" trials in this sweep are actual diagonal wedging; they
are peg-resting-flat-on-the-table failures (never engaged the hole at all).
Real wedging (peg partially entered and stuck at an angle) was not produced
by this sweep and needs a scenario specifically designed to create it before
F_wedge can be calibrated for real.
