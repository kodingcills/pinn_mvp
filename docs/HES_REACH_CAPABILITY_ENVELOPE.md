# HES Reach-Capability Envelope

Frozen runtime. The only system variable is the reach capability reported to the runtime (Branch B). Population is the frozen qualification generator extended to n=2000; the first 1000 scenarios are byte-identical to the frozen qualification population.

## 1. Scope and provenance

- Declared reach cap at freeze: `0.003` m
- Calibrated reach cap in the runtime: `0.0035` m (section 15)
- Band: 0.003–0.005 m
- Frozen baseline (same population): BSR 18.2%, coverage 57.3%, preservation 78.6%

## 2. Capture curve

| offset band (mm) | A present | B exec | B capture/exec | B capture/exec 95% CI | C capture/exec | D capture/exec |
|---|---|---|---|---|---|---|
| 3.00-3.25 | 126 | 102 | 31.4% | 23.2%–40.9% | 19.0% | 15.1% |
| 3.25-3.50 | 149 | 125 | 28.0% | 20.9%–36.4% | 20.1% | 19.5% |
| 3.50-3.75 | 60 | 52 | 9.6% | 4.2%–20.6% | 1.7% | 1.7% |
| 3.75-4.00 | 79 | 67 | 13.4% | 7.2%–23.6% | 3.8% | 3.8% |
| 4.00-4.25 | 50 | 42 | 2.4% | 0.4%–12.3% | 2.0% | 2.0% |
| 4.25-4.50 | 53 | 47 | 0.0% | 0.0%–7.6% | 0.0% | 0.0% |
| 4.50-4.75 | 48 | 41 | 0.0% | 0.0%–8.6% | 0.0% | 0.0% |
| 4.75-5.00 | 53 | 42 | 0.0% | 0.0%–8.4% | 0.0% | 0.0% |

## 3. Violation risk

| offset band (mm) | B violation/exec | B exec n | B peak force p95 (N) | C violation/exec |
|---|---|---|---|---|
| 3.00-3.25 | 49.0% | 102 | 59.42 | 77.8% |
| 3.25-3.50 | 47.2% | 125 | 70.90 | 75.2% |
| 3.50-3.75 | 51.9% | 52 | 87.07 | 86.7% |
| 3.75-4.00 | 50.7% | 67 | 95.13 | 84.8% |
| 4.00-4.25 | 61.9% | 42 | 102.23 | 88.0% |
| 4.25-4.50 | 63.8% | 47 | 85.30 | 86.8% |
| 4.50-4.75 | 61.0% | 41 | 104.97 | 79.2% |
| 4.75-5.00 | 61.9% | 42 | 108.56 | 79.2% |

## 4. Bounded-failure curve

| offset band (mm) | B bounded failure/exec | B exec n |
|---|---|---|
| 3.00-3.25 | 68.6% | 102 |
| 3.25-3.50 | 72.0% | 125 |
| 3.50-3.75 | 90.4% | 52 |
| 3.75-4.00 | 86.6% | 67 |
| 4.00-4.25 | 97.6% | 42 |
| 4.25-4.50 | 100.0% | 47 |
| 4.50-4.75 | 100.0% | 41 |
| 4.75-5.00 | 100.0% | 42 |

## 5. Capture levels

| level | raw crossing (mm) | isotonic crossing (mm) | reached above level | note |
|---|---|---|---|---|
| x90 | None | None | False | not_supported: capture never reaches this level in the band |
| x75 | None | None | False | not_supported: capture never reaches this level in the band |
| x50 | None | None | False | not_supported: capture never reaches this level in the band |
| x25 | 3.416 | 3.421 | True | None |

## 6. Authorised Haplous vs Direct (paired)

- both bounded: 59; B only: 23; C only: 0; neither: 536
- bounded successes created: 23; destroyed: 0
- McNemar exact: `{"b01_B_only": 23, "b10_C_only": 0, "method": "exact_binomial", "n_discordant": 23, "p_value": 2.384185791015625e-07}`

## 7. Interaction analysis

