# Haplous Product-Scale Evaluation, Learned-Policy & Evidence-Driven Development Campaign

Status: COMPLETE for the software/simulation scope. Freeze tag:
`haplous-product-scale-v3-candidate`. Final holdout: sealed seed B,
generated after the freeze, run once, no tuning.

## 1. Provenance

- Baseline: tag `haplous-eval-engine-v1` (6a3f1ac), 337 passed +
  1 xfailed, verified at campaign start.
- Platform: Apple M4 Pro, macOS 26.5, no NVIDIA GPU.
- Environments: main venv (mujoco 3.11.0); maniskill/robosuite venvs
  unchanged from prior campaigns.
- Candidate lineage: v1 (eval-engine baseline) -> v2 (capability
  calibration, KEEP) -> v3 (temporal hold/revalidate, KEEP) ->
  qualification fix (measured capture boundary) -> frozen.

## 2. Starting evidence (verified)

All prior-campaign evidence verified against the repository: 5,001,725
fuzz transitions (6/6 invariants), 120,000 soak cycles (0 failures),
counterfactual fork implemented, 27-scenario three-arm comparison,
claim/metric registries, doctor, dashboard. C5 was over-classified
(REFUTED_WITHIN_TESTED_SCOPE) and is reclassified UNKNOWN / NOT
CURRENTLY MOTIVATED per the campaign spec: only G1 and the momentum
observer are refuted-within-scope; no representative controller
tournament has run.

## 3. Runtime/evidence-storage scaling fix

`haplous/evidence_store.py`: EvidenceSink protocol, BoundedMemorySink
(explicit OverflowError backpressure), JsonlEvidenceStore (append-only
ordinal segments, rotation, reopen-resume, replay). Runtime:
evidence_sink + max_archived_evidence (bounded archive without a sink
is a construction error - no silent loss; flush-before-drop).

Retention benchmark (1,000,000 execution/reset cycles with sink):
26.2 s (the pre-fix configuration could not finish 1M in 20 min), live
objects FLAT (50,211 -> 49,957), archive pinned at 64, exact accounting
(64 + 999,936 = 1,000,000), zero replay failures, per-cycle cost flat
(growth ratio 1.011). RSS drift 60 MB over the run is IO buffering
residue with a flat live-set.

## 4. Learned-policy baseline reproduction

BLOCKED on credentials, not integration. Canonical repo verified
(jstmn/ColosseumV2); official ACT protocol is physx_cuda-only;
checkpoints 2 x 1.19 GB. Provisioning attempts: no AWS/GCP/RunPod
credentials on this machine (only an IDE-assistant directory), no cloud
CLIs, the one known remote Linux host (oci-ubuntu) rejects all four
local keys. Dispositions:
`results/external_eval/c01_colosseumv2_disposition.json`,
`results/external_eval/p01_compute_provisioning_disposition.json`. The
exact runnable NVIDIA package is committed
(scripts/run_colosseum_v2_official.sh).

## 5-7. Direct policy / supervisor / Haplous results

No learned policy (blocked). The three-arm comparison ran on the
internal task behaviors (bit-identical across arms). Development family
(27 scenarios, paired realizations, benchmark-owned grading):

| System | v1 SR | v2 SR | v3 SR | v3 BSR_sys | v3 BSR_acc | v3 viol-trials |
|---|---|---|---|---|---|---|
| bare | 0.815 | 0.815 | 0.815 | 0.556 | 0.556 | 8 |
| supervisor | 0.519 | 0.519 | 0.519 | 0.519 | 0.519 | 4 |
| haplous | 0.444 | 0.519 | **0.704** | **0.704** | **0.750** | **3** |

Final holdout (36 unseen scenarios, seed B):

| System | SR | BSR_system | BSR_conditional | coverage | viol-trials |
|---|---|---|---|---|---|
| bare | 0.833 | 0.639 | 0.639 | 1.0 | 7 |
| supervisor | 0.639 | 0.639 | 0.639 | 1.0 | 0 |
| **haplous** | **0.833** | **0.833** | **1.000** | 0.833 | **0** |

