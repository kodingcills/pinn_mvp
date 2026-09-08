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

**UPDATE, post seating-trigger fix (F1-F4 below):** the search-phase
seating-phase transition previously fired on an ABSOLUTE depth
(`z_tip < -chamfer_width`), which low f_search physically cannot reach
(descent is capped at f_search/k_z). This bug, not force-starved passive
engagement, explained a f_search=1N static settle at 1.51mm failing to
seat. Re-measured directly with the fix: R_passive bracket at theta=0 is
IDENTICAL, (1.5, 2.0)mm, at both f_search=1N and f_search=4N -- confirming
F2's statics argument (mu=0.4 << 1, no self-locking possible on a 45deg
chamfer face at any force) and contradicting the "sub-newton bias
perturbs a marginal, force-dependent mechanism" framing above for THAT
test case.

The ORIGINAL 7/16 direction-dependent capture at 1.8mm (f_search=4N,
k_xy=500, search ON) is UNAFFECTED by this bug (f=4N always cleared the
old absolute threshold) and still stands as measured. Whether its
direction-dependence is a genuinely anisotropic R_passive (scene tiling
asymmetry) or something else is still open -- re-verify with a per-angle
static-settle sweep (search OFF) at exactly 1.8mm before trusting the
"small lateral bias" explanation above; it was written before this
contamination was found and needs the same scrutiny.

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
given a measured R_cap_dyn. The retuned (f_search, k_xy) sweep script was
deleted after its control law became obsolete; its findings remain in git
history.

mu itself: confirmed via git history that friction=0.4 has been the value
since Milestone 1 (pegsim/scene.py's TaskSpec default) -- it was never
0.2 and never changed. 0.4 is dry steel-on-steel; if the target assembly
is lubricated or plastic-on-plastic (0.1-0.2 typical), this doubles the
friction floor above and should be revisited with the user before it's
relied on as a design constant.

## Seating-phase trigger was an absolute-depth stale constant (F1-F5)

`in_seating_phase` fired on `z_tip < -chamfer_width` (an absolute -1mm),
which silently assumed the search-phase reference could always push the
tip that far down. False whenever `f_search/k_z < chamfer_width`: at
f_search<=2N (k_z=2000) the peg's max unsupported descent tops out at
<=1mm, strictly short of the trigger, and the seating ramp never fires no
matter how well the lateral search finds the hole. Same class of bug as
the old z_end=-45mm ramp target above -- a stale constant surviving a
spec change (the blind-hole floor), producing a physics-shaped symptom
("search is fundamentally hard at low f_search") that invited a
physics-shaped explanation (friction/passive-engagement starvation)
instead of the actual threshold bug.

Fixed: the unified commit gate `pegsim.rollout.DEPTH_COMMIT` (0.5mm) + a
live-tracked `z_flat` baseline (updated only while genuinely flat, not
engaging), hysteresis release at 0.7x. This replaced the old split
`DELTA_Z_TRIGGER` (0.2mm) / `DEPTH_ENGAGE_MARGIN` (0.05mm) pair — those symbol
names no longer exist in `pegsim/rollout.py`; see the DEPTH_COMMIT derivation
note there. Independent of f_search/k_z.
`assert_commit_gate_reachable` catches configs that can't clear the
gate with margin. The deleted F_search x k_xy sweep (see git history)
showed capture jumped from 0/16 to
12-13/16 across f_search=1-2N at both k_xy=500 and 1000, exactly the
"largely f_search-independent, gated by breakaway" pattern predicted once
the trigger stopped being the binding constraint.

Standing lesson: two real bugs in a row (the z_end ramp target, this
trigger) were stale constants left over from a geometry/spec change, and
both produced symptoms that looked like fundamental physics limits.
Check constants against the CURRENT geometry/config before building a
theory on top of a measurement.

## k_xy above ~1000 destabilizes tip-over at these K_rot values -- physical, not numerical, uninvestigated

At k_xy=2000 (k_rx=k_ry=5.0, dt=0.0005), a search-phase trial tipped past
the 10deg safety bound after ~370 steps (0.185s). Checked the coupled-mode
numerical-stability hypothesis directly: built the full 6x6 joint-space K
(J^T diag(stiffness) J) and M (mj_fullM) through the trajectory and
solved the generalized eigenvalue problem: max(omega*dt) stayed flat at
~0.38-0.39 throughout, including at the crash step -- nowhere near the
omega*dt<=2 explicit-integration limit. Not a linear numerical
instability (370 steps also rules out an integrator blowup on its own,
which fails in single digits). This is a genuine dynamic tip-over: as
tilt grows the contact migrates toward the rim, the moment arm grows,
and it runs away. Uninvestigated further -- not blocking, since k_xy=1000
already works (13/16, see above) and 500 works too (12/16).

## The validated search config is a COMBINATION, not independent defaults

Attempted to lock k_xy=1000 (ImpedanceGains) and f_search=1.0
(InsertionProfile) as the shared dataclass defaults (Milestone 4 G4).
Reverted both: `ImpedanceGains()` with k_xy=1000 broke
`test_holds_small_lateral_setpoint_in_free_space` (tilt hit 11.98deg) --
that test uses k_rx=k_ry=1.0 (the OTHER default, tuned specifically
against k_xy=500) and dt=0.001 (SolverSpec's default, not the 0.0005 every
search-config sweep in this document actually used). The (f_search=1N,
k_xy=1000) result above was only ever validated together with
k_rx=k_ry=5.0 AND dt=0.0005 -- changing one shared default in isolation
silently breaks unrelated call sites (free-space sanity checks, straight
drops) that depend on the OTHER defaults staying matched to what they
were tuned against.

Conclusion: `ImpedanceGains`/`InsertionProfile`'s class defaults stay at
their original (k_xy=500, f_search=4.0, k_rx=k_ry=1.0) values. The locked
search config (f_search=1N, k_xy=1000, k_rx=k_ry=5.0, dt=0.0005) is a
named combination that search-oriented scripts must construct explicitly.
The example scripts cited here previously were deleted after their control
law became obsolete; their findings remain in git history. It is not safe as
a silent global default.

## Agent working rules

**Code first, tests second.** Prefer correct code on the first try. Reason
explicitly about edge cases, types, units, invariants, and failure modes before
writing. Self-review the patch as if reviewing someone else's work, checking
every numeric literal against its physical meaning, before running anything.

**Test economy.** Targeted quiet runs (`pytest tests/test_x.py -q -x`) after
meaningful changes only. Full suite twice: baseline at start, verification
before reporting. Never between intermediate edits. On failure, diagnose the
root cause and fix it properly; a re-run loop with small patches means reasoning
has stopped.

**Output economy.** Never dump full test output, logs, or stack traces. On
failure: test id, one-line error, suspected cause. On pass: the count. Verbose
only on request. Echoing passing test output every turn is the largest token
sink in agentic work.

**Code quality.** Comment WHY, not WHAT. Every numeric constant either derives
from a config object or carries a comment naming what it was calibrated against.
Assert physical relationships, not values, wherever quantities are coupled.