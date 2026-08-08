# CONTEXT.md

Read this first, every session. Then verify against the code before relying on
any number below. This file has been wrong before. **When it contradicts the
code, the code wins — and say so in your report so this file gets fixed.**

---

## What this project is

A research MVP in MuJoCo testing one claim:

> A physics-first controller plus a geometric state estimator recovers from a
> production-line fixture shift in about one cycle with zero learning, where
> end-to-end imitation needs a data campaign.

Task: peg-in-hole insertion. No RL is being trained. The deliverable is a
90-second recorded demo, not a paper.

---

## Repo structure

| Path | Contents |
|---|---|
| `pegsim/scene.py` | MJCF generator: `TaskSpec`, `SolverSpec`, `build_mjcf` |
| `pegsim/control.py` | Cartesian impedance controller, `InsertionProfile` |
| `pegsim/search.py` | `ArchimedeanSpiral`, `SpiralSpec`, `DELTA_MAX_SPEC_M`, force-budget asserts |
| `pegsim/rollout.py` | `run_episode`, `TrialResult`, `VALIDATED_LOOSE_V1`, `ground_truth_seated` |
| `pegsim/characterize.py` | `measure_settling_depths`, `run_offset_sweep` |
| `scripts/` | demo steps, retained characterization tools, and the `measure_l1/l2/l3_*` Chunk 1 drivers |
| `tests/` | incl. `TestCompiledSolverOptionsMatchSpec`, `TestSilhouette` |
| `docs/` | engineering reports, raw result tables, and reproducible figures; start at `docs/README.md` |
| `PHASE2.md` | deferred findings, hardware notes, product behavior notes |

`pegsim/safety.py` **does not exist.** Milestone 5 was specified and never
built. Not blocking in sim (no fixture to break, force governors cap commanded
force). Required before hardware.

---

## Geometry

50 geoms: 16 `bore_*` + 16 `chamfer_*` + 16 `table_*` (all boxes) + 1 `floor`
(cylinder) + 1 `peg_geom` (cylinder).

The hole is built from radial box primitives because **MuJoCo takes the convex
hull of meshes** — a plate-with-a-hole mesh would have its hole silently filled
in.

Silhouette verified at tile-center and tile-boundary azimuths: floor at −10mm
out to r=5.5mm, chamfer ramps −1.0 → 0.0mm over r=5.5–6.5mm, flat table at
0.0mm beyond, nothing past r=13.375mm.

`TaskSpec`: `peg_radius`, `radial_clearance`, `chamfer_width`,
`plate_thickness`, `peg_length`, `peg_mass` all required, no defaults.
`n_segments=16`, `friction=0.4`, `blind_hole=True` have defaults.
There is **no `peg_chamfer`** — the peg is a flat-bottomed cylinder with a sharp
90° rim.

---

## Canonical config: `VALIDATED_LOOSE_V1`

```
task     peg_r 5mm, clearance 0.5mm, chamfer 1mm, plate 10mm,
         peg 50g, mu 0.4, n_seg 16, blind_hole True
solver   dt 5e-4, cone elliptic, integrator implicitfast,
         solref (0.002, 1), solimp (0.9, 0.95, 1e-4)      [contact "setting B"]
gains    k_xy 1000, k_z_seating 2000, k_rx = k_ry 5.0, k_rz 0.05
insert   k_z_search 500, f_search 1N, f_target 15N, f_yield 25N,
         v_descent 10mm/s
gate     DEPTH_COMMIT 0.5mm, hysteresis release 0.7x
         Z_FLAT_TAU_S 0.5 (alpha = dt/tau)
spiral   v_t 5mm/s, a = b 119.37um, pitch 0.75mm, R_spiral 2.75mm,
         arc 31.62mm, T_coverage 6.32s, max_seconds 16.3s
grade    z_start 5mm, target_depth plate − 0.14mm (2x measured 0.07mm
         z-stop residual), pen_allow 12um (1.5x measured 8um lateral penetration)
control  decimation 1 (2000Hz), delay 0; lower rates are not validated
```

`ImpedanceGains.k_xy/k_z/k_rx/k_ry`, `SpiralSpec.v_t`, and
`SolverSpec.timestep` have **no defaults**; every construction site states them
explicitly. Shared defaults were silently breaking callers tuned against
different values.

### Episode interface and offset convention

`run_episode(cfg, pen_allow=0.0, *, model=None, data=None) -> TrialResult`.
`model` and `data` must either both be omitted or both be supplied. Supplying a
pair built by `build_episode_model_data` lets one worker reuse MuJoCo allocation
across episodes; `run_episode` still resets all runtime physics state first.

