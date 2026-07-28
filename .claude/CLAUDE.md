# Physics-first peg-in-hole insertion (MuJoCo)

Research MVP testing one claim: a physics-informed hybrid controller reaches
equal reliability to an end-to-end imitation baseline with ~an order of
magnitude fewer trials.

## Verify with
pytest tests/ && python scripts/step1_drop.py --check

## Non-negotiable constraints
These are physics/statistics correctness, not style. Violating any of them
silently produces a result that looks fine and is wrong.

1. MuJoCo takes the CONVEX HULL of meshes. A plate-with-a-hole imported as a
   mesh has its hole filled in. Hole geometry MUST be built from radial box
   primitives.
2. cone="elliptic" always, paired with integrator="implicitfast". Pyramidal
   cones make sliding friction magnitude direction-dependent relative to
   world X/Y, which silently breaks spiral-search angular symmetry.
3. Joint damping, frictionloss, AND armature stay 0.0 on every peg DOF. All
   stiffness and dissipation comes from pegsim/control.py — passive joint
   forces would act in parallel with the controller and pollute contact
   wrench logs, and armature silently corrupts the effective-inertia
   calculation used for damping.
4. Impedance maps through the Jacobian at the peg_tip site: tau = J^T @
   F_cart, using mj_jacSite every step. Never assign F_cart to qfrc_applied
   directly. Never evaluate J at the cylinder's center of mass — peg_tip is
   the body origin and the correct pivot.
5. Damping is DERIVED per-DOF from the mass matrix diagonal
   (D_i = 2*sqrt(K_i * M_ii)), never hardcoded, never shared between
   translational and rotational DOFs (rz has ~40x smaller inertia than
   rx/ry).
6. mj_contactForce returns contact-frame wrenches. Rotate to world using
   contact.frame (row-major, normal = first row) and verify sign against a
   static-rest test (peg resting on plate -> summed world-frame force is +z,
   magnitude ~= m*g) before trusting any force number.
7. Spiral pitch (2*pi*b) MUST be strictly less than capture_radius (clearance
   + chamfer). Assert at construction. A larger pitch steps over the hole;
   no policy recovers.
8. Spiral uses CONSTANT TANGENTIAL speed: theta_dot = v_t / (a + b*theta),
   never a fixed angular rate. Fixed angular rate puts peak impact energy at
   the outer radii.
9. Offsets sampled AREA-uniformly: r = R*sqrt(u). Never r ~ U(0,R).
10. R_spiral >= delta_max and dx_max >= delta_max. Assert at construction.
11. RULE ZERO: the reward reads only quantities that exist on real hardware
    (FK depth, force, wrench). Simulator ground truth NEVER enters the
    reward, even where it's free. Ground truth is used ONLY to grade the
    reward function offline, never to train.
12. Residual outputs the SPIRAL CENTER at 10-20 Hz, never per-step
    corrections at control rate, and its output layer is zero-initialized —
    episode 1 must be exactly base-controller behavior.
13. Safety is structural (bounds, virtual fixture, wrench clamp), never a
    reward penalty. The wrench clamp sits OUTSIDE the learned path.
14. Success thresholds are pre-registered in a file before any run. Scripts
    refuse to run if it's missing.

## Scope discipline
- Vectorized envs + multiprocessing on one machine. NO Kubernetes, no queue
  workers, no distributed anything, no C++ rewrite.
- No config framework, no plugin system, no abstract base classes for a single
  implementation. Two people, one task.
- If you're about to add an abstraction for a second use case that doesn't
  exist yet: don't.

## Benchmark is FOUR conditions, not two
A end-to-end IL baseline / B physics controller alone / C physics+residual with
full-range randomization / D physics+residual with CI-derived range. C vs D is
the entire thesis. Two conditions only re-prove published work.

## Stop and ask
- Before changing solref/solimp/timestep/cone (contact model is load-bearing).
- Before changing any pre-registered threshold.
- If a result looks surprisingly good.
