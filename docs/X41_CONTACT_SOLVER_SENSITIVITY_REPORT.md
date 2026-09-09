# X41 Contact-Solver Sensitivity Microbench

**Campaign status:** complete. **Every numerical cell below is a DEVIATION:
benchmark-only, never canonical.** The reference cell reproduces canonical
numerical values only to provide an internal comparator; X41 does not authorize
changing or revalidating the canonical contact model.

## Full configuration

The physical and control configuration is `VALIDATED_LOOSE_V1`: 5 mm peg
radius, 0.5 mm radial clearance, 1 mm chamfer, 10 mm blind-hole plate, 50 mm / 50 g
peg, friction 0.4, 16 segments; `k_xy=1000`, `k_z=2000`,
`k_rx=k_ry=5`, `k_rz=0.05`; search `k_z=500`, `f_search=1 N`,
`f_target=15 N`, `f_yield=25 N`, `v_descent=10 mm/s`; 5 mm start,
12 um lateral penetration allowance, 0.5 mm commit depth, 16.3 s limit,
2 kHz control, zero delay; spiral `v_t=5 mm/s`, 0.75 mm pitch, 2.75 mm
radius. Canonical contact values are `dt=0.5 ms`, elliptic cone,
`implicitfast`, Newton, `impratio=1`, `solref=(0.002,1)`, and
`solimp=(0.9,0.95,0.0001)`.

X41 used one-factor-at-a-time cells at commanded offsets 1 and 2 mm, each at
0/90/180/270 degrees (8 trials per cell, 48 total). Every trial called
`pegsim.rollout.run_episode`; no independent MuJoCo stepping loop was added.
The final 100 ms seating-persistence window defines settled force and force
standard deviation. Axial penetration is `max(0, -tip_z - plate_thickness)`;
contact penetration is `max(0, -min_contact_dist)` at the final sample.

Provenance: Haplous 0.5.0, MuJoCo 3.11.0, deterministic seed 41, source commit
`f2ea53261c5617368ca87e482f2f33e6d8d8d31e`, config SHA-256
`092852b3b83fc97b189ce80ac538db809b8a96d04825b30a227d4c0dd3984da3`.

## Measured results

All values except success and configuration are medians over 8 trials. Ranges
important to the disposition are stated below the table.

| DEVIATION cell | Success | t-seat (s) | axial pen. (um) | lateral excess (um) | settled Fz (N) | peak Fz (N) | post-seat Fz std (N) | impulse (N s) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| canonical numerics reference | 8/8 | 1.8018 | 24.7118 | 2.3199 | 14.9506 | 14.9506 | 1.48e-10 | 211.4867 |
| solver PGS | 8/8 | 1.8042 | 24.7135 | 5.0020 | 14.9506 | 14.9506 | 8.09e-7 | 211.4502 |
| solver CG | 8/8 | 1.7990 | 24.7133 | 5.3042 | 14.9506 | 14.9507 | 1.78e-5 | 211.5222 |
| `impratio=3` | 8/8 | 1.8120 | 24.7132 | 7.8721 | 14.9506 | 14.9507 | 1.94e-10 | 211.5399 |
| pyramidal cone | 8/8 | 1.7890 | **2.5009** | 5.9645 | 14.9950 | 14.9950 | 1.12e-10 | 212.4153 |
| `dt=0.1 ms` refinement | 8/8 | 1.8208 | 24.7130 | 6.2141 | 14.9506 | 14.9506 | 4.35e-7 | 211.3151 |

Across all 48 trials, time to seat was 1.5110-3.0195 s, settled force was
14.9506-14.9950 N, and the worst 100 ms force standard deviation was
0.000543 N. Lateral clearance excess was 0.086-10.319 um among the solver,
`impratio`, and cone cells and 1.299-10.130 um at refined timestep, always
inside the existing 12 um grading allowance.

## N01 disposition

- **Capture survives:** 48/48 seats at the tested 1-2 mm offsets; no variant
  failed or tripped an assertion.
- **The approximately 15 N force plateau survives:** the full settled-force
  span is only 14.9506-14.9950 N.
- **Settling behavior survives directionally:** every cell seats, cell-median
  times differ by less than 2%, and no practically meaningful post-seat force
  oscillation appears.
- **Penetration magnitude is cone-sensitive:** Newton/PGS/CG,
  `impratio=1/3`, and 0.5/0.1 ms elliptic cells remain at approximately
  24.7 um axial/contact penetration, while pyramidal contact gives
  approximately 2.5 um, a 9.9x reduction. Thus the broad claim that contact
  deformation is micrometric survives, but the narrower 7-25 um scale does
  not survive the cone formulation change.
- **Lateral final localization is numerically variable but remains within the
  pre-existing tolerance:** median clearance excess spans 2.32-7.87 um and
  maxima span 9.77-10.32 um across non-reference cells.

## External tool dispositions

### ContactBench: REJECTED

`uv pip install --dry-run contactbench` failed because no package exists in the
registry. The bounded Git dry run resolved upstream commit
`76fd04b8149fbc9360ff3a4fd2454c0fc7011c02` but failed in setuptools
`egg_info`: `package directory 'pycontact' does not exist`. ContactBench is a
clone-and-build C++/Python project requiring CMake, Pinocchio, ProxSuite,
eigenpy, Boost, and HPP-FCL; it has no insertion harness and no verified macOS
build path for this campaign. Repairing packaging, establishing the native
toolchain, and writing a Pinocchio insertion scenario is disproportionate to
X41. A working macOS arm64 wheel/build plus an upstream insertion scenario and
pre-registered metric mapping would unblock reconsideration. No dependencies
were installed or vendored.

### Drake: PROBED, NOT ADOPTED

Drake 1.46.0 installed into a temporary CPython 3.12 environment in about
10 seconds. A 1 s, 0.1 ms hydroelastic probe placed a rigid 5 mm sphere under a
15 N gravity load on a compliant plane (`E=10 MPa`, Hunt-Crossley dissipation
1.0, friction 0.4). It reached one contact, 15.000014 N final normal force,
14.999999 N s integrated normal impulse, 22.184 mm2 contact area, and
743.812 um geometric penetration.

This is deliberately not an apples-to-apples engine comparison. Drake uses a
user-selected volumetric hydroelastic modulus, sphere geometry, gravity load,
and 1 s horizon. The MuJoCo reference uses convex soft constraints, cylinder
and fixture geometry, controller load, and a 16.3 s horizon; it measured
24.712 um median axial penetration, 14.9506 N settled force, and 211.487 N s
whole-episode impulse. The large penetration difference primarily demonstrates
formulation and parameter dependence. A calibrated hydroelastic material model
and dedicated insertion scene would be required before Drake could adjudicate
the MuJoCo insertion result.

## Evidence

- `results/x41_solver_sensitivity_rows.jsonl`: all 48 trial rows.
- `results/x41_solver_sensitivity.json`: manifest, provenance, metric
  definitions, aggregates, and N01 classification.
- `results/x41_contactbench_disposition.json`: exact bounded install evidence.
- `results/x41_drake_probe.json`: setup, measurements, metric definitions, and
  non-equivalence caveat.
