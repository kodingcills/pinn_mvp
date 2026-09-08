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
| `pegsim/control.py` | Cartesian impedance controller and `ImpedanceGains` |
| `pegsim/search.py` | `ArchimedeanSpiral`, `SpiralSpec`, `DELTA_MAX_SPEC_M`, force-budget asserts |
| `pegsim/rollout.py` | `InsertionProfile`, `run_episode`, `TrialResult`, `VALIDATED_LOOSE_V1`, `ground_truth_seated` |
| `pegsim/characterize.py` | `measure_settling_depths`, `run_offset_sweep` |
| `pegsim/filter.py` | `StateFilter`, gated 3-state Kalman filter `[h_x, h_y, dc]` with antipodal pair batching, Joseph covariance, and consecutive-gate `ABORT`. Deliberately MuJoCo-free (AST-enforced by test) |
| `scripts/` | demo steps, retained characterization tools, and the measurement drivers (`measure_l1/l2/l3_*`, `measure_n2_probe_primitive`, `measure_n4_facet_convergence`, `measure_f1a_state_filter`) |
| `results/` | raw JSONL trials and derived summary JSON committed as evidence (`n2_probe_*`, `n4_facet_convergence_*`, `f1a_filter_summary`) |
| `tests/` | incl. `TestCompiledSolverOptionsMatchSpec`, `TestSilhouette` |
| `docs/` | engineering reports, raw result tables, and reproducible figures; start at `docs/README.md` |
| `PHASE2.md` | deferred findings, hardware notes, product behavior notes |

`pegsim/safety.py` **does not exist.** Milestone 5 was specified and never
built. This does not block simulation-only characterization because there is no
physical fixture to damage. `f_yield` is a configuration invariant, not a
runtime wrench clamp; the descent-rate limit and axial-reference leash govern
nominal motion and steady contact, but they are not an independent safety
layer. A watchdog and hard force limit are required before hardware.

---

## Geometry

A blind-hole scene with `n = n_segments` has `3n + 2` geoms: `n` each of
`bore_*`, `chamfer_*`, and `table_*` (all boxes), plus one `floor` cylinder and
one `peg_geom` cylinder. Thus canonical n=16 has 50 geoms; the N4 n=32/64/128
scenes have 98/194/386. A through-hole scene omits the floor and has `3n + 1`.

The hole is built from radial box primitives because **MuJoCo takes the convex
hull of meshes** — a plate-with-a-hole mesh would have its hole silently filled
in.

At facet-center azimuths, the contact-face meridian is exact: floor at −10mm
out to r=5.5mm, chamfer ramps −1.0 → 0.0mm over r=5.5–6.5mm, and the flat
table at 0.0mm ends at r=13.375mm. Between facet centers the radial silhouette
is polygonal and the boxes deliberately overlap to prevent gaps. At a canonical
n=16 facet boundary, the nominal 5.5/6.5mm faces occur at 5.6077/6.6275mm and
the nominal outer face at 13.637mm; overlap corners reach about 13.7045mm near
12.9°. The silhouette tests establish continuity and no chamfer/table ledge,
not azimuth-invariant radii.

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
         Z_FLAT_TAU_S 0.5 (alpha = min(1, control_decimation*dt/tau);
         canonical alpha 0.001)