Offsets are **polar commanded-nominal offsets**, not hole translations:
`initial_offset_r` is meters and `initial_offset_theta` is radians. They set the
peg's initial `(x, y)` and the center of its commanded spiral. The true hole
mouth remains at world `(0, 0)`. Thus an offset represents a nominal command
that is wrong by that vector relative to the fixed hole.

`TrialResult.success` gates all settled-state telemetry. On success,
`settled_tip_xy`, `settled_setpoint_xy`, `settled_descent`,
`tilt_at_settle_rad`, `true_hole_xy`, and `nominal_xy` are snapshots at the
sample that completes the seating-persistence window. All six are `None` on
failure.

---

## Measured numbers

| Quantity | Value | Notes |
|---|---|---|
| Capture at 2.0mm | **16/16 = 100%** | current canonical controls, L1/L3 |
| Prior capture, offsets 1.0–2.25mm | 96/96 = 100% | pre-L0 scoring tolerances; not re-run in Chunk 1 |
| Time-to-seat at 2.0mm | 2.015–3.1525s | 2000Hz L3 cell |
| Max tilt across 96 trials | 0.228° | |
| Settled / peak F_z at 2.0mm | 14.9506 / 14.9512N | 2000Hz L3 cell |
| Max post-seat |F_z| excursion, 200ms | 4.1805N | seating transient, 2000Hz L3 cell |
| Static settling depth | 4.48 µm | pure contact compliance at 1N |
| Lateral interpenetration, seated | ~8 µm | tip_lateral 0.5079 vs clearance 0.5 |
| Axial interpenetration at 15N | 24.7 µm | final z −10.0247mm |
| Constraint stiffness | ~277 kN/m | |
| R_cap_dyn (linear sweep) | 1.6mm | |
| R_passive | **1.5mm** | current config: 8/8 through 1.5mm, 0/8 from 1.75mm |
| Sampled control | 1000/500Hz: 0/16 | tilt guard trips at first plate contact; default stays 2000Hz |
| Search reach | 4.35mm | R_spiral + R_cap_dyn; 4.5/5.0mm fail correctly |
| N2 radial probe capture | **128/128** | 1.0mm offset, golden bearings, search off, canonical 12um grading |
| N2 probe estimator error | mean 0.02345mm; p95 0.04121mm; max 0.04199mm | `tip - clearance*u_probe`; max is 8.40% of 0.5mm fault threshold |
| N2 probe bearing error | mean abs 2.534°; p95 4.587°; max 4.699° | residual is 16-fold facet geometry; harmonic amplitude 4.088° |

---

## The project's #1 failure mode

**Stale constants.** Six expensive bugs so far. Four were a position threshold
whose true value is a force divided by a stiffness — correct when written,
silently wrong after a parameter changed, and each presented as a
physics-shaped symptom that cost multiple rounds of wrong theorizing.

| Bug | Root cause |
|---|---|
| `z_des` ramp to −45mm → 70N terminal force | written when plate was 40mm; plate is 10mm |
| Success used first-passage | now final-settled + 100ms persistence |
| Spiral couldn't move the peg | `k_xy·R_spiral` (1.31N) < `mu·f_search` (1.6N) |
| Seating trigger at z_tip < −1mm | max descent is `f_search/k_z` = 0.5mm — unreachable |
| `delta_max` derived from capture_radius | now fixed at `DELTA_MAX_SPEC_M` = 2.0mm |
| `DEPTH_ENGAGE_MARGIN` 0.05mm | inside the settling band; froze the spiral 82.8% of steps |

**Reflexes that follow:**

1. Before theorizing about a physical mechanism, verify every constant in the
   loop against current geometry, then plot the trajectory. Both bugs that were
   found fast were found this way. Seven theory-first hypotheses were falsified.
2. Assert physical *relationships*, not values:
   ```
   z_search      = -f_search / k_z_search
   max_descent   =  f_search / k_z_search
   DEPTH_COMMIT  <  0.5 * max_descent
   r_breakaway   =  mu * f_search / k_xy  <  0.5 * R_cap_dyn
   pen_allow     <  0.2 * clearance
   ```
3. A quasi-static mechanism that appears to depend on force magnitude is usually
   a threshold artifact — Coulomb driving and resisting terms scale together.
4. Check signal magnitude against the sensor noise floor before designing around
   it. A UR5e F/T sensor is ~4N accurate; the chamfer cue at f_search=1N is
   ~0.7N and the seating impulse is ~1.2N. Both invisible on hardware. This is
   why the seating trigger is FK-depth, not force.

---

## Standing rules

1. **`run_episode` is the only thing that steps physics.** Diagnostics add
   fields to `TrialResult` and read them. No script writes its own loop.
   This is a rule, not a framework — do not build config hashing, parquet, or
   a replay system.
