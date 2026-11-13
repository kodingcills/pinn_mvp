# M4 pre-registration: Haplous vs a competent incumbent vs direct execution

Status: **FROZEN, revision r2** (unit M4.2a fix round; changelog in section 12). M4.2b executes
it unchanged. Development seeds only. No Tier 3 holdout is touched.

config_sha256: `d69d8e626e13ddbac618aa1d7ac6f1f7a7353eb69fc9d929a75b0e7f64755aaa`

## 0. Question

Does Haplous add value over a competent incumbent (G1, `docs/eval_program/designs/G1_incumbent.md`)
and over direct execution, on physical MuJoCo scenarios, and where does it lose? The protocol is
built so that zero or negative Haplous advantage is a reportable outcome. G1 is run as committed
and is not weakened. The analysis below is fixed before any comparative episode is run.

What is already known, and why a stress population is needed: on the 27 committed HEB scenarios,
`haplous_mediated` and `g1_incumbent` give identical dispositions, driven by the same guard
classes. The HEB `population()` stays inside the machine's 0.5 s compliant-hold budget (dropouts
at most 0.2 s, stale windows 0.1 to 0.2 s, rejections at most 0.02 s). Run alone, it can only
re-measure agreement. This protocol keeps it as the control and adds a stress population in the
regions the M4.1 verifier named.

## 1. Binding

- All registered parameters live in `evaluation/m4_campaign.json`. Its SHA-256 is the
  `config_sha256` line above. `scripts/m4_campaign.py` refuses to `run` or `validate` when the
  config's hash is not exactly that value (`registration_problems`).
- The config pins the SHA-256 of every scoring source (HES metric, model, run, gate, regression,
  report, attribution, pareto and integrity modules, `evaluation/stats.py`,
  `evaluation/aggregate.py`, `evaluation/experiment.py`, `benchmarks/heb/metrics.py`, the driver
  and the recompute script) and every system source (the population generators, HEB systems,
  runner, backend and tasks, `benchmarks/incumbent.py`, the recorder, the Haplous runtime,
  capabilities, constraints, MuJoCo adapter and both behaviours). Any drift makes the driver refuse.
  A G1 or Haplous change therefore cannot enter M4.2b silently.
- A change to anything above requires a new revision (recorded in section 12), a new
  scoring-epoch name and a new config hash. Results from different revisions are never pooled.

## 2. Populations

Both are HES population classes (`hes populations`), registered in `hes/populations.py`. The frozen
HEB generator and its classes (`dev@v1`, `regression@v1`, `qualification@v1`) are not modified.

| Population | Class | Generator | Seed | Generated | Excluded before execution | Scheduled |
|---|---|---|---|---|---|---|
| control | `m4_control@v1` | `benchmarks.heb.population.population`, slide fraction 0.28 | 7 (the `dev@v1` seed) | 200 | 19 (slides with a translational disturbance, section 7) | 181 |
| stress | `m4_stress@v1` | `benchmarks.stress_population.stress_population`, slide fraction 0.28 | 1113 | 320 | 0 | 320 |

The control's first 40 scenarios are exactly `dev@v1` (tested). The scheduled counts follow from
scenario parameters alone. They were computed with the exclusion screen before any episode ran.

### 2.1 Stress strata (`benchmarks/stress_population.py`, version v1)

Each scenario carries exactly one stress factor. Strata are assigned round-robin by index, so each
holds exactly 64 scenarios. Everything not listed is the nominal task: insertion from an interior
offset U(0, 2.5 mm) at a uniform bearing, tolerance 0.512 mm, state-age bound 0.02 s, a
non-binding 25 N ceiling, no deadline. Slides use the HEB slide path, 2.0 N ceiling, 0.06 N
floor, no disturbance and no deadline.

