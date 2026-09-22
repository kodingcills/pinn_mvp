# HES v0 — Haplous Evaluation System

HES is the evaluation operating system around Haplous: it answers *what the
runtime currently claims, which experiment tests each claim, what evidence
supports or weakens it, and where the runtime helps, hurts, refuses, or
merely fails to capture attainable execution.* It is designed to discover
that Haplous is wrong as readily as that it works.

HES does not train a policy, does not host its own physics benchmark, and
does not emit a single weighted "Haplous score".

## Design principles

1. **Independent outcome axes.** Task success, contract safety, execution
   authority, and attainability are separate. No scalar collapse.
2. **Attainment-relative metrics.** "Execution capture" and "false refusal"
   are meaningless without a demonstrated-attainable set. Absent a
   demonstration, attainability is `UNKNOWN`, never `NON_ATTAINABLE`.
3. **Truth/visibility separation.** The evaluator reads benchmark-owned
   ground truth; Haplous sees only its normal observation pipeline. HES
   enforces this with a leakage integrity check.
4. **No fabrication.** A metric that cannot be computed from the evidence
   present is reported `NOT_AVAILABLE` with a reason, not back-filled.
5. **Honest attribution.** Failures are attributed to a layer
   (runtime / policy / controller / physics / benchmark / infrastructure),
   with an explicit confidence and an `UNKNOWN` bucket. Runtime-caused and
   artifact-caused effects are separated.
6. **Reproducibility.** The canonical report embeds a digest of the evidence
   it was generated from; `hes report --check` fails on drift.

## What HES composes (does not duplicate)

| Concern | Owner | HES role |
|---|---|---|
| Execution semantics | `haplous/` | read-only; never modified |
| Scenario truth, simulation, grading, counterfactuals | `benchmarks/heb/` | read; HES adds analysis over canonical rows |
| Claims/metrics registries | `evaluation/claims.yaml`, `evaluation/metrics.yaml` | read; HES derives statuses, never edits status |
| Statistics | `evaluation/stats.py` | reuse `wilson_interval`, `paired_bootstrap_mean`, `tail_percentiles`, ... |
| Resumable experiment lifecycle, Parquet row contract | `evaluation/experiment.py` | reuse; HES ingests its rows |
| Severity vocabulary | `evaluation/taxonomy.py` | reuse severity; HES refines root cause |
| Outcome/metrics/attribution/reporting | `hes/` | new |

## Architecture

```
canonical episode rows + forks + manifest
            │
      hes.run.load_run ──────────────► Run
            │                            │
            │                     attainability()   (hes.model)
            ▼                            │
      hes.metrics  ◄─────────────────────┘
        system / authority / non-interference / physical / timing
            │
   ┌────────┼───────────┬───────────────┬────────────────┐
   ▼        ▼           ▼               ▼                ▼
hes.attribution  hes.pareto   hes.intervention   hes.integrity  hes.claims
   │              │              │                  │              │
   └──────────────┴──────────────┴──────────────────┴──────────────
            │
      hes.gates (hard qualification gates)
            │
      hes.bundle.build_bundle ──► hes.report.render_markdown / check_report
            │
      hes.cli  (doctor test analyze qualify claims recommend-next
                characterize populations report migrate)
```

## Data model

### Canonical episode row (unchanged; owned by `evaluation/experiment.py`)

`schema_version, experiment_id, shard_id, shard_index, arm, episode_id,
scenario_id, seed, graded_disposition, reported_disposition, reason,
task_success, bounded_success, wall_s, n_hard_violations, refusal_codes,
hard_violations, task_metrics, tags`

### Independent axes (`hes.model`)

| Axis | Values |
|---|---|
| Task | `SUCCESS` \| `FAILURE` \| `PARTIAL` \| `UNKNOWN` |
| Contract | `BOUNDED` \| `VIOLATED` \| `UNKNOWN` |
| Authority | `ACCEPTED` \| `REFUSED` \| `ABORTED` \| `HELD` \| `RECOVERED` \| `UNKNOWN` |
| Attainability | `ATTAINABLE` \| `NON_ATTAINABLE` \| `UNKNOWN` |

### Attainability

Demonstrated only by: a paired bounded baseline, a classified
`FALSE_REFUSAL` counterfactual, an oracle, a known construction, or repeated
physical evidence. **A direct trajectory that completes while violating the
contract does not demonstrate bounded attainability.** The provenance is
stored on every assignment. `NON_ATTAINABLE` requires an explicit
infeasibility marker.

## Metric vector (`hes.metrics`)

| Group | Metrics |
|---|---|
| system | task success, bounded success (system BSR), conditional BSR, coverage |
| execution authority | authorization precision/recall, execution capture, hazard rejection, false refusal rate, false abort rate, violation given acceptance |
| non-interference | preservation rate, bounded/task regression rates, unnecessary intervention rate, completion-time tax |
| physical consequence | peak force, force exceedance, integrated excursion*, collision count/severity* |
| intervention quality | intervention precision, harmful/unnecessary rates, successes recovered/destroyed, ambiguous rate |
| timing | wall P50/P90/P95/P99/max (P95/P99 suppressed below sample floors), decision latency*, fault-to-intervention latency*, state age at authorization* |

Metrics marked `*` are `NOT_AVAILABLE` on the internal substrate today and
say so.

## Attribution (`hes.attribution`)

