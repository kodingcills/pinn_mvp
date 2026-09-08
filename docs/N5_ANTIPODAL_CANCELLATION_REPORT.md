# N5 antipodal cancellation report

Date: 2026-09-08

Repository state entering N5: `ff48f8b` (`178 passed, 1 xfailed` at final
verification; entry baseline `140 passed, 1 xfailed` at `81117a9` plus the
R4/R03 work committed during this campaign).

Scope: the locked N5 antipodal-cancellation campaign (`N5b`) plus the N5c
geometric control condition. No estimator integration (F1c), no baseline
comparison, no hardware claims.

## 1. Outcome

N5 seated **768/768** search-disabled probes (0 exclusions, 0 incomplete
pairs) across five injected part-tolerance clearance levels at n=64 and
64 antipodal pairs per level, plus the 128-trial half-facet control. The
antipodal pair-mean hole estimate measured a maximum error of **0.864 µm**
versus a single-probe maximum of **6.246 µm** at dc=0 — 7.2× better, and
3.1× better than the pre-registered ~2.7 µm antipodal-mean target. Pair-mean
radial error measured −0.0206 µm mean (≤0.08 µm at every level), confirming
radial penetration cancellation in the mean. The pair full-difference term is
linear in the injected DC with measured slope **2.009222** (predicted 2.0) and
measured intercept **−2.183 µm** — refuting the pre-registered intercept
−5.926 µm: the difference constant is twice the **population-mean** settled
penetration (1.09 µm), not twice the N4 pooled-minimum (2.963 µm) that the
pre-registration quoted. The half-facet control (N5c) degrades the pair-mean
maximum from 0.864 µm to **3.477 µm** and introduces facet-phase-structured
radial error up to 1.213 µm, confirming the cancellation is specific to
same-facet-phase antipodal walls at even n. The pair observation model of
`pegsim.filter.StateFilter.update_pair` is **SUPPORTED within tested
simulation scope**, with one revised constant for F1c: the pair-difference
penetration bias must be the population-mean settled radius deviation
(−1.09 µm), not the facet-center minimum (−2.963 µm).

Label per campaign rules: **INTERNAL RESULT — SUPPORTED WITHIN TESTED
SIMULATION SCOPE**. This is a deterministic geometric/contact result for the
16-facet-family scenes measured; it is not a hardware uncertainty statement.

## 2. Full measured configuration

| Group | Value |
|---|---|
| Locked specification | end of `docs/N4_FACET_CONVERGENCE_REPORT.md`: n=64, injected DC in {−10,−5,0,+5,+10} µm, 64 antipodal pairs per level |
| Canonical object | `VALIDATED_LOOSE_V1` unchanged (n=16); every trial constructed `replace(task, n_segments=64, radial_clearance=0.5mm+dc)` |
| Swept parameter | injected part-tolerance DC `dc ∈ {−10,−5,0,+5,+10} µm` — a PHYSICAL scene change (true clearance 490/495/500/505/510 µm); controller and estimator keep nominal 0.5 mm |
| Task geometry | peg radius 5 mm; chamfer 1 mm; plate 10 mm; peg length 50 mm; mass 50 g; friction 0.4; blind hole; n=64 (194 geoms) |
| Solver | dt 5e-4 s; elliptic cone; `implicitfast`; solref (0.002, 1); solimp (0.9, 0.95, 1e-4) — unchanged from canonical |
| Controller | k_xy 1000; k_z_search 500; k_z_seating 2000; k_rx=k_ry 5; k_rz 0.05; decimation 1 (2000 Hz); delay 0 |
| Probe | `initial_offset_r = 2×nominal clearance = 1.0 mm`; `spiral_spec=None`; N2 search-disabled primitive; pen_allow 12 µm; max_seconds 16.3 s |
| Bearings | plus: `theta_k = k·golden_angle`, k=0…63; minus (N5b): `theta_k + π`; minus (N5c control): `theta_k + π + π/64` |
| Estimator reduction | `hole_est = settled_tip_xy − 0.5 mm · u(commanded bearing)` — nominal clearance only; true clearance never enters the reduction |
| Known bias constant | penetration = 2.9630093418717097 µm, loaded from committed `results/n4_facet_convergence_summary.json` (`by_n["64"].penetration_m_from_pooled_minimum`), never hardcoded |
| Pair definition | one pair = two separate `run_episode` episodes; pair formed only in analysis (mean and half-difference along the plus commanded bearing) |
| Populations | 5 levels × 64 N5b pairs × 2 probes = 640; N5c 64 pairs × 2 = 128 at dc=0; global trial ids 0…767 |
| Contact metadata | new `TrialResult.settled_contact_geoms` records every active geom pair at the settle snapshot |
| Statistics | population std (`ddof=0`); `numpy.percentile(..., 95)` linear method |
| Determinism | no stochastic element anywhere (bearings, levels, controller all deterministic); `seed: null` recorded by design |
| Execution | 14 workers; one `ProcessPoolExecutor` per DC level; one `build_episode_model_data` per worker; main process alone wrote and flushed JSONL |