| Stratum | Families | Factor and range | Why it can discriminate |
|---|---|---|---|
| `dropout_long` | insertion, slide (p = 0.28) | one dropout window, start U(0.3, 1.0) s, duration U(0.25, 1.0) s | straddles the 0.5 s hold budget; G1 asymmetry 4 (no separate source-gap detection) |
| `stale_long` | insertion, slide | one stale window (age 0.05 s), start U(0.3, 1.0) s, duration U(0.25, 1.0) s | straddles the hold budget; hold entry and revalidation differ |
| `rejection_long` | insertion, slide | one command-rejection window, start U(0.3, 1.0) s, duration U(0.25, 1.0) s | straddles the hold budget; G1 asymmetry 5 (rejection hold entry after 3 vs every tick) |
| `tilt_limit` | insertion | hard tilt limit, log-uniform on [1e-5, 1e-3] deg | spans the measured nominal peak (8.9e-5 deg); the tilt guard never acts on HEB |
| `ceiling_retry` | insertion | hard ceiling U(1.0, 4.0) N; process-force recovery permitted; budget in {1, 2, 4, 6}; settle in {0.1, 0.3, 0.5} s | forced ceiling trips with an authorized, budgeted retry; HEB exercises retries only on mesh-excluded slides |

Exclusions are designed in. No slide carries a translational disturbance. Every force limit is
below the 100 N sensed ceiling (G1 asymmetry 2). Every tilt limit is below the 45 deg sensed range,
and every tolerance and state-age bound is above the machine's position resolution and state
period (G1 asymmetry 3). The screen in section 7 still runs and found 0 stress exclusions.

**Not included: adapter machines (G1 asymmetry 1).** No behaviour in this repository authorizes a
command-mode substitute (`authorized_substitutes` is empty for insertion and sliding) and no
verified adapter exists. On any adapter machine OD-1 and G1 both refuse, so the stratum would
re-measure agreement by construction. Making it discriminating needs a behaviour change in
`haplous/` and a real adapter, both outside M4.2. Adapter machines are untested in M4 (section 11).

### 2.2 Sample sizes

The unit is the scenario. Physics is deterministic, so each (scenario, arm) is one episode.

- **Stress, C1.** The paired difference in bounded success takes values d in {-1, 0, 1} per
  scenario, with variance at most the discordant fraction p. For a 95% half-width of 0.05 at
  p = 0.20, n >= 1.96^2 * 0.20 / 0.05^2 = 307. We use 320, 64 per stratum. At p = 0.40 the
  half-width is 0.069. Per-stratum comparisons (n = 64, half-width about 0.11 at p = 0.20) are
  exploratory.
- **Control, C2.** The control is where guards are expected to agree, so its job is to bound
  regressions. With 0 harmed scenarios in 181, the 95% upper bound on the harm rate is
  3/181 = 1.7% (rule of three). At p = 0.05 the half-width is 0.033.
- **CPU cost.** A pre-registration timing probe on 3 `dev@v1` scenarios (process time, one
  realization each, outcomes not read) measured 0.6 to 3.9 s for `haplous_mediated`, 0.6 to 7.7 s
  for `g1_incumbent`, 0.1 to 0.4 s for `competent_supervisor`, and 5 to 52 s for `bare_behavior`,
  which runs to the horizon when it does not complete. The dry run (`results/m4/dryrun/cost.json`)
  re-measures cost per episode and projects the campaign. Sizes are not reduced for cost. A long
  campaign resumes from completed shards.

## 3. Arms

Every arm runs on the same scenario list. `benchmarks.heb.runner.evaluate` builds one machine
realization per scenario (physics, measurement corruption, fault schedule) and hands it to every
arm, so task, initial state, physics, machine and observation channel are shared.

| Arm | Role | Why |
|---|---|---|
| `bare_behavior` | direct execution (HES baseline) | the same behaviour and task success condition over the live session, with no guards. Chosen over `direct_minimal`, which never evaluates success and always runs to the horizon, so its time comparison is confounded (HES records that caveat). HES selects `bare_behavior` as baseline when `direct_minimal` is absent; the driver checks it |
| `g1_incumbent` | competent incumbent | `benchmarks/incumbent.py` as committed (M4.1) |
| `haplous_mediated` | candidate | `benchmarks.heb.systems.HaplousSystem` |
| `competent_supervisor` | historical reference, labelled | the HEB supervisor behind historical HES epochs. Not a competent incumbent (G1 design section 2). Never the reference of a confirmatory comparison |

The disclosed G1 asymmetries (G1 design section 6) apply. Evidence quality (asymmetry 6) is not
an outcome axis here and is not scored against G1.

## 4. Estimands and metrics