2. Report what you **measured**, not what you concluded. Numbers in tables.
3. State the full config at the top of every report.
4. Deviate from spec only with an explicit note. Never swap silently.
5. Stop and ask if a result looks surprisingly good.
6. Stop and ask before changing `solref`, `solimp`, `timestep`, or `cone`. The
   contact model is load-bearing and was validated at specific values.
7. Any geometry change ships with a permanent assert-based test **and** a
   committed cross-section render.
8. When you fix something, delete the wrong version. No compatibility shims.
9. No abstraction for a second use case that does not exist yet.
10. Commit after each work item. Never squash a file deletion with a behavior
    change — it destroys `git bisect`.

### Test and iteration economy

- Reason through edge cases, types, and invariants **before** writing. Aim for
  correct on the first try.
- Self-review before running anything.
- Targeted quiet runs (`pytest tests/<file> -q -x`) after meaningful changes
  only. Full suite at baseline and final verification.
- Diagnose root causes. Do not re-run loops with small patches.
- Never dump full test output, logs, or stack traces. Report test id, one-line
  error, suspected cause. Verbose only on request.

---

## Open and unresolved — do not chase unless told

- **The historical “85%” cell did not reproduce at the requested 2.0mm
  offset.** Every constructible L1a cell and each one-at-a-time L1b reversal
  was 16/16. The exact all-old combination was 6/16. CAD seeding alone restored
  16/16 even with the old shallow/stiff pair and force continuity off. Mechanism:
  the old latch committed in mid-air at t=0.416s (tip +0.940mm, z_flat
  +0.995mm), pausing 99.97% of search; CAD seeding committed below the plate
  at 2.0575s and paused 9.81%.
- **4-fold cardinal capture pattern** at pre-fix offsets. Elliptic cone
  confirmed compiled; joint axes ruled out by a 45° rotation test. May be moot
  at 100%.
- **k_xy = 2000 tip-over** (tilt 0.11° → 10.29° over ~370 steps). Physical, not
  numerical — `max(omega*dt)` stayed at 0.38. Not blocking; k_xy=1000 works.
- **Control decimation is blocking sim-to-real.** At 1000Hz and 500Hz all
  16/16 trials trip the 10° tilt guard at first plate contact; delayed 500Hz
  also trips. The failure begins before commit/seating, so it is the sampled
  coupled lateral/rotational impedance (`k_rx/k_ry=5` with the x/y coupling),
  not `D_z`, `K_z`, or the commit gate. Do not set decimation 4 as default.
- **`F_wedge` = inf**, deferred. No jams exist in this regime to calibrate on.
- **Search-disabled radial probing is accurate but facet-limited.** N2 seated
  128/128 at a `2*clearance=1.0mm` commanded offset. The raw estimator has
  p95/max error 0.04121/0.04199mm. Bearing error is not zero: its fitted 16th
  harmonic is 4.088°, identifying the 16-box bore geometry rather than a frame
  offset. See `docs/N2_PROBE_PRIMITIVE_REPORT.md`.

---

## Deferred to PHASE2.md (hardware path)

- **Safety watchdog.** Independent process above the control loop, faster than
  the command rate, hard force limit, emergency stop regardless of what the
  controller commands.
- **Touch-off routine.** `z_flat` is currently seeded from CAD nominal. Real
  hardware must establish that datum by contact: descend at `f_search` until
  contact, latch `z_flat`. On the sim-to-real critical path.
- **Control-rate remediation.** Decimation and one-cycle delay are implemented,
  but the 1000/500Hz measurements above fail before seating. Real UR5e RTDE
  caps at 500Hz with jitter. Retune/replace the sampled coupled impedance before
  hardware; do not claim the 2000Hz result is hardware-plausible.
- **Recording.** Use `mujoco.Renderer(m, h, w)` synchronously inside the demo
  wrapper, not `launch_passive`. The passive viewer runs on its own thread;
  programmatic frame capture from it tears and drops frames. `launch_passive`
  is for interactive watching only.
- **Product behavior: salvage-and-latch.** Field practice says a shift beyond
  ~0.5mm in a precision task is special-cause variation. Correct behavior:
  finish the current insertion to salvage the part, publish the deviation
  vector with part/batch/cycle ID, latch a fault, block the next automatic
  cycle until a human clears it. Silent adaptation across a batch hides a
  mechanical fault behind software. The 0.5mm threshold is tied to what a
  pinned fixture should hold (microns), not to task clearance.
- **Estimator measurement mode.** M2a showed only 0.106mm spring error at first
  commit and near-zero error after the correct snap-to-tip capture behavior, so
  spring deflection is not a usable natural-cycle direction signal. N2 instead
  validated `hole_est = settled_tip_xy - clearance*u_probe` using a deliberate
  search-disabled radial approach: 128/128 seated, p95 error 0.04121mm. N3 must
  still measure the 64-trial natural-search negative before deliberate probing
  is declared mandatory rather than merely the validated metrology mode.