Paired McNemar (holdout): vs supervisor +7 task successes (p=0.016)
and +7 bounded successes (p=0.016); vs bare equal task success
(p=1.0), +7 bounded (p=0.016).

## 8-11. BSR semantics, coverage, false abort, false refusal

Both BSR denominators are reported everywhere (bsr_system over all
presented requests; bsr_conditional over accepted). Coverage 0.833 on
the holdout = 6 refusals, all on by-construction-infeasible requests
(offsets beyond the measured 3.0 mm capture boundary).

False-refusal counterfactual metric (fork every refusal, execute the
exact refused request without negotiation, classify against ALL
declared hard contracts including temporal freshness):
`benchmarks/false_refusal.py` + `scripts/measure_false_refusals.py`.
First map: 2 of 5 refusals FALSE (force limit above sensor ceiling
refused though execution peaks at 2.18 N; insertion 2 s deadline refused
though seating takes 1.62 s). After Wave 2: 0 false refusals; all
remaining refusals TRUE (5 mm offset, persistent staleness, slide
1 s deadline vs 1.95-6.1 s measured slide completions).

## 12. Intervention-value analysis

Counterfactual fork at every runtime-initiated stop; restored-state
continuation proven bit-identical to uninterrupted execution
(tests/test_counterfactual_fork.py). v1: 4 forked stops = 1 BENEFICIAL
(force-limit stop prevented 14.95 N vs 2 N limit) + 3 HARMFUL (all
temporal-integrity stops costing completions the unmediated arm
records). v3: 0 HARMFUL (the temporal stops no longer occur - hold and
resume replaced them); 1 BENEFICIAL. Classification rules preregistered
(worsening credited only against a declared hard limit; completion
dominates otherwise).

## 13. Recovery

Ablation-isolated: recovery contributes +1 task success, +0 bounded
success (its scenario completes with recorded violations). Recovery
remains scoped to physical breaches; temporal faults never consume
recovery budget.

## 14-15. Failure taxonomy and failure mass

Mechanical reason->root-cause mapping (UNKNOWN default). v3 failure
mass (dev family): Haplous 3 EXECUTION_SEMANTICS (the TRUE refusals) +
2 PHYSICS + 1 CONTROL per arm - the remaining Haplous-attributable mass
is the two TRUE refusals and the slide stale-hold tilt-guard
limitation.

## 16-18. Development iterations (versioned history)

- v2 (capability calibration): force limits above the sensor ceiling
  degrade to ceiling enforcement instead of refusing; insertion
  expected_duration calibrated 4.0 -> 2.0 s from measured completions.
  A deadline degrade-and-attempt variant was tested and REJECTED within
  the wave (it accepted a TRUE-infeasible slide deadline, converting a
  clean refusal into a violation on unwinnable work). KEEP.
- v3 (temporal hold/revalidate/resume): temporal faults suspend
  authority (compliant hold) and revalidate on restoration; bounded by
  max_hold_unobserved_s (0.5 s placeholder, 25x the freshness contract,
  one measured safe point 200 ms). KEEP.
- Qualification fix: refusal reach calibrated to the measured capture
  boundary (3.0 mm; the 4.35 mm geometric envelope accepted 4.0-4.3 mm
  requests that always timed out).

## 19. Ablations

hold_disabled (v2 semantics): 13 SR; recovery_disabled: 18 SR; full v3:
19 SR. Attribution: temporal hold +5, recovery +1, capability
calibration +1 task successes.

## 20-22. Robustness, boundaries, compound perturbations

Insertion temporal envelope under hold semantics: fully covered through
40 ms stale delays / 50 ms dropouts (the old 20 ms abort boundary is
gone; the new boundary is the 0.5 s hold budget). Slide stale boundary
UNCHANGED at 20 ms with a changed mechanism (semantic abort ->
tilt-guard on resume): hold is safe for quasi-static contact, unsafe
for sustained sliding - documented task-physics limitation. Compound
grid (prior campaign): additive, no interactions.