## 3. Evidence and reproduction

- Driver: [`../scripts/measure_n5_antipodal.py`](../scripts/measure_n5_antipodal.py)
- Permanent tests: [`../tests/test_n5_antipodal.py`](../tests/test_n5_antipodal.py) (13 tests)
- Raw JSONL, 768 records: [`../results/n5_antipodal_raw.jsonl`](../results/n5_antipodal_raw.jsonl)
- Derived summary: [`../results/n5_antipodal_summary.json`](../results/n5_antipodal_summary.json)
- Figures: `figures/n5_antipodal_pairs.*`, `figures/n5_antipodal_difference.*`

```bash
uv run pytest tests/test_n5_antipodal.py -q
uv run python scripts/measure_n5_antipodal.py --workers 14
uv run python scripts/measure_n5_antipodal.py --analyze-only --workers 14
uv run pytest -q
```

`--analyze-only` re-derives the summary from the committed raw JSONL and
reproduces every number in this report without physics. Commit provenance:
driver/tests before the campaign, raw+summary+figures+report after; use
`git log --oneline -- results/n5_antipodal_raw.jsonl` for the measurement
commit.

## 4. Engineering decisions

### D1. The injected DC is a physical scene change, not a measurement perturbation

**Problem.** Part-to-part dimensional variation must move the TRUE geometry, or the campaign measures the analysis, not the physics.

**Decision.** Each level builds its own MJCF with `radial_clearance = 0.5 mm + dc`. The estimator reduction and the controller keep the nominal 0.5 mm. One scene per level (`scene_key` equality asserted per level) enables one reusable worker model per level.

**Defense.** Preflight asserts per-level task equality, one scene key per level, spiral disabled, probe radius inside `(max true clearance, R_passive)` and `< R_cap_dyn`. 768/768 seated at every level including ±10 µm.