Top-level layers: `UPSTREAM_POLICY`, `EXECUTION_RUNTIME`, `CONTROLLER`,
`MACHINE_PHYSICS`, `ENVIRONMENT`, `BENCHMARK_EVALUATOR`, `INFRASTRUCTURE`,
`UNKNOWN`. Runtime failures carry a subcause
(`false_refusal`, `correct_infeasible_refusal`, `capability_rejection`,
`deadline_rejection`, `freshness_rejection`, `reach_rejection`,
`hold_timeout`, `constraint_abort`, `recovery_failure`, ...).

Each attribution records: primary cause, contributing causes, first causal
divergence, confidence (high/medium/low), evidence, addressability, and
severity (S0–S5). `UNKNOWN` is tracked as a first-class row, never folded
into a runtime bucket.

## Failure Pareto (`hes.pareto`)

`priority = frequency × severity_weight × haplous_responsibility × addressability_weight`

Raw counts, responsibility, and addressability are always reported next to
the ranking. Responsibility decomposition charges a runtime refusal as a
caused loss **only when the scenario was demonstrated attainable**;
UNKNOWN-attainability refusals go in `runtime_refusal_attainability_unknown`.

## Intervention semantics (`hes.intervention`)

Canonical and pinned by regression test (see `tests/test_hes_intervention_semantics.py`):

| Class | Meaning |
|---|---|
| `BENEFICIAL` | continuation would record a strictly worse breach |
| `HARMFUL` | continuation completes without worsening; stop destroyed work |
| `UNNECESSARY` | continuation neither completes nor worsens |
| `TRUE_REFUSAL` | refused request violates a hard contract or misses its deadline |
| `FALSE_REFUSAL` | refused request completes and satisfies every hard contract |

Section 18 of the historical v3 report inverted `BENEFICIAL`/`HARMFUL` in
prose. The classifier was always correct; the prose was fixed and a test now
prevents the inversion from returning.

## Integrity and benchmark health (`hes.integrity`, `hes.benchmark_health`)

Pass/fail checks, not performance metrics:

- `shortcut_solvability` — trivial policies must not dominate;
- `mirrored_scenarios` — evaluator metadata must not change decisions;
- `small_sample_intervals` — small-n rates must carry intervals;
- `privileged_leakage` — grader-only fields are not runtime-visible;
- `metric_gaming` — high acceptance with high violation-after-acceptance is flagged.

`hes.benchmark_health` audits the instrument from the grader's side:
`discriminating_power` (do arms ever disagree?), `degenerate_scenarios`
(trivially solved / never solved), `population_balance` (task families and
offset strata), and `grader_consistency` (bounded success never coexists with
a recorded hard violation).

## Hard gates (`hes.gates`)

A candidate is qualified only if all pass:

| Gate | Condition |
|---|---|
| `G1` integrity | every integrity check passes |
| `G2` bounded violation | conditional violation ≤ unmediated baseline |
| `G3` non-interference | preservation of baseline bounded success ≥ floor |
| `G4` false refusal | false-refusal rate ≤ ceiling |
| `G5` reproducibility | provenance present |

The performance frontier (BSR, coverage, capture) is **reported, not
gated**: making it pass/fail would manufacture a target to game.

## Metamorphic and boundary (`hes.metamorphic`, `hes.boundary`)

- metamorphic relations: requirement relaxation/tightening monotonicity,
  decision-irrelevant invariance, re-parameterization invariance;
- boundary search: bounded-budget bisection of a decision transition along a
  scenario parameter, with a monotonicity guard that refuses to report a
  boundary on a non-monotone response.

Both take a `runner` callable, so unit tests use stubs and qualification runs
use the real benchmark runner.

## Regression and populations (`hes.regression`, `hes.populations`)

- `compare_metric_vectors` applies per-metric direction/tolerance gates;
- `paired_rate_delta` does a scenario-paired bootstrap;
- `slice_gates` rejects a global win that hides a per-slice regression, and
  marks under-powered slices instead of passing them;
- populations are named and versioned (`dev@v1`, `regression@v1`,
  `qualification@v1`) so results are never pooled across classes.

## CLI

```bash
hes doctor [--run DIR]                 # environment + evidence check
hes test --smoke                       # HES self-tests
hes analyze RUN [--out FILE]           # metric-vector bundle (JSON)
hes qualify RUN                        # hard gates; exit 1 on failure
hes claims RUN                         # claim ledger
hes recommend-next RUN                 # ranked next experiments
hes characterize                       # substrate characterization
hes benchmark-health RUN               # is the benchmark instrument sound?
hes populations                        # versioned population classes
hes report RUN [--out FILE] [--check]  # render / drift-check canonical report
hes migrate SOURCE --out DIR           # ingest a historical campaign
```

`hes replay`, `hes counterfactual`, and `hes boundary` require a live
benchmark runner; without one they exit 2 with
`NOT_AVAILABLE` rather than pretending to run.

## Reproducing the v0 campaign

```bash
.venv/bin/python scripts/run_hes_v0_campaign.py
```

This migrates `results/v3_hardening_qualification/` into
`results/hes/v3_hardening_qualification/` without modifying the historical
results, analyzes it, evaluates the gates, and writes the digest-checked
`docs/HES_V0_REPORT.md`. The migration driver is
`scripts/migrate_v3_campaign_to_hes.py`.

## Deferred (not in v0)

- live `replay`/`counterfactual`/`boundary` CLI driven by the benchmark runner;
- recovery/latency/evidence channels in the canonical row schema;
- hardware-leg qualification;
- cross-engine pairing for `C12`.

Each of these has an executable-test stub in the claim ledger marked
`NOT_EXECUTABLE_IN_HES_V0` with the missing channel named.