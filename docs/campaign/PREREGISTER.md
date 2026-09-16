# EXTERNAL EVALUATION PREREGISTRATION (X90-X91)

Written 2026-09-09 before any external qualification or final run exists.
Committed before the freeze tag. If final numbers contradict dev numbers,
they are reported as measured — no post-hoc tightening.

## What is already fixed (from the previous campaign, unchanged)

- Prior held-out evaluation (HEB 0.2.0 regrade) stands VALID per S16/S17
  adjudication; canonical numbers: mediated coverage 0.842, BSR 0.844,
  breach 3; bare BSR 0.789, breach 6; refusal P/R 1.0/1.0.
- Prior known genuine mechanism boundary: tilt guard trips on long-arc
  constrained slides near corridor-minimum radius for BOTH systems.
- Negative evidence preserved: bare beats Haplous on some dev bounded-
  success cells; clearance-mechanism envelope [0.1, 0.5] mm; probing/
  filtering did not earn runtime status (R05 INCONCLUSIVE, R06 retired).

## Final external evaluation design

1. **Systems**: `direct_minimal`, `strong_osc` (same controller as direct,
   full telemetry — the OSC controller is the incumbent), `haplous_mediated`,
   and `haplous_mediated_governor` ONLY IF the governor passes qualification
   (G1 promotion rule below).
2. **Benchmarks**: internal HEB (reference, already frozen), robosuite
   NutAssemblySquare (external, official env, robosuite 1.5.2 + mujoco
   3.9.0). Isaac Lab family REJECTED on platform grounds
   (results/x10_isaac_disposition.json).
3. **Final seeds**: generated fresh at freeze time by hashing
   "haplous-external-eval-v0/robosuite" with a declared source; NEVER drawn
   from any dev seed list. Dev seeds: 0..4 (development only, tuning
   permitted). Qualification seeds: 5..9. Final seeds: 10..24 (15 paired
   scenarios x 3 systems minimum). No tuning after final results exist.
4. **Sample size**: 15 scenarios x 3 systems is sized for an expected
   nonzero-vs-zero difference on failure counts (binomial) and for
   completion-time percentiles; more scenarios are added only if a
   qualification bug required a semantic fix (fix must be applied to dev +
   qual + final identically, rerun all three).
5. **Metrics** (frozen): task success; bounded success = success AND no
   hard requirement breach AND no staleness violation; coverage (fraction
   attempted); hard violation rate; refusal precision/recall where
   refusals exist; completion p50/p95; peak |F|; runtime overhead (R2 - R1
   wall-clock per trial, reported as overhead % median).
6. **Grading implementation**: benchmark-owned, reused from the dev
   harness unchanged except bug fixes applied BEFORE qualification.
   Grading modules may not import haplous runtime internals (existing
   HEB-style guard).
7. **Failure taxonomy**: as in the campaign spec; framework-native failure
   reasons preserved alongside normalized classes.
8. **Governor promotion rule (G1)**: promote to final-eval system only if
   on qualification cells G1 shows (a) bounded-success improvement >= one
   scenario-cell over G0 with no coverage loss, or (b) equal BSR with
   >= 20% peak-force reduction, AND no new failure class introduced. A
   negative result retires G1 from default execution (candidate remains
   in benchmarks/governor/ as evidence).
9. **Reviews**: independent evidence review + red-team review run on the
   frozen protocol + raw artifacts (same oracle-based protocol as the
   pre-hardware campaign). Methodology-invalidating defects invalidate the
   affected results; mere dislike does not trigger reruns.
10. **Contact/real-data tracks** (X41, X50-X52) are independent evidence
    lines with their own preregistered questions (N01 solver invariance;
    observer precision/recall vs phase-label GT). They do not feed the
    primary external matrix unless a metric is BOTH preregistered and
    supported by dataset ground truth.

## Deviations ledger

- X10 Isaac Lab: REJECTED on platform evidence (documented JSON).
- Real data: derivative HF subsets (robot-lev/REASSEMBLE, DAVIAN/FMB)
  because official archives (54.8 GiB / 70-545 GB) exceed the 7.8 GiB disk.
  All real-data conclusions are labeled derivative-data results.
- robosuite k_z mapping: OSC_POSE kp stays fixed (150); ServoCommand
  k_z_n_per_m is not mapped onto it — recorded as a documented deviation
  in the adapter, visible in capability declarations.