**Rejected alternatives.** Perturbing reported measurements (models part variation as sensor noise — R03's error_injection module exists for the sensor side); changing only the floor radius (a partial geometry change that misstates the part tolerance being modeled).

### D2. N5c half-facet control is defined by this campaign and labeled as such

**Problem.** The pre-registered spec defines N5b only. Without a control, "pair mean is small" could be dismissed as averaging.

**Decision.** N5c: identical design at dc=0 with the minus probe at `+π + π/64` so opposite walls sit half a facet apart. Provenance recorded in the summary (`n5c_provenance`).

**Defense.** N5c degrades the pair-mean maximum to 3.477 µm (4.0× N5b's 0.864 µm) and produces facet-phase-structured radial error (max magnitude 1.213 µm). The degradation is measured, not asserted. Two contributions, both reported: loss of tangential anticorrelation (dominant) and the relief difference; the deterministic reduction-direction projection is ≤0.073 µm (derived in the test suite) and negligible.

**Rejected alternatives.** Running N5c at all DC levels (unnecessary for the geometry discrimination); running the control at n=16 (changes two variables — scene and relief scale — against the locked n=64 spec).

### D3. The penetration constant is loaded from committed evidence, never hardcoded

**Decision.** `load_penetration_m()` reads `by_n["64"].penetration_m_from_pooled_minimum` from the committed N4 summary and is cached; a missing or unusable file fails the preflight.

**Defense.** Test pins the exact value (2.9630093418717097e-06) and asserts failure on a missing file. This is what exposed the intercept discrepancy honestly rather than through a stale literal.

**Rejected alternatives.** Hardcoding 2.963 µm (the exact stale-constant failure mode this repository documents six times).

### D4. Excluded trials and incomplete pairs are reported, never dropped

**Decision.** Every failure record carries the full schema with `exclusion_reason`; incomplete pairs are enumerated in `summary["incomplete_pairs"]`; aggregates are survivor-conditioned only through the explicit `n_success` accounting.

**Defense.** Test `test_incomplete_pairs_are_reported_not_dropped` removes one side and asserts the incomplete entry appears with its reason. In the actual campaign there were 0 exclusions and 0 incomplete pairs.

### D5. Deviations from the pre-registered wording

1. The pre-registered antipodal-mean target (~2.7 µm) was beaten: measured maximum 0.864 µm. The prediction implicitly assumed partially independent side errors; the measured radial/tangential error correlation between sides is −0.8599/−0.9810 because at even n the opposite walls share the same local facet phase and the facet deflection enters the two reductions with opposite signs.
2. The pre-registered difference constant (−2×2.963 µm = −5.926 µm) was refuted: measured −2.183 µm. 2.963 µm is the pooled-MINIMUM settled penetration (facet-center); the difference intercept carries the population-MEAN settled radius deviation, measured −1.09…−1.16 µm per side at dc=0 across the committed populations. Structure (slope ≈ 2, linear in dc) confirmed.
3. N5c's design exists nowhere in prior documentation; it is defined in §D2 and its provenance is embedded in the summary JSON.

## 5. Measurements

Distances in µm. Population std with `ddof=0`. Per-level single-probe statistics pool both sides (their radial means are ~0 by ± antisymmetry; per-side means are given in §5.3).

### 5.1 Capture

| Quantity | Value |
|---|---:|
| Trials | 768 |
| Seated | 768 (100%) |
| Exclusions | 0 |
| Incomplete pairs | 0 |

### 5.2 Single probe vs antipodal pair mean at dc=0

| Quantity | Mean | p95 | Max |
|---|---:|---:|---:|
| Single-probe error norm (µm) | 1.972 | 4.369 | 6.246 |
| Pair-mean error norm (µm) | 0.190 | 0.566 | **0.864** |
| Pair-mean radial (signed, µm) | −0.0206 | — | +0.8635 |
| Pair-mean tangential (signed, µm) | −0.0166 | — | — |

### 5.3 Per-side radial error and settled radius (the penetration structure)

| Level | Plus radial mean (µm) | Minus radial mean (µm) | Settled radius − c_nom, mean (µm) | min / max (µm) |
|---:|---:|---:|---:|---|
| −10 µm | −11.143 | +11.102 | −11.138 | −13.915 / −9.739 |
| 0 µm | −1.163 | +1.122 | −1.160 | −2.963 / +0.297 |
| +10 µm | +9.065 | −8.904 | +9.069 | +7.985 / +10.329 |

The dc=0 per-side mean (−1.16 µm) reproduces N4's n=64 pooled radial signed mean (−1.015 µm, different 96-probe population), and this population's deepest single probe reaches exactly the committed N4 pooled-minimum penetration (−2.963 µm). The ±10 µm levels shift the settled radius by the injected DC as physics requires.

### 5.4 Difference term versus injected DC

| Level | Full difference mean (µm) | Predicted 2·dc − 2·pen (µm) | dc-estimate error mean (µm) | max (µm) |
|---|---:|---:|---:|---:|
| −10 µm | −22.245 | −25.926 | +1.840 | 3.221 |
| −5 µm | −12.195 | −15.926 | +1.866 | 3.236 |
| 0 µm | −2.285 | −5.926 | +1.821 | 3.223 |
| +5 µm | +7.838 | +4.074 | +1.882 | 3.341 |
| +10 µm | +17.969 | +14.074 | +1.948 | 3.293 |

Slope 2.009222 (predicted 2.0); intercept −2.183 µm (predicted −5.926 µm, refuted — see D5.2). The dc-estimate error columns use the N4 pooled-minimum bias constant; with the measured population-mean constant (−1.09 µm) the residual constant would shrink by 1.87 µm, leaving a per-pair scatter of the same order as the difference distribution.

### 5.5 N5c half-facet control at dc=0

| Quantity | N5b (antipodal) | N5c (half-facet) |
|---|---:|---:|
| Pair-mean norm max (µm) | 0.864 | 3.477 |
| Pair-mean radial mean (µm) | −0.0206 | −0.0665 |
| Pair-mean radial max magnitude (µm) | 0.8635 | 1.213 |
| Half-difference mean (µm) | −1.0915 | −1.0965 |

Shifting the minus probe by half a facet breaks the tangential anticorrelation and the relief cancellation, degrading the pair mean by 4×. The cancellation is a same-facet-phase geometric property of even-n antipodal pairs, not generic averaging.

### 5.6 Side-error anticorrelation (mechanism of the beaten target)

At dc=0: corr(radial_plus, radial_minus) = **−0.8599**; corr(tangential_plus, tangential_minus) = **−0.9810**; mean ratio radial_minus/radial_plus = **−1.002**. The two probes of a pair see the SAME world-frame wall-deviation vector with opposite reduction directions, so pairing cancels it.

## 6. Pre-registered checks

| # | Pre-registered target | Measured | Status |
|---|---|---|---|
| 1 | Single-probe norm ≈ 5 µm | max 6.246 µm at dc=0 (= N4 n=64 coarse max exactly; the ~5 µm anchor was the N4 fine-population max 5.295 µm) | **consistent within population definition** |
| 2 | Antipodal-mean error ≈ 2.7 µm | max 0.864 µm (3.1× better) | **beaten — mechanism measured (§5.6)** |
| 3 | Radial penetration cancels in the mean | pair-mean radial −0.0206 µm vs per-side −1.1 µm | **confirmed** |
| 4 | Difference = DC + constant 2×2.963 µm | slope 2.009 ≈ 2 confirmed; constant −2.183 µm vs −5.926 µm | **structure confirmed, constant refuted** — correct constant is −2× population-mean penetration (−2.18 µm) |

## 7. What this does not establish

- No hardware statement of any kind: deterministic simulation, no sensor noise, no calibration error, no control-rate limitation (all trials at 2000 Hz, decimation 1).
- The cancellation requires even n (opposite walls at the same local facet phase). Odd-n or out-of-round bores are not covered; real bores are round, so the facet structure itself does not transfer — what transfers is the pair-mean-observes-position / pair-difference-observes-clearance structure.
- The pair probes are separate episodes with no intra-pair fixture drift; the T9 half-drift coupling (dc error = half the intra-pair motion) remains the binding hardware constraint on probe interval.
- The penetration-bias correction here used a single constant for all bearings; per-bearing facet structure of the settled radius (±1.4 µm at n=64) remains as per-pair scatter that no constant can remove.
- The filter itself was not run against these measurements (that is F1c); no NEES, gating, or abort behavior is exercised by this campaign.
- `settled_contact_geoms` at the settle snapshot recorded floor-dominant contact sets: the peg is genuinely bottomed out (an irrelevant contact cannot fake `success`, which requires depth ≤ −9.86 mm), and the lateral equilibrium retains the commanded bearing; the snapshot is one instant of a dynamic contact set and is not evidence that the wall contact is absent during descent.

## 8. Tests added

13 permanent tests in `tests/test_n5_antipodal.py`: exact 768-trial locked design and bearing formulas; minus-bearing construction (antipodal and half-facet, plus unknown-campaign rejection); per-level task identity and one-scene-key invariant; estimator recovery of a known hole; commanded-frame decomposition with norm reconstruction; pair-quantity cancellation math against a synthetic analytic pair; pair-ordering invariance (mean invariant, difference sign flips); end-to-end `analyze()` on 768 synthetic perfect probes recovering slope 2.0, intercept −2·pen, and exact dc; incomplete-pair reporting; failure-record schema completeness; the committed-N4 penetration constant and its missing-file failure; the no-estimator-import scoping rule; and the worker-initialization precondition.

| Stage | Command | Result |
|---|---|---|
| Entry baseline | `uv run pytest -q` (at `ff48f8b`) | 178 passed, 1 xfailed |
| Targeted N5 suite | `uv run pytest tests/test_n5_antipodal.py -q` | 13 passed |
| Final full suite | `uv run pytest -q` | 178 passed, 1 xfailed |

## 9. Remaining risks and consequences for F1c

1. **The filter's penetration constant must change for pair-difference use.** `docs/F1_STATE_FILTER_REPORT.md` §2 specifies the simulation penetration as the N4 pooled-minimum (2.963 µm). N5 measures that the pair difference carries the population-MEAN settled penetration (1.09 µm per side). F1c should wire `penetration_m = mean(settled_radius − c_true)` at the operating scene (−1.09 µm at n=64, dc=0), or better, treat the residual per-pair penetration asymmetry as part of R.
2. The pair-mean position estimate is geometrically excellent here (0.86 µm max), but it inherits the facet-geometry artifact; on a round bore the analogous wall-deviation structure vanishes and the pair mean is limited by penetration asymmetry only.
3. The pre-registered prediction set was produced before the mean/minimum distinction existed in the evidence. Any future pre-registration should name the exact statistic (population, percentile) of every constant, not just its source dataset.
