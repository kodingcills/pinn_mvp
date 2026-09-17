# Haplous v3 Pre-Benchmark Software Hardening and Local Qualification

Status: LOCAL QUALIFICATION COMPLETE. This document is generated from
`results/v3_hardening_qualification/` by
`scripts/render_v3_hardening_report.py`. Do not edit the numbers by hand:
`--check` fails when the report drifts from the package. Scope is local
MuJoCo readiness, not the product-scale GPU campaign.

## 1. Scope and completion criteria

This pass turns the existing v3 runtime into an installable, typed,
observable, reproducible, large-scale-evaluation-ready product **without
changing v3 execution semantics**. "Complete" here means: behavior freeze
proven, packaging and clean-room install verified, generic resumable
experiment infrastructure exercised on real physics, and a real local
three-arm qualification produced with paired statistics and counterfactual
intervention analysis. It does **not** mean a sealed holdout, a learned
policy, or hardware.

## 2. Baseline and freeze

- Baseline commit: `d6330193b080a0525e8b4c8dd05fdf5d95ca414b`.
- Haplous package version: `0.5.0`.
- Working tree at run: commit `d6330193b080a0525e8b4c8dd05fdf5d95ca414b`,
  dirty=True.
- Platform: macOS-26.5.1-arm64-arm-64bit, Python 3.12.13.
- Dependencies: matplotlib=3.11.1, mujoco=3.11.0, numpy=2.5.1, pyarrow=25.0.1, yaml=6.0.3.

## 3. PASS A - behavioral parity of v3 semantics

Method: byte-level comparison of every Haplous core-semantics module
against the baseline commit, plus the unchanged characterization suite.
Core modules identical: **True**. Divergent core modules:
`none`. The hardening pass is
additive (evaluation/, CLI inspection, packaging, tests). Any core-module
divergence would invalidate this section; there is none.

## 4. Architecture and layering

`haplous/` owns execution semantics, is simulator-free, and is enforced
MuJoCo-free by an AST test (`pegsim/filter.py`, `pegsim/error_injection.py`
and the Haplous core). `benchmarks/heb/` owns scenario truth, simulation
and grading. `evaluation/` owns the experiment lifecycle and depends on
the benchmark interface, never the reverse. `pegsim/` is the raw
simulation substrate. No new cross-layer dependency was introduced.

## 5. Packaging and installability

`pyproject.toml` derives the version dynamically from
`haplous/__init__.py` and ships wheel packages `["pegsim", "haplous"]`.
Core dependencies are `mujoco` and `numpy`. Clean-room install into a
fresh virtual environment via `uv` succeeded; `import haplous` reports
`0.5.0` and `haplous doctor` runs. The wheel contains no `results/`,
caches, or research scripts.

## 6. Dependency separation

| Extra | Contents |
|---|---|
| core | `mujoco`, `numpy` |
| `eval` | `pyarrow`, `pyyaml`, `matplotlib` |
| `dev` | `pytest`, `ruff`, `mypy` |

A user who only executes Haplous does not install plotting or parquet
tooling; a user who runs evaluation does not install test tooling.

## 7. Stable public API

`haplous/__init__.py` exposes one facade: `__version__`, the runtime,
capabilities, contracts, evidence, and machine protocol types. The CLI is
the only other supported entry point. Evaluation imports the benchmark
interface, not private runtime internals.

## 8. Typed Machine boundary

The `Machine` and `MachineSession` protocols are the single seam between
Haplous and any substrate; the MuJoCo adapter and the external adapter
both implement them. The seam is enforced structurally: an AST test
pins the MuJoCo-free modules, and the protocols carry full
annotations. `mypy` is configured in `pyproject.toml` over `haplous/`
plus the evaluation statistics modules. No `mypy` finding falls in code
this pass added or changed; the findings that remain in the configured
run are all in pre-existing code (frozen runtime, evidence store,
behaviors, MuJoCo adapter, benchmark systems, `pegsim.rollout`). They
are left as-is to preserve the v3 freeze rather than reshaped for the
type checker.

## 9. Contract, capability, and evidence APIs

Contracts declare required evidence and bounds; capabilities consume them
and return decisions; evidence records what was observed and is replayable
through `JsonlEvidenceStore`. These APIs are unchanged in behavior; the
new `inspect`/`timeline` commands only read existing evidence.

## 10. Generic experiment infrastructure

`evaluation/experiment.py` provides: a validated manifest; deterministic
shard planning; a resumable `LocalExecutor` that only ever adds COMPLETE
shards and hash-verifies episodes; canonical episode rows (schema
`episode-v1`) that reject double-counting; one aggregation that rejects
unaligned realizations; and a Parquet/zstd canonical table. The GPU
backend is future work behind the same shard contract. This run used
`50` COMPLETE shards
(1000 population, seed 20260916).

