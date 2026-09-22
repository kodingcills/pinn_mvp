# HES Boundary Calibration Falsification Campaign

Frozen Haplous runtime. Evaluator semantics repaired to the bounded-execution epoch (`bounded-execution-v1`) before any reach calibration claim. The only system variable is the reach capability reported to the runtime, and only for Branch B.

## 1. Question and scope

Is `max_initial_offset_m = 0.003` merely miscalibrated relative to the actual bounded capture envelope, or does Haplous genuinely fail to execute boundedly in the 3.0-3.5 mm region?

- Eligible boundary_hi reach-rejections run once: **142**
- Branch A (frozen Haplous): 0.0 capture (142 refusals)
- Branch B (reach authorised): 21.1% among eligible, 26.5% among executed
- Branch C (direct_minimal): 16.9%
- Branch D (competent supervisor): 14.8%
- Branch B refusals retained: submit-time (non-reach) 19 of which deadline 19; mid-execution freshness 10

## 2. Evaluator repair (prerequisite)

- Legacy epoch `completion-only-v0`: HARMFUL iff the counterfactual continuation merely completed the task.
- Corrected epoch `bounded-execution-v1`: bounded execution (`task_success AND no hard violations`) is the reference.
- Migration on the 23 frozen intervention forks: `{"BENEFICIAL->BENEFICIAL": 5, "HARMFUL->HARMFUL": 1, "HARMFUL->UNNECESSARY": 11, "UNNECESSARY->UNNECESSARY": 6}`
- Legacy HARMFUL forks without a bounded continuation: **12** (secondary check: 1 with a bounded continuation).

## 3. Estimands

- Direct bounded capture in the band: 16.9% (24/142)
- Authorised Haplous capture (executed): 26.5%
- Paired B vs C: {"B_only": 6, "C_only": 0, "both_bounded": 24, "capture_gap_C_minus_B": -0.04225352112676056, "mcnemar": {"b01_B_only": 6, "b10_C_only": 0, "method": "exact_binomial", "n_discordant": 6, "p_value": 0.03125}, "neither": 112}
- Bounded successes destroyed by authorised Haplous: 0; created: 6
- Violation rate among executed - C 78.2%, B 47.8%

## 4. Capture curve

| offset band (mm) | A | B | C | D | n |
|---|---|---|---|---|---|
| 3.00-3.25 | 0% | 16% | 10% | 6% | 67 |
| 3.25-3.50 | 0% | 25% | 23% | 23% | 75 |

## 5. Interaction analysis

Stratified rates, Wilson intervals, paired exact tests. No fitted model.

