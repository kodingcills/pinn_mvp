# Documentation index

This directory contains durable engineering evidence. `CONTEXT.md` at the
repository root remains the short canonical state summary; the documents here
explain how that state was reached.

## Current engineering reports

- [Generalized execution evaluation, iteration 1](eval_v1/ENGINEERING_STATUS.md):
  M0 run registry (`evaluation/run_registry.py`), LIBERO-Dynamic pinned audit
  and CPU policy-free smoke, GPU handoff. Track B/G0 blocked on the missing
  v1.1 package. Companion files: `EVIDENCE_REPORT.md`, `DECISION_LOG.md`,
  `REPRODUCE.md`, `LIBERO_DYNAMIC_AUDIT.md`, `GPU_HANDOFF.md`.
- [HES v0 design and usage](HES.md) — the Haplous Evaluation System:
  outcome/attainability model, metric vector, attribution, integrity
  gates, regression, boundary search, populations, CLI, and how each part
  maps onto the existing `evaluation/` + `benchmarks/heb/` stack.
- [HES v0 canonical report](HES_V0_REPORT.md) — generated from
  `results/hes/v3_hardening_qualification/` by
  `scripts/run_hes_v0_campaign.py`; digest-checked with
  `hes report --check`.
- [HES v0 final report](HES_V0_FINAL_REPORT.md) — narrative conclusions
  from the v0 campaign, including the findings that weaken Haplous's
  registered claims.
- [HES causal diagnosis](HES_CAUSAL_DIAGNOSIS.md) — post-HES attribution of
  the G3 bounded-success loss to a single reach-rejection mechanism, with
  the evaluator-mislabelling evidence and the improvement ceiling (driver:
  `scripts/hes_causal_diagnosis.py`).
- [HES boundary calibration](HES_BOUNDARY_CALIBRATION.md) — falsification
  campaign on the 3.0-3.5 mm reach-rejection band: Branch B (reach
  authorised) vs the unmediated and supervisor arms, the bounded-execution
  evaluator repair, and the D1/E1 decision (driver:
  `scripts/run_hes_boundary_calibration.py`, `--check` reproducible).
- [HES reach capability envelope](HES_REACH_CAPABILITY_ENVELOPE.md) —
  predeclared 0.25 mm-band sweep over the full 3.0-5.0 mm offset region with
  paired authorised/direct/supervisor realizations: the capture and
  violation-risk curves, the scalar-adequacy verdict (adequate as a safety
  boundary, inadequate as a bounded-success predictor), the calibrated
  `max_initial_offset_m = 0.0035` m with its isolated qualification rerun, and
  the HES regression analysis (driver:
  `scripts/run_hes_reach_capability_envelope.py`, `--check` reproducible;
  qualification driver: `scripts/run_hes_reach_capability_qualification.py`).
- [Haplous v0-sim report](HAPLOUS_V0_SIM_REPORT.md) — the clean runtime
  (Phase III), the 12-scenario semantic benchmark (Phase IV, 12/12
  conformance), fault simulation (Phase V), and the physical gate with
  hardware requirements.
- [Haplous v3 software hardening](HAPLOUS_V3_SOFTWARE_HARDENING.md) —
  behavior-freeze parity, packaging/installability, dependency
  separation, generic resumable experiment infrastructure, CLI
  observability, and the local three-arm qualification (generated from
  `results/v3_hardening_qualification/` by
  `scripts/render_v3_hardening_report.py`).
- [Haplous product-scale evidence](HAPLOUS_PRODUCT_SCALE_EVIDENCE.md) —
  the frozen v3 candidate, the 27-scenario development family and the
  36-scenario sealed holdout, with the learned-policy workstream blocked
  on compute credentials rather than integration.
- [R-series freeze](research/R_SERIES_FREEZE.md) — the frozen pre-runtime
  research lineage: R01-R06 conclusions, refuted beliefs, known simulator
  limitations, migration classification, and reproduction commands. The
  clean Haplous runtime starts from the `research-rseries-complete` tag.
- [F1 state filter report](F1_STATE_FILTER_REPORT.md) — F1a filter core,
  synthetic consistency evidence, and the falsified clearance-step
  discrimination requirement; F1b/F1c are stopped pending a process-model
  decision.