Computed per population and per arm by `population_metrics` in `scripts/m4_campaign.py`. HES
functions are used wherever HES has the metric. The supplemental estimands are the ones HES does
not have. All proportions carry Wilson 95% intervals (`hes.metrics._prop`). "Scheduled" means
every scenario left after section 7, whatever the arm did with it.

| Estimand | Source | Numerator | Denominator |
|---|---|---|---|
| task success | `hes.metrics.system_metrics` | episodes graded `task_success` | all scheduled |
| bounded success | `hes.metrics.system_metrics` | task success with no graded hard violation | all scheduled |
| acceptance coverage | `hes.metrics.system_metrics` | episodes not REFUSED | all scheduled |
| violation given acceptance | `hes.metrics.authority_metrics` | accepted episodes with at least one hard violation | accepted episodes |
| false refusal | `hes.metrics.authority_metrics` with `hes.run.Run.attainability` | REFUSED on a scenario demonstrated attainable | scheduled scenarios demonstrated attainable |
| refusals with attainability UNKNOWN | supplemental, over `Run.attainability` | REFUSED on a scenario no arm completed within bounds | reported as a count next to the false-refusal rate |
| non-interference | `hes.metrics.noninterference_metrics` vs `bare_behavior` | preserved bounded successes; refused ones (unnecessary intervention) | scenarios where `bare_behavior` reached bounded success |
| recovery | supplemental | bounded successes among scenarios tagged `recovery_authorized` | all scheduled scenarios tagged `recovery_authorized` (stress `ceiling_retry`; control has none after section 7) |
| total time per accepted task | supplemental | simulated seconds of every executed episode (success, failure, holds and waits included; a refusal executes 0 s) + reset_s per executed episode | bounded successes. Reported at reset_s = 1.0 s (primary, an assumption: the substrate models no reset) and 0.0 s (sensitivity), side by side |
| intervention overhead | supplemental | median over scenarios of (arm simulated duration minus `bare_behavior` duration) | scenarios where both reached bounded success; plus HES unnecessary-intervention rate above. Both episodes are executed, so reset_s cancels: the value is the same at 1.0 s and 0.0 s, and the report says so |
| paired wins and regressions | supplemental | per scenario and per comparison: rescued (candidate bounded, reference not), harmed (reference bounded, candidate not), both, neither, with scenario ids | all scheduled scenarios |
| HES hard gates | `hes.gates.evaluate_hard_gates` via `hes.bundle.build_bundle` | as HES defines them (G3 floor 0.95, G4 ceiling 0.20) | reported, never overridden |

"Accepted task" means bounded success: a task outcome the customer can accept. A simulated
episode duration is the last observation's source time (`seating_time_s`), never host time.
HES's `completion_time_tax_s` and timing block use host `wall_s` and are compute time, not task
time. They are reported by HES and are not official M4 metrics.

Comparisons (paired, every scenario): `haplous_mediated` vs `g1_incumbent`,
`haplous_mediated` vs `bare_behavior`, `g1_incumbent` vs `bare_behavior`,
`haplous_mediated` vs `competent_supervisor`. Exploratory slices: stress strata, and the task
family for the control.

HES intervention-quality metrics need counterfactual forks, which this campaign does not run.
HES reports them as `NOT_AVAILABLE`.

## 5. Statistics

- Resampling unit: the scenario (one shared realization, one episode per arm). There is no finer
  cluster, and no interval is shown at a finer level.
- Proportions: Wilson 95%.
- Paired differences in bounded success: scenario-paired percentile bootstrap, 10 000 resamples,
  seed 20261113 (`hes.regression.paired_rate_delta`). Pairing is checked before use: exactly one
  row per (scenario, arm), else the driver stops (`check_aligned`). This closes the open
  `paired_rate_delta` duplicate-collapse issue for this campaign.
- **Confirmatory (two, each its own question, no family-wise claim):**
  - **C1.** Stress population. Bounded success, `haplous_mediated` minus `g1_incumbent`.
  - **C2.** Control population. Bounded success, `haplous_mediated` minus `g1_incumbent`.
- Everything else is exploratory: every other comparison, every stratum, every other metric. It is
  reported with intervals and labelled exploratory. No p-value hunting and no subgroup claims.

## 6. Decision rules

For C1 and C2, from the 95% interval [lo, hi] of Haplous minus G1 and the equivalence margin
0.05, the rules below are applied in order and the first that matches is the verdict
(`decide` in `scripts/m4_campaign.py`, mirrored by `decision` in `scripts/m4_recompute.py`):