- `max_force_n`: levels=5, matched bands=2, median matched capture diff=0.5, max=0.5, material=True
- `deadline_s`: levels=6, matched bands=0, median matched capture diff=None, max=None, material=False
- `max_tilt_deg`: levels=1, matched bands=0, median matched capture diff=None, max=None, material=False
- `clearance_delta_m`: levels=5, matched bands=3, median matched capture diff=0.3263888888888889, max=0.3333333333333333, material=False
- `facet_count`: levels=5, matched bands=2, median matched capture diff=0.3712121212121212, max=0.3712121212121212, material=True
- `plate_thickness_m`: levels=3, matched bands=4, median matched capture diff=0.09929078014184395, max=0.1785714285714286, material=False
- `position_tolerance_m`: levels=2, matched bands=7, median matched capture diff=0.16904761904761909, max=0.23684210526315788, material=True
- `state_max_age_s`: levels=3, matched bands=6, median matched capture diff=0.09523809523809523, max=0.26666666666666666, material=False
- `receive_delay_s`: levels=4, matched bands=4, median matched capture diff=0.08308004052684903, max=0.1375661375661376, material=False
- `n_disturbances`: levels=2, matched bands=1, median matched capture diff=0.06060606060606061, max=0.06060606060606061, material=False
- `n_rejection`: levels=2, matched bands=2, median matched capture diff=0.21516754850088182, max=0.21516754850088182, material=False
- `n_dropout`: levels=2, matched bands=3, median matched capture diff=0.12629399585921325, max=0.19047619047619047, material=False
- `n_stale`: levels=2, matched bands=6, median matched capture diff=0.14594594594594595, max=0.32667876588021777, material=True

Boundary shift (median success offset by factor level, mm):

```json
{
 "clearance_delta_m": {
  "median_success_offset_mm_by_level": {
   "-0.0001": 3.2113717655256337,
   "-0.0002": 3.3225911394851666,
   "0.0": 3.2886098393081817,
   "0.0001": 3.3959415736824283,
   "0.0002": 3.3443840044486017
  },
  "shift_mm": 0.18456980815679458
 },
 "facet_count": {
  "median_success_offset_mm_by_level": {
   "16": 3.3869125209978455,
   "24": 3.2850883779059297,
   "32": 3.327602987969776,
   "48": 3.275357817707825,
   "64": 3.204132141523191
  },
  "shift_mm": 0.18278037947465453
 },
 "max_force_n": {
  "median_success_offset_mm_by_level": {
   "15.0": 3.3680391480799527,
   "20.0": 3.3706886060519734,
   "25.0": 3.3225911394851666,
   "None": 3.305242770209057
  },
  "shift_mm": 0.06544583584291619
 },
 "n_disturbances": {
  "median_success_offset_mm_by_level": {
   "0": 3.305242770209057,
   "1": 3.3680391480799527
  },
  "shift_mm": 0.06279637787089554
 },
 "n_dropout": {
  "median_success_offset_mm_by_level": {
   "0": 3.3318170709645885,
   "1": 3.2113717655256337
  },
  "shift_mm": 0.12044530543895471
 },
 "n_rejection": {
  "median_success_offset_mm_by_level": {
   "0": 3.2850883779059297,
   "1": 3.4257172746994686
  },
  "shift_mm": 0.14062889679353896
 },
 "n_stale": {
  "median_success_offset_mm_by_level": {
   "0": 3.305242770209057,
   "1": 3.537888074837935
  },
  "shift_mm": 0.2326453046288779
 },
 "plate_thickness_m": {
  "median_success_offset_mm_by_level": {
   "0.01": 3.266898461179783,
   "0.015": 3.332079798227878,
   "0.02": 3.3234220571157866
  },
  "shift_mm": 0.0651813370480947
 },
 "position_tolerance_m": {
  "median_success_offset_mm_by_level": {
   "0.000312": 3.250367190896688,
   "0.000512": 3.3443840044486017
  },
  "shift_mm": 0.09401681355191371
 },
 "receive_delay_s": {
  "median_success_offset_mm_by_level": {
   "0.0": 3.332079798227878,
   "0.005": 3.2886098393081817,
   "0.01": 3.2536134455557093
  },
  "shift_mm": 0.0784663526721685
 },
 "state_max_age_s": {
  "median_success_offset_mm_by_level": {
   "0.01": 3.2850883779059297,
   "0.02": 3.2841027624870507,
   "0.05": 3.3225911394851666
  },
  "shift_mm": 0.038488376998115914
 }
}
```

