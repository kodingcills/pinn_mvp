# M4 addendum: simulator divergence (issue #21)

This addendum does not change `M4_REPORT.md`, `results/m4/campaign/raw_rows.jsonl` or any other sealed
M4 file. Those stay under scoring epoch `hes-m4-e2`. This file re-scores the affected rows under a new
scoring version and reports a sensitivity analysis. The data is in `results/m4_divergence_rescore/`
(`summary.json` and `rescored_rows.jsonl`). The driver is `scripts/m4_divergence_rescore.py`.

## Defect

MuJoCo sometimes finds a NaN, Inf or huge value in qpos, qvel or qacc. When that happens, `mj_step`
prints a warning and silently resets `MjData` to the model's default state. HEB 0.2.0 did not notice the
reset. The episode carried on from the reset pose and was graded like any other episode. Example: in
`m4s_stale_long_00116`, arm `g1_incumbent`, the simulation diverged at t=0.759 s and the episode was
still graded COMPLETE with `task_success=True`. The verdicts from such episodes measure the simulator's
reset, not the system under test.

## Scoring version and impact note

**New scoring version.** HEB `0.3.0` (`benchmarks/heb/__init__.py`), scoring epoch `hes-m4-e3-simdiv`.
It must never be pooled with `hes-m4-e2` or any earlier epoch.

**Detection.** The HEB machine is `benchmarks/heb/divergence.py:DivergenceGuardedMachine`, the default
factory of `mujoco_backend.build_realization` and the base class of the recorder's machine. After every
session step it reads two things: MuJoCo's reset warning counters (`mjWARN_BADQPOS`, `mjWARN_BADQVEL`,
`mjWARN_BADQACC`) and the sim clock. If a counter goes up or sim time goes backwards, the guard stops
the session and reports fault `sim_diverged`. Every HEB system copies the machine's record into
`SystemRun.detail["sim_diverged"]`. `grade_run` then grades the trial `SIM_DIVERGED`, with no task
success and no bounded success, whatever the system reported.

**Taxonomy.** A `SIM_DIVERGED` row gets root cause `SIMULATOR` (`evaluation/taxonomy.py`) and primary
cause `INFRASTRUCTURE` in HES attribution (`hes/attribution.py`). Both are keyed on the benchmark's
grade, not on the system's own reason. Studio shows `SIM_DIVERGED` as a recording's disposition.

**Impact.** Before the divergence step, the guard writes no physics state, so a trial that never
diverges is unchanged. Two measurements show this:

- On the standard HEB insertion and slide scenarios (4 systems, 108 trials), the trials are identical
  to `1aefc64` in disposition, reason, graded outcome, violations, task metrics and the observation
  stream digest. See `results/heb_divergence/parity.json`.
- Of the 392 re-executed M4 episodes that did not diverge, all 392 reproduce their sealed outcome and
  their applied-command digest (`summary.json`, `reproduction`).

Only a trial that diverges can score differently, and it can only lose a success.

## Re-score method

The sealed rows record no per-episode divergence evidence, so a re-score needs the episodes to be
re-executed. Re-running all 501 scenarios × 4 arms would take about 3.2 CPU-hours (`cost.json`). The
re-score instead re-executes, with the guard on, through the M4 executor
(`m4_campaign.evaluate_with_digest`):

- **Proxy-flagged scenarios, all 4 arms.** A scenario is flagged when any of its rows has
  `max_tilt_deg > 90` or hard-violation times that go backwards. There are 76 such scenarios: 57
  stress and 19 control.
- **A seeded audit, 2 confirmatory arms only.** 30 proxy-clean scenarios per population (seed 21),
  run for `haplous_mediated` and `g1_incumbent`. The audit bounds how many divergences the proxy misses.

In total, 136 scenarios and 424 episodes were re-executed. Every other row keeps its sealed score.