1. lo > 0: "Haplous better". If also hi <= 0.05, append "(difference within the 0.05
   equivalence margin: practically equivalent)".
2. hi < 0: "G1 better". If also lo >= -0.05, append the same margin note.
3. -0.05 <= lo and hi <= 0.05: "tie (equivalent within 0.05)".
4. Otherwise: "inconclusive at this sample size".

Exactly one rule applies to any interval. For example, [0.002, 0.02] is "Haplous better (difference
within the 0.05 equivalence margin: practically equivalent)", not a tie.

- A G1 win and a tie are reported as such, in the headline, with the same prominence as a
  Haplous win.
- Every harmed scenario is listed by id, whatever the headline. A positive C1 with harmed
  scenarios is reported as "advantage with regressions", never as a plain advantage.
- A failed HES hard gate stays failed. The known `non_interference` failure of the committed v3
  qualification is a different epoch and is neither cited as M4 evidence nor hidden.
- If G1 wins, Haplous is not retuned against these scenarios (program section 10). Any follow-up
  is a new revision on new seeds.

## 7. Exclusions and stopping

Exclusions are decided from scenario parameters before execution (`exclusion_reasons`), recorded
per scenario in each run's `schedule.json`, and never added after outcomes are seen:

1. `slide_disturbance_no_recovery` and `slide_disturbance_recoverable_ceiling` by id (mesh
   artifact, KNOWN_ISSUES).
2. Any slide with a translational disturbance: the same 16-tile facet-tap mechanism makes its
   outcome a mesh artifact (KNOWN_ISSUES). A rotational impulse is kept.
3. Any scenario that Haplous negotiation refuses with a code G1 does not implement
   (`missing_force_measurement`, `force_floor_unsensed`, `missing_tilt_observation`,
   `tilt_requirement_unsensed`, `missing_pose_observation`, `requested_tolerance_unachievable`,
   `state_too_stale`; G1 asymmetry 3). The screen calls `haplous.capabilities.negotiate` on the
   intent only. Nothing is executed.

For each population and each rule, `official_metrics.json` (`exclusions`) and the report list the
count and the excluded scenario ids, zero counts included. A scenario that matches several rules is
listed under each.

Stopping: no interim analysis and no early stop for efficacy or futility. Every shard runs. A
failed shard is retried up to 3 attempts. If any shard is still incomplete, the campaign is
reported INCOMPLETE with the shard listed, and no metric is claimed. Partial metrics are not
inspected.

## 8. Scoring epoch

Epoch `hes-m4-e2`, new for this campaign (r1 used `hes-m4-e1`; nothing scored under it is kept). It is not poolable with HES v0
(`docs/HES_V0_REPORT.md`, stale on main) or any earlier epoch. Its scoring and system sources are
pinned by hash in the config (section 1). Each population's HES report
(`results/m4/<phase>/<population>/HES_REPORT.md`) is committed. `validate` rebuilds the HES bundle
from the M0-sealed rows and checks it against that committed file (`hes.report.check_report`),
never against a file it rendered itself in the same step.

## 9. Evidence

- **Rows.** Canonical episode rows are written by `evaluation.experiment.LocalExecutor` inside an
  M0 run per population (`m4-<phase>-<population>-episodes`). Each row carries
  `metadata = {population_id, phase, scoring_epoch, config_sha256, m0_run_id}`, the digest of the
  commands the machine applied (`task_metrics.applied_commands_sha256`) and its CPU seconds.
  `results/m4/<phase>/raw_rows.jsonl` holds every row of every population.
- **Recordings.** Recorded with the M2 recorder (`benchmarks.recording`), each as its own M0 run
  (`m4-<phase>-<population>-<scenario>-<arm>`), both arms of a pair. Selected: every harmed pair,
  then every rescued pair, for `haplous_mediated` vs `g1_incumbent`, then vs `bare_behavior`.
  Within a class the order is sha256(scenario id). Cap: 25 pairs per population (about 140 MB at
  the measured ~2.8 MB per recording). If the cap binds, the unrecorded pairs are listed in the
  report. The dry run adds one pipeline-probe pair per population.