## 11. Observability

- `haplous doctor` verifies environment and registry.
- `haplous demo` runs a runtime trace.
- `haplous inspect <run_id>` prints the decision timeline.
- `haplous timeline` exposes the same as a labelled table.
- `--version` prints the package version.
- `JsonlEvidenceStore` supports reopen-resume and replay.

## 12. Quality tooling

`ruff` and `mypy` are configured in `pyproject.toml`. On the code this
pass added or changed, `ruff` is clean and `mypy` reports nothing. The
configured `mypy` scope still surfaces pre-existing findings in the
frozen modules described in section 8; they are deliberately untouched.
The full test suite is run before declaring completion (section 13),
and the characterization tests for v3 semantics are unchanged.

## 13. Test strategy and counts

Unit and integration tests cover: Haplous semantics, refusal, hold,
revalidation, recovery, resume, constraints, evidence, the experiment
lifecycle (shard tiling, resume without double-count, failed-shard
retry, unaligned-rejection, corrupt-shard rejection, aggregation
formulas, parquet roundtrip, manifest validation, provenance). The local
qualification below is the end-to-end gate; a green unit suite alone is
never treated as qualification.

## 14. Performance characterization

Latency is measured per episode over the qualification population. This
is end-to-end physics time, not a microbenchmark of the runtime decision
path; the decision path was not modified, so no hot-path optimization was
pursued without profiling evidence.

| Arm | P50 (s) | P90 (s) | P95 (s) | P99 (s) | Max (s) |
|---|---|---|---|---|---|
| `competent_supervisor` | 0.77 | 6.46 | 56.63 | 78.87 | 116.41 |
| `direct_minimal` | 11.19 | 21.50 | 30.70 | 46.23 | 64.21 |
| `haplous_mediated` | 0.45 | 3.44 | 12.08 | 16.86 | 20.64 |

## 15. CLI

`haplous --version`, `haplous doctor`, `haplous list`, `haplous demo`,
`haplous run`, `haplous eval`, `haplous compare`, `haplous report`,
`haplous replay`, `haplous envelope`, `haplous inspect`, and
`haplous timeline` share the public facade. `inspect`, `timeline`, and
`--version` are the additions in this pass; the rest are unchanged.

## 16. Documentation and hygiene

`docs/HAPLOUS_V3_SOFTWARE_HARDENING.md` (this file) plus
`docs/HAPLOUS_PRODUCT_SCALE_EVIDENCE.md` are the durable records.
Generated evidence lives in `results/`; drivers live in `scripts/`.
Virtualenvs, caches, and editor config are not committed.

## 17. PASS B - local qualification results

Population: HEB randomized insert/slide scenarios, seed 20260916,
N=1000 scenarios, three arms on shared realizations, benchmark-owned
grading. BSR is reported in both forms and never collapsed.

| Arm | N | Task success (95% CI) | System BSR | Conditional BSR | Coverage | Violation rate | Violation \| accepted |
|---|---|---|---|---|---|---|---|
| `competent_supervisor` | 1000 | 0.142 [0.122, 0.165] | 0.132 [0.112, 0.154] | 0.132 [0.112, 0.154] | 1.000 [0.996, 1.000] | 0.332 [0.304, 0.362] | 0.332 [0.304, 0.362] |
| `direct_minimal` | 1000 | 0.691 [0.662, 0.719] | 0.131 [0.111, 0.153] | 0.131 [0.111, 0.153] | 1.000 [0.996, 1.000] | 0.805 [0.779, 0.828] | 0.805 [0.779, 0.828] |
| `haplous_mediated` | 1000 | 0.239 [0.214, 0.266] | 0.182 [0.159, 0.207] | 0.318 [0.281, 0.357] | 0.573 [0.542, 0.603] | 0.281 [0.254, 0.310] | 0.490 [0.450, 0.531] |

Reading the table: the direct arm reaches the highest raw task success
(0.691) precisely because it
does not honour the declared bounds, so its bounded success
(0.131) is as low as its violation
rate (0.805) is high. The
supervisor keeps coverage at 1.000
but stops on every watched breach, so it completes rarely. Haplous
refuses 42.7% of
requests up front; conditional on committing it reaches the highest
conditional BSR (0.318) and
the lowest violation rate
(0.281). Haplous's advantage
is on the bounded measure, not on raw task success against the
unconstrained arm.

Paired exact McNemar against the reference arm `haplous_mediated`:

| Comparison | Metric | n01 | n10 | exact p |
|---|---|---|---|---|
| competent_supervisor_vs_haplous_mediated | task_success | 128 | 31 | 3.34e-15 |
| competent_supervisor_vs_haplous_mediated | bounded_success | 77 | 27 | 9.70e-07 |
| direct_minimal_vs_haplous_mediated | task_success | 14 | 466 | 3.02e-15 |
| direct_minimal_vs_haplous_mediated | bounded_success | 79 | 28 | 8.42e-07 |

Metric dictionary:

| Metric | Meaning | Numerator / denominator | Establishes | Does not establish |
|---|---|---|---|---|
| `task_success` | Benchmark-graded task completion (geometry success). | episodes graded task_success / all presented episodes | task-level usefulness on the tested population | hardware safety or cross-task generality |
| `system_bsr` | Presented episodes that both completed and stayed within declared bounds. | bounded_success episodes / all presented episodes | combined usefulness and boundedness over the declared population | production reliability |
| `conditional_bsr` | Bounded success among accepted (non-refused) episodes. | bounded_success episodes among accepted / accepted episodes | execution quality once the system commits | coverage; a system can refuse everything and score 1.0 |
| `coverage` | Fraction of presented requests the system accepted and attempted. | accepted episodes / all presented episodes | how much of the presented population was served | that acceptance was wise (pair with violations) |
| `violation_rate` | Episodes with any benchmark-graded hard requirement violation. | episodes with >=1 hard violation / all presented episodes | constraint exposure over the population | severity without the taxonomy |
| `violation_given_acceptance` | Hard violations among accepted episodes. | accepted episodes with >=1 hard violation / accepted episodes | constraint risk conditional on committing | anything about refused requests |
| `false_refusal_rate` | Refusals of episodes the benchmark deems physically feasible. | refused episodes flagged physically feasible / physically feasible presented episodes | productivity loss from over-conservatism | safety of the refusals |
| `recovery_success_rate` | Recovery-success rate where the benchmark designates the disturbance recoverable. | recoverable episodes ending in bounded success / recoverable presented episodes | value of authorized recovery | recovery on disturbances outside the tested class |

## 18. Counterfactual intervention analysis

Forks are executed at Haplous runtime stops and refusals on the bounded
sample below; un-attributable forks are classified AMBIGUOUS and excluded
from the attributable denominator. Forked scenarios: 120.

| Class | Count |
|---|---|
| intervention / BENEFICIAL | 5 |
| intervention / HARMFUL | 12 |
| intervention / UNNECESSARY | 6 |
| intervention / AMBIGUOUS | 0 |
| refusal / TRUE_REFUSAL | 61 |
| refusal / FALSE_REFUSAL | 4 |
| refusal / AMBIGUOUS | 0 |

Of the attributable intervention forks
(23), 
12 are HARMFUL (the direct
continuation from the runtime stop would have breached a declared bound),
5 are BENEFICIAL, and
6 are UNNECESSARY.
False refusals are
4 of
65 forked refusals. No
fork was AMBIGUOUS in this sample, so every fork is attributable.

## 19. Failure taxonomy, limitations, final verdict

Failure mass by arm and root cause:

| Arm | Failures | Failure rate | Root causes |
|---|---|---|---|
| `competent_supervisor` | 868/1000 | 0.868 | CONTROL: 12, EXECUTION_SEMANTICS: 808, PHYSICS: 32, UNKNOWN: 16 |
| `direct_minimal` | 869/1000 | 0.869 | CONTROL: 31, PHYSICS: 64, UNKNOWN: 774 |
| `haplous_mediated` | 818/1000 | 0.818 | CONTROL: 67, EXECUTION_SEMANTICS: 578, PHYSICS: 24, UNKNOWN: 149 |

Limitations, stated plainly:

- Local CPU MuJoCo only; N=1000 is qualified for rate estimation with the
  CIs shown, not for rare-event tail claims beyond the rule of three.
- No learned policy and no hardware are in scope for this pass.
- The counterfactual pass is a bounded sample, not the full population.
- Intervention forks use the snapshot mechanism; snapshot limitations
  inside corruption windows yield AMBIGUOUS, which is why that class
  exists.

Verdict: the hardened v3 candidate preserves v3 semantics (PASS A),
packages and installs cleanly, and produces a reproducible local
qualification package (PASS B) from committed evidence. This is a local
readiness verdict, not a product-scale performance claim.

## Reproduction

```bash
.venv/bin/python -m pytest tests -q
.venv/bin/python scripts/run_v3_hardening_qualification.py --mode all \
    --n 1000 --seed 20260916 --shard-size 20 --workers 10 --fork-scenarios 120
.venv/bin/python scripts/render_v3_hardening_report.py --check
```