## 8. Scalar-threshold adequacy

**Verdict: adequate_as_safety_boundary+inadequate_as_success_predictor**

- raw capture series: [0.3137254901960784, 0.28, 0.09615384615384616, 0.13432835820895522, 0.023809523809523808, 0.0, 0.0, 0.0]
- isotonic capture series: [0.3137254901960784, 0.28, 0.11764705882352941, 0.11764705882352941, 0.023809523809523808, 0.0, 0.0, 0.0]
- max capture observed in band: 0.3137254901960784
- max isotonic deviation: 0.02149321266968325
- raw monotone violations (>0.05): 0
- offset curve coherent: True
- adequate as safety/refusal boundary: True
- adequate as bounded-success predictor: False
- material factors: ['facet_count', 'max_force_n', 'n_stale', 'position_tolerance_m']
- boundary-shift factors (mm): {}
- violation risk nondecreasing with offset: True
- required generalisation: Bounded success is not a function of offset alone: the material factors above shift capture at matched offset. The smallest generalisation supported by this evidence is to keep the scalar as a safety/refusal boundary and treat the other axes as separate feasibility constraints, not to fold them into the reach scalar.

## 9. Candidate operating points (envelope band)

| cap (mm) | authorized exec | bounded capture | violation risk | band coverage |
|---|---|---|---|---|
| 3.00 | 0 | n/a | n/a | 0.0% |
| 3.25 | 102 | 31.4% | 49.0% | 16.5% |
| 3.50 | 227 | 29.5% | 48.0% | 36.7% |
| 3.75 | 279 | 25.8% | 48.7% | 45.1% |
| 4.00 | 346 | 23.4% | 49.1% | 56.0% |
| 4.35 | 404 | 20.3% | 51.2% | 65.4% |
| 4.50 | 435 | 18.9% | 52.0% | 70.4% |
| 5.00 | 518 | 15.8% | 53.5% | 83.8% |

## 10. Counterfactual calibration impact (frozen population)

Reference bounded successes (direct_minimal): 131; attainable scenarios: 214

| cap (mm) | coverage | system BSR | conditional BSR | violation/accept | preservation | false refusal | accepted violations | G3 | G4 |
|---|---|---|---|---|---|---|---|---|---|
| 3.00 | 57.3% | 18.2% | 31.8% | 49.0% | 78.6% | 14.5% | 514 | fail | pass |
| 3.25 | 62.6% | 19.3% | 30.8% | 49.5% | 84.0% | 11.2% | 543 | fail | pass |
| 3.50 | 69.6% | 21.2% | 30.5% | 49.6% | 96.9% | 3.3% | 578 | pass | pass |
| 3.75 | 72.0% | 21.5% | 29.9% | 49.6% | 96.9% | 3.3% | 590 | pass | pass |
| 4.00 | 74.7% | 21.8% | 29.2% | 49.5% | 97.7% | 2.8% | 603 | pass | pass |
| 4.35 | 78.1% | 21.9% | 28.0% | 50.3% | 98.5% | 2.3% | 626 | pass | pass |
| 4.50 | 80.4% | 21.9% | 27.2% | 50.6% | 98.5% | 2.3% | 640 | pass | pass |
| 5.00 | 84.5% | 21.9% | 25.9% | 51.4% | 98.5% | 2.3% | 667 | pass | pass |

## 11. Decision

- Product criterion: existing declared hard gates, not invented targets: G3 preservation >= 0.95, G4 false refusal <= 0.20, and conditional violation within 0.02 of the declared cap's rate
- Candidate caps clearing the declared gates: ['0.00350', '0.00375', '0.00400', '0.00435', '0.00450']
- Scalar verdict: **adequate_as_safety_boundary+inadequate_as_success_predictor**
- Selected cap: 0.0035
- BSR-maximising cap (reported, not chosen): 0.00435
- Rationale: smallest cap clearing G3/G4 while holding conditional violation within tolerance of the declared rate; a minimal restoration of non-interference, not a BSR-maximising choice
- Selected impact: coverage 69.6%, system BSR 21.2%, preservation 96.9%, accepted violations 578

**Not bundled:** deadline expected_duration_s recalibration; freshness refusal mechanism; insert_000793 49.7 N mediation divergence

## 12. Strongest evidence against the chosen model

