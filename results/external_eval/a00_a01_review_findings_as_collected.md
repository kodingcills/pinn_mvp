# A00/A01 review findings as collected

Provenance: the two adversarial reviews ran as oracle background subagents.
The harness did not persist their verbatim transcripts; this artifact records
the findings AS COLLECTED in-session, at the granularity stated in the
committed adjudication
(`results/external_eval/a00_a01_review_adjudication.json`). No check-number
invention: findings are listed under the defect IDs they produced. Raw
first-pass evidence artifacts remain committed as superseded.

## A00 independent review (13m27s, checklist verdicts)

| Verdict | Finding as collected | Defect |
|---|---|---|
| FAIL | bounded-grading asymmetry: direct used 3D force norm + tilt from task telemetry; haplous used runtime axial-only violation events, omitting tilt and norm; dev ep4 marked bounded at 146.509 N peak | D1-metric-asymmetry |
| FAIL | stopping-semantics asymmetry: SuccessCondition.window_s is observation retention, not persistence; mediated arm stopped on first success while direct arm waited extra steps via adapter latches; post-success excursions and tilt/wall differences were stopping artifacts | D2-stopping-asymmetry |
| CONCERN | latency residual computation misaligned tick spans (negative residuals proved it); pooling by p50-of-p50 invalid | D3-latency-misalignment |
| NOTE | x01 wording "caps it" overstates sampled enforcement | D6-x01-wording |

## A01 red team (15m33s, attack-vector verdicts)

| Verdict | Attack as collected | Defect |
|---|---|---|
| BROKEN | stopping window: post-success excursion gap (16/16 vs 3-4/16 bounded) was stopping-semantics artifact, not supervision value; arms executed different numbers of steps | D2-stopping-asymmetry |
| BROKEN | bounded-success metric formula differed per arm; dev ep4 146.5 N same-step impulse graded bounded by one formula, breached by the other | D1-metric-asymmetry |
| BROKEN | "1-2 orders of magnitude peak-force reduction" compared maxima from different seeds and different episode lengths; no exposure-matched ratio | D4-force-ratio-overclaim |
| BROKEN | latency pairing misalignment: gap[i] pairs with steps[i+1], not steps[i]; unmatched setup samples in observation arrays | D3-latency-misalignment |
| WEAK | cohort naming: "unseen episodes 23-38" were conversion-success-qualified, not outcome-blind | D5-cohort-naming |

## Adjudication outcome (accepted all, rejected none)

- D1: both drivers grade bounded_success identically (recorded 3D-norm peak <= 25 N AND recorded tilt <= 30 deg). Verified: M03v2 rerun ep4 bounded=false in BOTH arms; dev 15/16 both.
- D2: both direct loops end on first benchmark-success observation. Verified: step parity 16/16 (M03v2), 16/16 (G02v2), 10/10 (Lift dev), 10/10 (Lift final); per-pair peaks bit-identical.
- D3: alignment-proof per-tick non-physics time, raw rows committed (L00v3: 0 negative samples in 1412 pooled ticks; direct P50 92.5 us vs mediated 122.3 us; overhead ~30 us/tick P50, <0.1% of 50 ms period).
- D4: no ratio claimed; bounded termination vs unbounded continuation is the demonstrated property.
- D5: cohort renamed "conversion-success-qualified holdout"; sampling rule stated in report section 3.
- D6: wording changed to "aborts earlier and reduces exposure" (report section 5).

## Withdrawn headline claims (3)

1. 16/16 vs 3-4/16 bounded-success gap as supervision value (was stopping-semantics artifact; harmonized reruns show 15/16 vs 15/16 dev and 16/16 vs 16/16 final).
2. runtime-residual latency figures from the first-pass misaligned computation.
3. "terminates at the bound" / "1-2 orders of magnitude peak-force reduction" wordings.

## Surviving headline claims (3)

1. Non-interference under harmonized stopping + grading: bit-identical per-pair peaks, identical step counts (32/32 ManiSkill, 20/20 Lift), identical success/completion.
2. ~30 us/tick P50 mediation overhead (<0.1% of 50 ms period), alignment-proof instrumentation, 0 negative samples in 1412 pooled ticks.
3. Bounded termination vs unbounded continuation on failing streams (no exposure-matched ratio claimed).

## Rerun artifacts

- maniskill dev v2: `results/external_eval/maniskill-3.0.1/20260910T064011Z`
- maniskill final v2: `results/external_eval/maniskill-3.0.1/20260910T064040Z`
- maniskill latency v3: `results/external_eval/maniskill-3.0.1/20260910T064409Z`
- lift dev v2: `results/external_eval/robosuite-1.5.2/lift-20260910T064020Z`
- lift final v2: `results/external_eval/robosuite-1.5.2/lift-20260910T064025Z`