```json
{
 "deadline_s": {
  "1.8": {
   "B_capture": {
    "ci_high": 0.16818436536845052,
    "ci_low": 0.0,
    "k": 0,
    "method": "wilson95",
    "n": 19,
    "value": 0.0
   },
   "C_capture": {
    "ci_high": 0.16818436536845052,
    "ci_low": 0.0,
    "k": 0,
    "method": "wilson95",
    "n": 19,
    "value": 0.0
   },
   "D_capture": {
    "ci_high": 0.16818436536845052,
    "ci_low": 0.0,
    "k": 0,
    "method": "wilson95",
    "n": 19,
    "value": 0.0
   },
   "n": 19
  },
  "2.0": {
   "B_capture": {
    "ci_high": 0.22810184305529166,
    "ci_low": 0.0,
    "k": 0,
    "method": "wilson95",
    "n": 13,
    "value": 0.0
   },
   "C_capture": {
    "ci_high": 0.22810184305529166,
    "ci_low": 0.0,
    "k": 0,
    "method": "wilson95",
    "n": 13,
    "value": 0.0
   },
   "D_capture": {
    "ci_high": 0.22810184305529166,
    "ci_low": 0.0,
    "k": 0,
    "method": "wilson95",
    "n": 13,
    "value": 0.0
   },
   "n": 13
  },
  "2.5": {
   "B_capture": {
    "ci_high": 0.35433884297520657,
    "ci_low": 0.0,
    "k": 0,
    "method": "wilson95",
    "n": 7,
    "value": 0.0
   },
   "C_capture": {
    "ci_high": 0.35433884297520657,
    "ci_low": 0.0,
    "k": 0,
    "method": "wilson95",
    "n": 7,
    "value": 0.0
   },
   "D_capture": {
    "ci_high": 0.35433884297520657,
    "ci_low": 0.0,
    "k": 0,
    "method": "wilson95",
    "n": 7,
    "value": 0.0
   },
   "n": 7
  },
  "3.0": {
   "B_capture": {
    "ci_high": 0.3244156195108769,
    "ci_low": 0.0,
    "k": 0,
    "method": "wilson95",
    "n": 8,
    "value": 0.0
   },
   "C_capture": {
    "ci_high": 0.3244156195108769,
    "ci_low": 0.0,
    "k": 0,
    "method": "wilson95",
    "n": 8,
    "value": 0.0
   },
   "D_capture": {
    "ci_high": 0.3244156195108769,
    "ci_low": 0.0,
    "k": 0,
    "method": "wilson95",
    "n": 8,
    "value": 0.0
   },
   "n": 8
  },
  "5.0": {
   "B_capture": {
    "ci_high": 0.3244156195108769,
    "ci_low": 0.0,
    "k": 0,
    "method": "wilson95",
    "n": 8,
    "value": 0.0
   },
   "C_capture": {
    "ci_high": 0.3244156195108769,
    "ci_low": 0.0,
    "k": 0,
    "method": "wilson95",
    "n": 8,
    "value": 0.0
   },
   "D_capture": {
    "ci_high": 0.3244156195108769,
    "ci_low": 0.0,
    "k": 0,
    "method": "wilson95",
    "n": 8,
    "value": 0.0
   },
   "n": 8
  },
  "None": {
   "B_capture": {
    "ci_high": 0.44935423242303607,
    "ci_low": 0.2534251109614927,
    "k": 30,
    "method": "wilson95",
    "n": 87,
    "value": 0.3448275862068966
   },
   "C_capture": {
    "ci_high": 0.37773969253288286,
    "ci_low": 0.19294158124476965,
    "k": 24,
    "method": "wilson95",
    "n": 87,
    "value": 0.27586206896551724
   },
   "D_capture": {
    "ci_high": 0.34099180673422297,
    "ci_low": 0.16364043223999153,
    "k": 21,
    "method": "wilson95",
    "n": 87,
    "value": 0.2413793103448276
   },
   "n": 87
  }
 },
 "facet_count": {
  "16": {
   "B_capture": {
    "ci_high": 0.5921482039625402,
    "ci_low": 0.22157377982382145,
    "k": 9,
    "method": "wilson95",
    "n": 23,
    "value": 0.391304347826087
   },
   "C_capture": {
    "ci_high": 0.5511002309310836,
    "ci_low": 0.18811054636982238,
    "k": 8,
    "method": "wilson95",
    "n": 23,
    "value": 0.34782608695652173
   },
   "D_capture": {
    "ci_high": 0.5511002309310836,
    "ci_low": 0.18811054636982238,
    "k": 8,
    "method": "wilson95",
    "n": 23,
    "value": 0.34782608695652173
   },
   "n": 23
  },
  "24": {
   "B_capture": {
    "ci_high": 0.350283620635152,
    "ci_low": 0.09752972019453349,
    "k": 7,
    "method": "wilson95",
    "n": 36,
    "value": 0.19444444444444445
   },
   "C_capture": {
    "ci_high": 0.2865985128039319,
    "ci_low": 0.060817253631150966,
    "k": 5,
    "method": "wilson95",
    "n": 36,
    "value": 0.1388888888888889
   },
   "D_capture": {
    "ci_high": 0.2865985128039319,
    "ci_low": 0.060817253631150966,
    "k": 5,
    "method": "wilson95",
    "n": 36,
    "value": 0.1388888888888889
   },
   "n": 36
  },
  "32": {
   "B_capture": {
    "ci_high": 0.36280603555459856,
    "ci_low": 0.09186883586347633,
    "k": 6,
    "method": "wilson95",
    "n": 31,
    "value": 0.1935483870967742
   },
   "C_capture": {
    "ci_high": 0.36280603555459856,
    "ci_low": 0.09186883586347633,
    "k": 6,
    "method": "wilson95",
    "n": 31,
    "value": 0.1935483870967742
   },
   "D_capture": {
    "ci_high": 0.36280603555459856,
    "ci_low": 0.09186883586347633,
    "k": 6,
    "method": "wilson95",
    "n": 31,
    "value": 0.1935483870967742
   },
   "n": 31
  },
  "48": {
   "B_capture": {
    "ci_high": 0.32479063346829484,
    "ci_low": 0.059158318596448906,
    "k": 4,
    "method": "wilson95",
    "n": 27,
    "value": 0.14814814814814814
   },
   "C_capture": {
    "ci_high": 0.2336997729073062,
    "ci_low": 0.020554221697383523,
    "k": 2,
    "method": "wilson95",
    "n": 27,
    "value": 0.07407407407407407
   },
   "D_capture": {
    "ci_high": 0.2336997729073062,
    "ci_low": 0.020554221697383523,
    "k": 2,
    "method": "wilson95",
    "n": 27,
    "value": 0.07407407407407407
   },
   "n": 27
  },
  "64": {
   "B_capture": {
    "ci_high": 0.34654013980238163,
    "ci_low": 0.06403347608577989,
    "k": 4,
    "method": "wilson95",
    "n": 25,
    "value": 0.16
   },
   "C_capture": {
    "ci_high": 0.2995619218337349,
    "ci_low": 0.04166741357068093,
    "k": 3,
    "method": "wilson95",
    "n": 25,
    "value": 0.12
   },
   "D_capture": {
    "ci_high": 0.13319649395317873,
    "ci_low": 0.0,
    "k": 0,
    "method": "wilson95",
    "n": 25,
    "value": 0.0
   },
   "n": 25
  }
 },
 "max_force_n": {
  "12.0": {
   "B_capture": {
    "ci_high": 0.13319649395317873,
    "ci_low": 0.0,
    "k": 0,
    "method": "wilson95",
    "n": 25,
    "value": 0.0
   },
   "C_capture": {
    "ci_high": 0.13319649395317873,
    "ci_low": 0.0,
    "k": 0,
    "method": "wilson95",
    "n": 25,
    "value": 0.0
   },
   "D_capture": {
    "ci_high": 0.13319649395317873,
    "ci_low": 0.0,
    "k": 0,
    "method": "wilson95",
    "n": 25,
    "value": 0.0
   },
   "n": 25
  },
  "15.0": {
   "B_capture": {
    "ci_high": 0.2679640429809402,
    "ci_low": 0.02417949540723341,
    "k": 2,
    "method": "wilson95",
    "n": 23,
    "value": 0.08695652173913043
   },
   "C_capture": {
    "ci_high": 0.2679640429809402,
    "ci_low": 0.02417949540723341,
    "k": 2,
    "method": "wilson95",
    "n": 23,
    "value": 0.08695652173913043
   },
   "D_capture": {
    "ci_high": 0.2679640429809402,
    "ci_low": 0.02417949540723341,
    "k": 2,
    "method": "wilson95",
    "n": 23,
    "value": 0.08695652173913043
   },
   "n": 23
  },
  "20.0": {
   "B_capture": {
    "ci_high": 0.3788249920651624,
    "ci_low": 0.0373604698913593,
    "k": 2,
    "method": "wilson95",
    "n": 15,
    "value": 0.13333333333333333
   },
   "C_capture": {
    "ci_high": 0.3788249920651624,
    "ci_low": 0.0373604698913593,
    "k": 2,
    "method": "wilson95",
    "n": 15,
    "value": 0.13333333333333333
   },
   "D_capture": {
    "ci_high": 0.3788249920651624,
    "ci_low": 0.0373604698913593,
    "k": 2,
    "method": "wilson95",
    "n": 15,
    "value": 0.13333333333333333
   },
   "n": 15
  },
  "25.0": {
   "B_capture": {
    "ci_high": 0.44803635738467273,
    "ci_low": 0.04696414761482223,
    "k": 2,
    "method": "wilson95",
    "n": 12,
    "value": 0.16666666666666666
   },
   "C_capture": {
    "ci_high": 0.44803635738467273,
    "ci_low": 0.04696414761482223,
    "k": 2,
    "method": "wilson95",
    "n": 12,
    "value": 0.16666666666666666
   },
   "D_capture": {
    "ci_high": 0.44803635738467273,
    "ci_low": 0.04696414761482223,
    "k": 2,
    "method": "wilson95",
    "n": 12,
    "value": 0.16666666666666666
   },
   "n": 12
  },
  "None": {
   "B_capture": {
    "ci_high": 0.47781712305247737,
    "ci_low": 0.2539788838135447,
    "k": 24,
    "method": "wilson95",
    "n": 67,
    "value": 0.3582089552238806
   },
   "C_capture": {
    "ci_high": 0.3851833594127559,
    "ci_low": 0.1772206517897013,
    "k": 18,
    "method": "wilson95",
    "n": 67,
    "value": 0.26865671641791045
   },
   "D_capture": {
    "ci_high": 0.33707192822338217,
    "ci_low": 0.1406360851472927,
    "k": 15,
    "method": "wilson95",
    "n": 67,
    "value": 0.22388059701492538
   },
   "n": 67
  }
 },
 "n_disturbances": {
  "0": {
   "B_capture": {
    "ci_high": 0.27894382620972863,
    "ci_low": 0.1414184699384659,
    "k": 26,
    "method": "wilson95",
    "n": 129,
    "value": 0.20155038759689922
   },
   "C_capture": {
    "ci_high": 0.23607511702681952,
    "ci_low": 0.10900955524451719,
    "k": 21,
    "method": "wilson95",
    "n": 129,
    "value": 0.16279069767441862
   },
   "D_capture": {
    "ci_high": 0.20979865357331362,
    "ci_low": 0.09011944437190833,
    "k": 18,
    "method": "wilson95",
    "n": 129,
    "value": 0.13953488372093023
   },
   "n": 129
  },
  "1": {
   "B_capture": {
    "ci_high": 0.5763111218666236,
    "ci_low": 0.12680497161618087,
    "k": 4,
    "method": "wilson95",
    "n": 13,
    "value": 0.3076923076923077
   },
   "C_capture": {
    "ci_high": 0.5025687399512693,
    "ci_low": 0.08179379092465713,
    "k": 3,
    "method": "wilson95",
    "n": 13,
    "value": 0.23076923076923078
   },
   "D_capture": {
    "ci_high": 0.5025687399512693,
    "ci_low": 0.08179379092465713,
    "k": 3,
    "method": "wilson95",
    "n": 13,
    "value": 0.23076923076923078
   },
   "n": 13
  }
 },
 "n_rejection": {
  "0": {
   "B_capture": {
    "ci_high": 0.3375793952193932,
    "ci_low": 0.17900186747034863,
    "k": 28,
    "method": "wilson95",
    "n": 112,
    "value": 0.25
   },
   "C_capture": {
    "ci_high": 0.2795427852869552,
    "ci_low": 0.13344874797915993,
    "k": 22,
    "method": "wilson95",
    "n": 112,
    "value": 0.19642857142857142
   },
   "D_capture": {
    "ci_high": 0.24981851702314575,
    "ci_low": 0.11137815153115602,
    "k": 19,
    "method": "wilson95",
    "n": 112,
    "value": 0.16964285714285715
   },
   "n": 112
  },
  "1": {
   "B_capture": {
    "ci_high": 0.21323817721072091,
    "ci_low": 0.01847663532769335,
    "k": 2,
    "method": "wilson95",
    "n": 30,
    "value": 0.06666666666666667
   },
   "C_capture": {
    "ci_high": 0.21323817721072091,
    "ci_low": 0.01847663532769335,
    "k": 2,
    "method": "wilson95",
    "n": 30,
    "value": 0.06666666666666667
   },
   "D_capture": {
    "ci_high": 0.21323817721072091,
    "ci_low": 0.01847663532769335,
    "k": 2,
    "method": "wilson95",
    "n": 30,
    "value": 0.06666666666666667
   },
   "n": 30
  }
 },
 "plate_thickness_m": {
  "0.01": {
   "B_capture": {
    "ci_high": 0.31357871567282575,
    "ci_low": 0.09976228838458358,
    "k": 9,
    "method": "wilson95",
    "n": 49,
    "value": 0.1836734693877551
   },
   "C_capture": {
    "ci_high": 0.2666797318916292,
    "ci_low": 0.07096333720544586,
    "k": 7,
    "method": "wilson95",
    "n": 49,
    "value": 0.14285714285714285
   },
   "D_capture": {
    "ci_high": 0.2424466240188096,
    "ci_low": 0.057347477598098245,
    "k": 6,
    "method": "wilson95",
    "n": 49,
    "value": 0.12244897959183673
   },
   "n": 49
  },
  "0.015": {
   "B_capture": {
    "ci_high": 0.42691312900129397,
    "ci_low": 0.16746451821827157,
    "k": 12,
    "method": "wilson95",
    "n": 43,
    "value": 0.27906976744186046
   },
   "C_capture": {
    "ci_high": 0.40238689868254857,
    "ci_low": 0.14929365877065542,
    "k": 11,
    "method": "wilson95",
    "n": 43,
    "value": 0.2558139534883721
   },
   "D_capture": {
    "ci_high": 0.37744808299439414,
    "ci_low": 0.1315353846924483,
    "k": 10,
    "method": "wilson95",
    "n": 43,
    "value": 0.23255813953488372
   },
   "n": 43
  },
  "0.02": {
   "B_capture": {
    "ci_high": 0.3079631945470261,
    "ci_low": 0.0977008273245379,
    "k": 9,
    "method": "wilson95",
    "n": 50,
    "value": 0.18
   },
   "C_capture": {
    "ci_high": 0.23805078886623582,
    "ci_low": 0.05617523710624646,
    "k": 6,
    "method": "wilson95",
    "n": 50,
    "value": 0.12
   },
   "D_capture": {
    "ci_high": 0.2136049033478839,
    "ci_low": 0.04347512399157111,
    "k": 5,
    "method": "wilson95",
    "n": 50,
    "value": 0.1
   },
   "n": 50
  }
 }
}
```

## 6. HES recompute

- Attainable under declared reach cap: 24 / 142
- Attainable under authorised reach: 30 / 142
- Declared reach cap: 0.003 m

## 7. Decision

**Primary: D1 - calibration defect (confidence high)**

max_initial_offset_m is more conservative than the bounded capture envelope.

- D1 (calibration defect, high): max_initial_offset_m is more conservative than the bounded capture envelope
- E1 (benchmark/evaluator defect, moderate): the reachability constant is inconsistent with the offset population it generates

**Highest-information next experiment:** Authorized-reach sweep (Branch B vs Branch C) across the full 0.003-0.005 m offset population to estimate the bounded capture envelope and select a calibrated max_initial_offset_m with a declared safety margin; calibration is against measured physics, not benchmark score.

**Not bundled:** deadline expected_duration_s recalibration; insert_000793 49.7 N mediation divergence