The interaction analysis identifies factors that shift the boundary at matched offset; see section 7. Any such factor is evidence that a single scalar is lossy.

## 13. Remaining uncertainty

- Bin-level Wilson intervals remain wide in the 3.75-5.00 mm region.
- The counterfactual join uses the reach-overridden Branch B outcome as the authorised outcome; it is a measurement, not a replay of the modified runtime.
- Deadline and freshness refusals are retained in Branch B and are not charged to reach calibration.

## 14. Next highest-information experiment

The scalar is adequate as a safety/refusal boundary, so the highest information action is the isolated calibration to the selected cap plus a rerun of the frozen qualification population with the HES regression analysis (driver: ``scripts/run_hes_reach_capability_qualification.py``). Success prediction is out of scope for the reach scalar; a separate feasibility experiment is needed to predict bounded success from the material factors.

## 15. Qualification rerun (frozen population)

Applied `max_initial_offset_m = 0.0035` m to `benchmarks/heb/tasks.py`, then re-ran the Haplous arm over the frozen qualification population (1000 scenarios, 3000 episodes including the reused deterministic baselines).

### Hard gates

| gate | name | passed | observed |
|---|---|---|---|
| G1 | integrity | PASS | `[]` |
| G2 | no_more_conditional_violation_than_baseline | PASS | `{'baseline': 0.805, 'runtime': 0.4956896551724138}` |
| G3 | non_interference | PASS | `0.9694656488549618` |
| G4 | false_refusal_ceiling | PASS | `0.031818181818181815` |
| G5 | reproducibility_provenance | PASS | `['benchmark', 'benchmark_version', 'calibration', 'cpu_count', 'dependencies', 'episode_schema_version', 'git_commit', 'git_dirty']` |

Qualified: **True** (0 gate violations).

### Metric deltas vs frozen qualification

| metric | candidate | frozen | delta | direction | gate |
|---|---|---|---|---|---|
| system.bounded_success | 0.212 | 0.182 | 0.03 | higher | pass |
| system.coverage | 0.696 | 0.573 | 0.123 | higher | pass |
| authority.execution_capture | 0.9636363636363636 | 0.8465116279069768 | 0.11712473572938686 | higher | pass |
| authority.authorization_precision | 0.30603448275862066 | 0.3193717277486911 | -0.01333724499007044 | higher | FAIL |
| authority.authorization_recall | 0.9681818181818181 | 0.8511627906976744 | 0.11701902748414372 | higher | pass |
| authority.hazard_rejection | None | None | None | higher | pass |
| authority.false_refusal_rate | 0.031818181818181815 | 0.14883720930232558 | -0.11701902748414376 | lower | pass |
| authority.false_abort_rate | 0.004545454545454545 | 0.004651162790697674 | -0.00010570824524312912 | lower | pass |
| authority.violation_given_acceptance | 0.4956896551724138 | 0.49040139616055844 | 0.005288259011855367 | lower | FAIL |
| non_interference.preservation_rate | 0.9694656488549618 | 0.7862595419847328 | 0.18320610687022898 | higher | pass |
| non_interference.bounded_success_regression_rate | 0.030534351145038167 | 0.21374045801526717 | -0.183206106870229 | lower | pass |
| non_interference.task_success_regression_rate | 0.030534351145038167 | 0.21374045801526717 | -0.183206106870229 | lower | pass |
| non_interference.unnecessary_intervention_rate | 0.022900763358778626 | 0.20610687022900764 | -0.183206106870229 | lower | pass |

Scenario-paired bounded-success delta (candidate - frozen): 0.03 over 1000 pairs (95% CI 0.02..0.041).
Slice gates: passed=True, checked=5, violations=0, underpowered=0.

The frozen zero-tolerance regression contract flags `authority.authorization_precision`, `authority.violation_given_acceptance`. Both are mechanistically expected consequences of authorising a wider offset band (accepting more in-band, non-attainable scenarios lowers authorization precision; accepting slightly more hard scenarios nudges conditional violation by half a point). Both remain inside the declared hard gates (G2/G3/G4). No other metric regresses.

Artifacts: `hes_reach_capability_envelope/qualification/` (episodes.parquet, manifest.json, provenance.json, analysis.json, gates.json, regression.json, summary.json, comparison.md).