- **Recording storage.** Bulk recordings are sealed into a gitignored M0 registry,
  `results/m4/recordings_store/<phase>/m0`, and are not committed. Per population, the first
  harmed and the first rescued `haplous_mediated` vs `g1_incumbent` pair in selection order whose
  recordings are each under 5 MB is sealed a second time into the committed registry
  `results/m4/<phase>/m0` as an exemplar (at most 2 pairs per population, none if the class is
  empty).
- **Linkage.** `replay_index.jsonl` (committed) maps (population, scenario, arm) to the
  recording's M0 run id, the episodes' M0 run, the registry holding it, the exemplar registry if
  any, the SHA-256 and host-clock-free SHA-256 of every artifact the recorder wrote, the exact
  regeneration command and the replay command. Episodes are deterministic; the recorder's host
  clock (`host_monotonic_ns` in `state.arrow` and `telemetry.jsonl`) is the only field that
  differs between regenerations, so regeneration is compared on the host-clock-free digests.
- **Regeneration.** When a bulk recording is absent from the store (any fresh checkout),
  `validate` regenerates it into the store through M0 and compares its digests with the index. An
  absent recording is not a failure; a digest mismatch is
  (`test_regenerated_recording_with_a_digest_mismatch_fails_validation`).
- **Provenance.** M0 manifests and the executor's `provenance.json` compute `git_dirty` with
  `results/m4` excluded (the driver's own output roots, the store included), as M0 excludes its
  registry root. A run from a clean tree records `git_dirty: false`.
- **Score/trace check.** For every recording, `validate` compares the scored row with the
  M0-sealed recording: disposition, graded disposition, task and bounded success, reason, refusal
  codes, violation count, and the digest of the commands the machine applied (the A/A on
  machine-received commands that the `_Tap` wrapper cannot give). A mismatch fails validation
  (`test_planted_score_trace_mismatch_fails_validation`, which simulates a forger who re-sealed M0).
- **Independent recompute.** `scripts/m4_recompute.py` recomputes every official metric from
  `raw_rows.jsonl` and the config without importing `hes`, `evaluation` or `benchmarks` (it checks
  this at exit). `validate` requires agreement to 1e-9. A planted row change makes them disagree
  (tested).

## 10. Execution (M4.2b)

```bash
MUJOCO_GL=osmesa PYTHONPATH=. .venv/bin/python scripts/m4_campaign.py run --phase campaign --workers 4
MUJOCO_GL=osmesa PYTHONPATH=. .venv/bin/python scripts/m4_campaign.py validate --phase campaign
```

The report states C1 and C2 per section 6, all exploratory metrics with intervals, every time
metric at reset_s 1.0 s and 0.0 s, the exclusion count and ids per population and rule (section
7), every harmed and rescued scenario with its replay link, the HES gates, and the disclosed
asymmetries.

## 11. Scope limits

1. Adapter machines (G1 asymmetry 1) are untested in M4 (section 2.1). Testing them needs a
   behaviour that authorizes a substitute and a verified adapter. M4 makes no claim about them.
2. reset_s = 1.0 s is an assumption, reported with the 0.0 s sensitivity case in every time
   metric. A measured reset time for a real cell would replace it in a new revision.
3. Recordings are stored per section 9 (bulk outside git, regenerable from the committed index;
   exemplars committed).

## 12. Changelog

- **r2** (M4.2a fix round). Changed after a dry run of 4 scenarios (2 per population) whose
  outcomes were not interpreted. Section 6: overlapping decision table replaced by ordered,
  first-match-wins rules with a margin note, implemented in the driver and the recompute. Section
  7: exclusion count and ids per population and rule reported. Section 9: provenance ignores the
  driver's output roots; bulk recordings move to a gitignored store with a committed digest index,
  regeneration in `validate`, and at most one harmed and one rescued exemplar per population
  committed. Section 4: reset sensitivity reported in every time metric. Section 11: adapter
  machines stated as untested, operator questions closed. Scoring epoch renamed `hes-m4-e2`;
  the driver and recompute hashes and the config hash are updated (r1 config hash
  66bb60c60158e5dde26382ef489989f4ea3af1ac42c8f8fd921a89639ebce2da). Populations, arms, sample
  sizes, estimands, statistics and exclusion rules are unchanged.
- **r1** (M4.2a). First frozen version.