- [F1c probe session report](F1C_PROBE_SESSION_REPORT.md) — physics-integrated
  filter sessions on the N5 primitive: measured pair covariance replacing the
  independent-side placeholder, clean-session consistency, the corrupted
  (confidently-wrong) session, and lifecycle/reset evidence.
- [N5 antipodal cancellation report](N5_ANTIPODAL_CANCELLATION_REPORT.md) —
  768-trial antipodal-pair campaign under injected part-tolerance clearance
  DC: pair-mean cancellation, difference-term linearity, the half-facet
  control, and the penetration-constant revision for F1c.
- [R4 coupled dynamics summary](../results/r4_coupled_modes_summary.json) —
  full-mass-matrix coupled modes and the exact discrete closed-loop map at
  2000/1000/500 Hz control periods (driver:
  `scripts/measure_r4_coupled_modes.py`).
- [N4 facet convergence report](N4_FACET_CONVERGENCE_REPORT.md) — stratified
  n=32/64/128 bore-facet sweep, radial/tangential estimator-error
  decomposition, geometry/contact crossover, and measured high-n plateau.
- [N2 radial probe report](N2_PROBE_PRIMITIVE_REPORT.md) — search-disabled
  probe geometry, 128-trial golden-angle validation, raw estimator error, and
  the residual 16-fold bore-facet pattern.
- [Chunk 1 engineering report](CHUNK1_ENGINEERING_REPORT.md) — repository lock,
  capture attribution, passive-radius measurement, sampled-control
  implementation, results, decisions, rejected alternatives, and test evidence.
- [Phase 2 deferred work](../PHASE2.md) — hardware-path and product-behavior
  items that remain out of scope.

## Chunk 1 evidence

Raw, machine-readable results:

- [L1 attribution data](data/chunk1_l1_attribution.csv)
- [L2 passive-capture grid](data/chunk1_l2_passive_grid.csv)
- [L3 control-rate data](data/chunk1_l3_control_rate.csv)

Rendered figures:

- [L1 capture attribution](figures/chunk1_l1_attribution.png)
- [L2 passive-capture radius](figures/chunk1_l2_passive_capture.png)
- [L3 sampled-control stability](figures/chunk1_l3_control_rate.png)

The figures are regenerated by
[`scripts/render_chunk1_report_figures.py`](../scripts/render_chunk1_report_figures.py).
Both PNG and SVG versions are committed: PNG renders consistently in Markdown,
while SVG is useful for presentations and lossless editing.

## N2 evidence

Machine-readable results:

- [N2 raw trial records](../results/n2_probe_raw.jsonl)
- [N2 derived summary](../results/n2_probe_summary.json)

Rendered figures:

- [N2 probe overview](figures/n2_probe_primitive.png)
- [N2 facet-phase residual](figures/n2_probe_facet_phase.png)

The results and figures are generated by
[`scripts/measure_n2_probe_primitive.py`](../scripts/measure_n2_probe_primitive.py).
Both PNG and SVG figure versions are committed.

## N4 evidence

Machine-readable results:

- [N4 raw trial records](../results/n4_facet_convergence_raw.jsonl)
- [N4 derived summary](../results/n4_facet_convergence_summary.json)

Rendered figures:

- [N4 estimator error and invalid asymptote fit](figures/n4_estimator_error_asymptote.png)
- [N4 fine-population bearing deflection](figures/n4_bearing_deflection_facet_phase.png)
- [N4 fine-population settled tip radius](figures/n4_settled_tip_radius_facet_phase.png)
- [N4 invalid-fit residuals](figures/n4_asymptote_residuals.png)

The results and figures are generated by
[`scripts/measure_n4_facet_convergence.py`](../scripts/measure_n4_facet_convergence.py).
Both PNG and SVG figure versions are committed.

## Existing visual assets

- `scene_cross_section.png` and `scene_cross_section_tilted.png` validate scene
  geometry.
- `bottom_out_fz_zero_offset.png` records the earlier bottom-out force trace.
- `d3_trajectory_capture_vs_fail.png` records an earlier trajectory comparison;
  its controller predates the current unified commit gate and must not be used
  as current performance evidence.
