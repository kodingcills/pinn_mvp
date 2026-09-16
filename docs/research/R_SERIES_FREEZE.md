# R-Series Research Freeze

**Status:** research lineage frozen, pre-runtime Haplous baseline.
**Canonical commit:** the commit containing this file (tag `research-rseries-complete`).
**Test status at freeze:** 208 passed, 1 xfailed (full suite, ~96 s).

This document is provenance, not doctrine. It records what the R-series
campaign established, what it refuted, and what each existing component is
allowed to become in the clean Haplous runtime.

---

## 1. Freeze purpose

The pegsim research system has produced all the simulation evidence the
R-series questions can yield. The lineage is closed here so that the clean
Haplous runtime (Phase III) starts from a tagged, reproducible evidence
baseline instead of a moving codebase. Nothing in this file should be
rewritten by later work; later work appends.

## 2. Full configuration at freeze

Canonical scene/controller: `VALIDATED_LOOSE_V1` (peg r 5 mm, clearance
0.5 mm, chamfer 1 mm, plate 10 mm, peg 50 g, mu 0.4, n=16 blind hole;
dt 5e-4 s, elliptic cone, implicitfast, solref (0.002, 1), solimp
(0.9, 0.95, 1e-4); k_xy 1000, k_z seating 2000, k_rx=k_ry 5, k_rz 0.05;
f_search 1 N, f_target 15 N, f_yield 25 N; 2000 Hz, decimation 1).

R05 challenge-set deviations: n_segments=64 (pair covariance/bias evidence
geometry), tuned baseline spiral speed v_t=0.1 m/s (dev-selected), Haplous
treatment = 1 antipodal probe pair (1 mm radius) + N5 pair covariance/bias +
StateFilter + recentered straight-drop execution. Everything else locked
per `evaluation/locked-tests/R05-v3.yaml`.

## 3. R-series conclusions

| ID | Conclusion | Evidence |
|---|---|---|
| R01 | Main hypothesis SUPPORTED WITHIN TESTED SIMULATION SCOPE; the preregistered pooled-minimum difference constant REFUTED (difference carries 2x population-MEAN penetration, -2.18 um; pair-mean max error 0.864 um vs single-probe 6.246 um; strong side anticorrelation) | N5, commit `e31ea85`, `docs/N5_ANTIPODAL_CANCELLATION_REPORT.md` |
| R02 | SUPPORTED: physics-generated measurements reached the filter; clean convergence ~24 nm, NIS O(1); placeholder independent-side pair covariance falsely gated a physically correct pair at NIS ~38.5 and was replaced by the measured N5 covariance | F1c, commit `828ceb2`, `docs/F1C_PROBE_SESSION_REPORT.md` |
| R03 | WEAKENED: modeled channels behave as specified, but the 3 um TCP-bias session demonstrates the confidently-wrong regime (NEES 226-407, 2/6 gated); no sweep across corruption ranges was run | F1b/F1c, commit `0e1555b` |
| R04 | RESOLVED: the ~135 Hz physical-mode explanation is REFUTED; coupled sampled closed-loop dynamics reproduce the L3 boundary (spectral radius ~0.96 at 2000 Hz, 2.85 at 1000 Hz, 7.25 at 500 Hz; ~548 Hz overdamped branches) | commit `ff48f8b`, `results/r4_coupled_modes_summary.json` |
| R05 | INCONCLUSIVE under the preregistered K6 shared-infrastructure waiver (red-team objection to the label recorded; under the unqualified literal Rule Zero clause the experiment would be INVALID). delta_hat = 0, zero discordant pairs: n10=0, n01=0, n11=70, n00=10. Mixed ceiling (strata 0-3, both 16/16) and floor (stratum 4, both 6/16). No material execution value demonstrated for the probing/filter treatment; median +17.9 s probe overhead for zero discordance | R05-v3, `results/r05_adjudication.json` |
| R06 | RETIRED. No surviving Haplous execution advantage exists for R06 to isolate (79/80 scenarios ran the bit-identical baseline fallback; the sole guided execution was confidently wrong in an already-successful scenario) | both independent reviewers, `results/r05_adjudication.json` |

## 4. Important refuted beliefs (kept visible)

1. The R01 preregistered difference constant (2x pooled-MINIMUM penetration)
   was wrong; the pair difference carries 2x the population-MEAN penetration.
2. The placeholder independent-side pair covariance was wrong and falsely
   gated a correct pair (NIS ~38.5); measured N5 covariance replaced it.
3. The ~135 Hz rotational-mode explanation of the sampled-loop instability
   was wrong; the coupled sampled closed-loop map explains the boundary.