**CPU.** The estimate from the sealed per-episode `cpu_s` was 18.9 CPU-min. The measured cost was
31.1 CPU-min (the episodes' own `cpu_s` sums to 31.0). That is over the 25 CPU-min budget. The likely
cause is CPU contention: the 4 workers shared 4 cores with a test run that was going at the same time,
so each episode took about 1.6× its sealed CPU time. The outcomes are deterministic, as the
reproduction check above shows, so the overrun does not affect the results.

## Findings

- **Proxy scenarios.** 32 episodes diverged, in 15 scenarios. All of them were caught by the guard.
- **Audit.** 0 of 60 audit scenarios diverged (120 episodes). By the rule of three, the proxy misses
  divergence in at most about 5% of proxy-clean scenarios for the confirmatory arms (95% upper bound).
  The audit does not cover the context arms.
- **Where it happens.** Every detected divergence was signalled by `mjWARN_BADQACC` together with a
  backwards sim clock. 11 of the 15 scenarios are stress scenarios (9 `rejection_long`, 2 `stale_long`).
  The other 4 are control insertions. When several arms diverged in the same scenario, they diverged at
  the same sim time.
- **Successes the sealed epoch counted.** In the sealed rows, 19 of the diverged episodes were graded
  `task_success`, and 16 of those were also graded bounded success. 4 of the 16 belong to the guarded arms.
  - Stress `m4s_rejection_long_00047` and `m4s_rejection_long_00187` were bounded successes for both
    guarded arms.
  - The `00116` G1 episode counted as a task success but was not bounded.

## Confirmatory sensitivity (C1 stress, C2 control)

Bounded success, `haplous_mediated` minus `g1_incumbent`. Scenario-paired percentile bootstrap,
10 000 resamples, seed 20261113. The decision rules are those of pre-registration section 6.

| Variant | C1 stress: delta [95% CI], verdict | C2 control: delta [95% CI], verdict |
|---|---|---|
| Sealed (`hes-m4-e2`) | +0.000 [+0.000, +0.000] (n=320), tie | +0.000 [+0.000, +0.000] (n=181), tie |
| Proxy worst case (every proxy-flagged row a failure) | +0.000 [+0.000, +0.000] (n=320), tie | +0.000 [+0.000, +0.000] (n=181), tie |
| Re-scored, HEB 0.3.0 | +0.000 [+0.000, +0.000] (n=320), tie | +0.000 [+0.000, +0.000] (n=181), tie |
| Re-scored, diverged scenarios dropped | +0.000 [+0.000, +0.000] (n=309), tie | +0.000 [+0.000, +0.000] (n=177), tie |

**No confirmatory verdict changes.** C1 and C2 stay "tie (equivalent within 0.05)" under every variant.

## Success counts per arm (sealed → re-scored HEB 0.3.0)

| Population | Arm | Task success | Bounded success |
|---|---|---|---|
| stress (320) | bare_behavior | 265 → 257 | 64 → 58 |
| stress | competent_supervisor | 64 → 58 | 64 → 58 |
| stress | g1_incumbent | 67 → 64 | 60 → 58 |
| stress | haplous_mediated | 65 → 63 | 60 → 58 |
| control (181) | bare_behavior | 114 → 114 | 23 → 23 |
| control | competent_supervisor | 21 → 21 | 20 → 20 |
| control | g1_incumbent | 42 → 42 | 41 → 41 |
| control | haplous_mediated | 42 → 42 | 41 → 41 |

The control divergences were all G1 or Haplous episodes that were already graded ABORTED, so the
control counts do not move. In stress, every arm's bounded-success rate falls by 2 to 6 scenarios. The
two guarded arms fall together, so their paired delta stays at zero. The exploratory comparisons
against `bare_behavior` and `competent_supervisor` are not recomputed here.

## Limits

- The re-score is complete only for the 76 proxy-flagged scenarios, plus the confirmatory arms of 60
  audited scenarios. A divergence in an unaudited, proxy-clean scenario would go unseen. For the
  confirmatory arms that risk is bounded at about 5% of such scenarios (95% upper bound).
- The guard detects MuJoCo's own reset signals. It does not judge physically implausible but finite
  motion, such as a fast pose jump without a reset. The pre-divergence observations, including any
  numerically blown-up samples just before the reset, are still graded for hard violations.
- The tie partly reflects a mechanism the two guarded arms share (#19), not equivalent guards.