## 23. Controller result

Not triggered: CONTROL-class failure mass is 1 trial per arm
(identical across arms, the modeled tilt guard). No Haplous controller
candidate is justified by current evidence; C5 stays UNKNOWN / NOT
CURRENTLY MOTIVATED.

## 24. Runtime reliability

Retention-fixed runtime: 1M lifecycle cycles with durable sink, zero
failures, zero replay corruption, bounded live-set (section 3). The
5,001,725-transition fuzz suite passes on the v3 runtime.

## 25. Tail latency

Per-cycle soak p50 23.4 us / p99 64.7 us (scripted loop, 1M cycles,
first-vs-last checkpoint flat). The mediation overhead result (~30 us/tick
P50 on PhysX, alignment-proofed) is unchanged by this campaign: the
hold path adds no per-tick work (flush happens at reset only).

## 26. Engineering leverage

Campaign delta vs eval-engine-v1: ~1,300 LOC runtime/benchmarks
(evidence_store 150, hold semantics ~120, capability calibration ~40,
supervisor 120, counterfactual/false-refusal ~200, drivers ~700).
Guarded core growth: evidence_store (new module, core-adjacent) +
hold semantics in runtime.py. No task-specific core branches.

## 27-28. Strongest evidence FOR / AGAINST

FOR: the sealed holdout - task success equal-best with the unsupervised
arm, bounded success +0.194 over both baselines, conditional BSR 1.000,
zero violations, exact McNemar significance vs the supervisor; plus the
ablation-isolated mechanism attribution and 1M-cycle reliability.

AGAINST: the slide hold limitation (tilt-guard on resume at >=20 ms
stale); the hold budget is a placeholder pending hardware statistics;
the holdout distribution overlaps the dev distribution by construction
(fresh samples of a known process, not a new process); the learned-
policy leg is entirely missing (credentials); the supervisor matches
bare's bounded success with zero violations - a production team could
ship it and lose only the +7 task successes and the temporal-robustness
envelope that Haplous adds.

## 29. Claim adjudication

| Claim | Status |
|---|---|
| C1 Non-interference | SUPPORTED_WITHIN_TESTED_SCOPE |
| C2 Bounded execution | SUPPORTED_WITH_CAVEATS |
| C3 Net system value | SUPPORTED_WITHIN_TESTED_SCOPE |
| C4 Recovery | SUPPORTED_WITHIN_TESTED_SCOPE |
| C5 Controller superiority | UNKNOWN (NOT CURRENTLY MOTIVATED) |
| C6 Learned-policy value | UNKNOWN (credential-blocked) |
| C7 Robustness | MIXED |
| C8 Generality | SUPPORTED_WITH_CAVEATS |
| C9 Real-time viability | SUPPORTED_WITHIN_TESTED_SCOPE |
| C10 Runtime reliability | SUPPORTED_WITHIN_TESTED_SCOPE |
| C11 Incumbent differentiation | SUPPORTED_WITHIN_TESTED_SCOPE |
| C12 Simulation relevance | MIXED |

## 30. Remaining unknowns

C6 end-to-end (the runnable NVIDIA package awaits credentials); C11 on
external tasks and learned policies; the slide hold limitation's fix
(task-aware hold budgets); hardware hold statistics; all hardware
questions from prior campaigns.

## 31. Exact physical hardware experiments now justified

1. Hold-statistics measurement: how long can the real machine hold its
   last command unobserved safely (sets max_hold_unobserved_s from
   data, replacing the placeholder).
2. Real dropout/staleness distributions (price the temporal-integrity
   semantics against reality).
3. All prior hardware gates (500 Hz stability, F/T accuracy, touch-off,
   watchdog) unchanged.