4. The F1c off-center probe observation model pulls accepted estimates
   toward the commanded center and is confidently wrong at realistic
   fixture offsets (R05 K5, directional confirmation: one accepted update,
   NEES 2.05e6, error 0.236 mm aligned with c0, cos 0.9994).
5. The fixed 1 mm-radius probe is structurally incapable of probing beyond
   |e| > 1.118 mm given measured R_passive = 1.5 mm (R05 K1, confirmed):
   probing cannot reach the only region where the baseline fails.

## 5. Known simulator limitations at freeze

- K6: `run_episode`'s capture branch triggers on the peg mouth's distance to
  the world origin (the true hole) and snaps the setpoint to the measured
  tip — a ground-truth-triggered control event present in both R05 arms and
  in all prior capture evidence. Absolute capture/success rates are partially
  attributable to it. Repair (observable capture trigger) is deferred to the
  next lineage.
- Control decimation 1000/500 Hz fails before seating (sampled coupled
  impedance); the 2000 Hz result is not hardware-plausible (R04/L3).
- `f_yield` is a configuration invariant, not a runtime clamp; no safety
  watchdog exists.
- F1a process priors are placeholders with no empirical basis.
- The 3 um TCP-bias confidently-wrong regime has no defense (gating caught
  9/10; the accepted update was confidently wrong).

## 6. Component migration classification

Classification is by role in the Haplous product, not code quality.

| Component | Disposition | Rationale |
|---|---|---|
| `pegsim/scene.py` (MJCF generator) | REUSE AS ADAPTER/SUBSTRATE | validated MuJoCo scene construction behind the machine boundary |
| `pegsim/control.py` (Cartesian impedance) | MIGRATE (mechanism) | execution-critical, evidence-earned controller |
| `pegsim/search.py` (spiral) | MIGRATE (mechanism) | strongest simple execution mechanism (R05 baseline) |
| `pegsim/rollout.py` (`run_episode`) | REUSE AS ADAPTER/SUBSTRATE | the only permitted physics-stepping path; K6 repair lands here |
| `pegsim/filter.py` (StateFilter) | KEEP — research/commissioning tooling | no demonstrated online execution value (R05/R06) |
| `pegsim/probing.py` (PairProbeSession) | KEEP — research/commissioning tooling | validated metrology mode (N2/N5); not runtime |
| `pegsim/error_injection.py` | KEEP — adversarial-test tooling | corruption channels for future robustness suites |
| `pegsim/dynamics.py` | KEEP — research tooling | R04 evidence generator |
| `pegsim/characterize.py` | ARCHIVE — historical experiment | L0/L1 lineage |
| `pegsim/r05.py` + R05 driver | ARCHIVE — historical experiment (evidence) | the locked R05 comparison |
| `scripts/measure_*` | KEEP — measurement tooling (per-file role) | reproducibility of committed results |
| `tests/` | KEEP | regression floor for the substrate |
| N-series reports/results | ARCHIVE — historical experiment | evidence, frozen |

## 7. Key artifact locations

- Locked designs: `evaluation/locked-tests/R05-v{1,2,3}.yaml`
- Freeze record: `results/r05_freeze.json` (HEAD `abedb9ba`)
- R05 adjudication: `results/r05_adjudication.json`
- R05 rows: `results/r05_dev_trials.jsonl` (200), `results/r05_holdout_trials.jsonl` (160)
- R05 summary: `results/r05_summary.json`
- Holdout trace sidecars (float32 npz + meta, gitignored, sha-pinned):
  `results/r05_traces_holdout/` (dir digest `cfc6fd41...` in the summary)
- N2/N4/N5/F1 evidence: `results/*`, `docs/*_REPORT.md`
- R04: `results/r4_coupled_modes_summary.json`

## 8. Reproduction commands

```sh
uv run pytest tests/ -q                       # 208 passed, 1 xfailed
uv run python scripts/measure_r05_comparison.py analyze   # recompute R05 summary
```

The R05 physics itself is one-shot and locked; rows, traces, and the freeze
record are committed evidence, not re-runnable. Manifest regeneration is
refused by the driver unless artifacts are explicitly moved aside.

## 9. What the clean runtime inherits

- The impedance + spiral execution mechanism (with the R05-tuned speed
  available as a variant).
- `run_episode` as the validated MuJoCo substrate, behind a machine adapter,
  with the K6 capture trigger repaired to an observable event.
- The N5 pair-measurement model and filter as commissioning/metrology tools.
- The R-series evidence discipline: preregistration, freeze records,
  paired intent-to-treat evaluation, adjudication with independent review.