spiral   v_t 5mm/s, a = b 119.37um, pitch 0.75mm, R_spiral 2.75mm,
         integrated tangential path 31.62mm, T_coverage 6.32s,
         max_seconds 16.3s
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
| N2 lateral solver penetration, seated | 7.3869 µm | `clearance - min(settled_tip_radius)` over the N2 raw trials; polygon relief is excluded at the facet-center minimum |
| Axial interpenetration at 15N | 24.7 µm | final z −10.0247mm |
| Constraint stiffness | ~277 kN/m | |
| R_cap_dyn (linear sweep) | 1.6mm | |
| R_passive | **1.5mm** | current config: 8/8 through 1.5mm, 0/8 from 1.75mm |
| Sampled control | 1000/500Hz: 0/16 | tilt guard trips at first plate contact; default stays 2000Hz |
| Search reach | 4.35mm | R_spiral + R_cap_dyn; 4.5/5.0mm fail correctly |
| N2 radial probe capture | **128/128** | 1.0mm offset, golden bearings, search off, canonical 12um grading |
| N2 probe estimator error | mean 0.02345mm; p95 0.04121mm; max 0.04199mm | `tip - clearance*u_probe`; max is 8.40% of 0.5mm fault threshold |
| N2 probe bearing error | mean abs 2.534°; p95 4.587°; max 4.699° | residual is 16-fold facet geometry; harmonic amplitude 4.088° |
| N4 estimator-error plateau, n≥64 | pooled mean envelope 3 µm; pooled max envelope 7 µm | conservative whole-µm external claim from n=64/128; 167×/71× below the 0.5mm fault threshold |
| N4 facet/contact crossover | relief 9.80/2.42/0.60/0.15 µm; penetration 7.39/5.15/2.96/4.12 µm | n=16/32/64/128; penetration first exceeds relief by n=32, while nth-harmonic R² collapses from 0.914 to 0.249 between n=32 and 64 |
| F1a-rev2 filter consistency | mean NEES 3.060536 | inside the prescribed [0.215795, 9.348404] band; synthetic T1–T10 suite, all synthetic observations |
| F1a-rev2 abort behavior | ABORT on 3rd consecutive gate (4th pair) | reinitializes fixture h from corrected observation, preserves dc; see `docs/F1_STATE_FILTER_REPORT.md` |
| F1a-rev2 process priors | q_h=(0.2 µm/cycle)², q_c=(0.001 µm/cycle)² | placeholders with no empirical basis; customer-facing stop rates undecided |
| Penetration bias constant | 2.963 µm | N4 n=64 `by_n["64"].penetration_m_from_pooled_minimum`; the filter's known observation bias in simulation |

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

1. **`run_episode` is the only permitted physics-stepping path for new
   measurement and diagnostic work.** Diagnostics add fields to `TrialResult`
   and read them; no new script writes its own control loop. Retained pre-rule
   development tools (including `scripts/step1_drop.py` and
   `scripts/contact_model_audit.py`), tests, and interactive UI code may call
   `mj_step` directly, but they are not current measurement evidence. This is a
   rule, not a framework — do not build config hashing, parquet, or a replay
   system.
2. Report what you **measured**, not what you concluded. Numbers in tables.
3. State the full config at the top of every report.
4. Deviate from spec only with an explicit note. Never swap silently.
5. Stop and ask if a result looks surprisingly good.
6. Stop and ask before changing `solref`, `solimp`, `timestep`, `cone`, or
   `integrator`. The contact model is load-bearing and was validated at
   specific values.
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
- **N4 reaches a measured contact-solver plateau after facet convergence.** At
  n≥64 the conservative pooled envelope is 3µm mean and 7µm max. Facet
  relief first falls below measured penetration between n=16 and 32, but the
  facet response remains strong at n=32; the nth-harmonic R² collapse from
  0.914 to 0.249 between n=32 and 64 marks the observed geometry-to-contact
  dominance crossover. The n=128 fine maximum (6.8µm versus 5.3µm at n=64)
  is unresolved at 32 fine samples and is not evidence of reversal; only its
  tangential DC offset remains unexplained. See
  `docs/N4_FACET_CONVERGENCE_REPORT.md`.
- **N5 (antipodal cancellation) has never run.** The audit at `81117a9`
  (`docs/AUDIT_GROUND_TRUTH.md`) confirms no N5 artifact exists anywhere on any
  ref. The locked spec lives at the end of `docs/N4_FACET_CONVERGENCE_REPORT.md`:
  n=64, injected part-tolerance DC in {-10,-5,0,+5,+10} µm, 64 antipodal pairs
  per level, pre-registered targets ~5 µm single-probe norm, ~2.7 µm
  antipodal-mean error, radial penetration cancellation in the mean, and a
  difference term of injected DC plus a constant of twice the measured n=64
  penetration (2×2.963 µm). It gates F1c.
- **F1b (error injection) and F1c (filter/physics wiring) are unstarted.**
  F1b/F1c were stopped pending the process-model decision; the audit confirms
  `pegsim/error_injection.py` does not exist and `StateFilter` is imported only
  by tests and the synthetic F1a driver. F1c additionally requires the N5
  result. See `docs/F1_STATE_FILTER_REPORT.md` §7.
- **The ~135 Hz rotational-mode estimate has never been measured.** Both
  `mj_fullM` call sites (`control.py` `derived_damping`/`stability_cap`) slice
  only the diagonal; no eigenvalue decomposition of the coupled mass matrix
  exists anywhere. The x/ry off-diagonal coupling (1.25e-3) is flagged in
  `assert_gains_stable` as necessary-not-sufficient, unquantified.

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
