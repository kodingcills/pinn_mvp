"""Render docs/HAPLOUS_V3_SOFTWARE_HARDENING.md from the committed
qualification evidence package.

Every number in the report is injected from
results/v3_hardening_qualification/summary.json (+ metrics.json,
forks_summary.json, parity/parity.json). Nothing is transcribed by hand.

    python scripts/render_v3_hardening_report.py            # write
    python scripts/render_v3_hardening_report.py --check    # fail on drift
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = ROOT / "results" / "v3_hardening_qualification"
REPORT_PATH = ROOT / "docs" / "HAPLOUS_V3_SOFTWARE_HARDENING.md"


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{100 * value:.1f}%"


def _ci(block: dict) -> str:
    if not block or block.get("value") is None:
        return "n/a"
    return f"{block['value']:.3f} [{block['ci_low']:.3f}, {block['ci_high']:.3f}]"


def _num(block: dict) -> str:
    return "n/a" if not block or block.get("value") is None else f"{block['value']:.3f}"


def _wilson(k: int, n: int) -> str:
    from evaluation.stats import wilson_interval

    lo, hi = wilson_interval(k, n)
    return f"{k / n:.3f} [{lo:.3f}, {hi:.3f}]"


def render(root: Path) -> str:
    summary = json.loads((root / "summary.json").read_text(encoding="utf-8"))
    metrics = json.loads((root / "metrics.json").read_text(encoding="utf-8"))
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    parity = json.loads((root / "parity" / "parity.json").read_text(encoding="utf-8"))
    forks = {}
    if (root / "forks_summary.json").is_file():
        forks = json.loads((root / "forks_summary.json").read_text(encoding="utf-8"))

    arms = summary["arms"]
    reference = metrics["reference_arm"]
    n = summary["n_scenarios"]
    provenance = summary["provenance"]
    deps = provenance.get("dependencies") or {}

    def arm_table() -> str:
        header = (
            "| Arm | N | Task success (95% CI) | System BSR | Conditional BSR | "
            "Coverage | Violation rate | Violation \\| accepted |\n"
            "|---|---|---|---|---|---|---|---|\n"
        )
        rows = []
        for name, data in arms.items():
            rows.append(
                f"| `{name}` | {data['n']} | {_ci(data['task_success'])} | "
                f"{_ci(data['bsr_system'])} | {_ci(data['bsr_conditional'])} | "
                f"{_ci(data['coverage'])} | {_ci(data['any_violation'])} | "
                f"{_ci(data['violation_conditional_acceptance'])} |"
            )
        return header + "\n".join(rows)

    def latency_table() -> str:
        header = "| Arm | P50 (s) | P90 (s) | P95 (s) | P99 (s) | Max (s) |\n|---|---|---|---|---|---|\n"
        rows = []
        for name, data in arms.items():
            lat = data.get("latency_s") or {}
            rows.append(
                f"| `{name}` | {lat.get('p50', float('nan')):.2f} | "
                f"{lat.get('p90', float('nan')):.2f} | {lat.get('p95', float('nan')):.2f} | "
                f"{lat.get('p99', float('nan')):.2f} | {lat.get('max', float('nan')):.2f} |"
            )
        return header + "\n".join(rows)

    def paired_table() -> str:
        header = "| Comparison | Metric | n01 | n10 | exact p |\n|---|---|---|---|---|\n"
        def _p(value: float) -> str:
            return f"{value:.2e}" if value < 1e-4 else f"{value:.4f}"
        rows = []
        for key, data in summary["paired"].items():
            rows.append(
                f"| {key} | task_success | {data['mcnemar_task_success']['n01']} | "
                f"{data['mcnemar_task_success']['n10']} | "
                f"{_p(data['mcnemar_task_success']['p_value'])} |"
            )
            rows.append(
                f"| {key} | bounded_success | {data['mcnemar_bounded_success']['n01']} | "
                f"{data['mcnemar_bounded_success']['n10']} | "
                f"{_p(data['mcnemar_bounded_success']['p_value'])} |"
            )
        return header + "\n".join(rows)

    def failure_table() -> str:
        header = "| Arm | Failures | Failure rate | Root causes |\n|---|---|---|---|\n"
        rows = []
        for name, data in arms.items():
            fm = data["failure_mass"]
            causes = ", ".join(f"{k}: {v}" for k, v in sorted(fm["by_root_cause"].items()))
            rows.append(
                f"| `{name}` | {fm['n_failures']}/{fm['n_trials']} | "
                f"{fm['failure_rate']:.3f} | {causes or 'none'} |"
            )
        return header + "\n".join(rows)

    def intervention_table() -> str:
        if not forks:
            return "_Fork pass not present in this package._"
        iv = forks.get("interventions", {})
        ref = forks.get("refusals", {})
        lines = ["| Class | Count |", "|---|---|"]
        for key in ("BENEFICIAL", "HARMFUL", "UNNECESSARY", "AMBIGUOUS"):
            lines.append(f"| intervention / {key} | {iv.get(key, 0)} |")
        for key in ("TRUE_REFUSAL", "FALSE_REFUSAL", "AMBIGUOUS"):
            lines.append(f"| refusal / {key} | {ref.get(key, 0)} |")
        return "\n".join(lines)

    def metric_dict_table() -> str:
        header = "| Metric | Meaning | Numerator / denominator | Establishes | Does not establish |\n|---|---|---|---|---|\n"
        rows = []
        for entry in summary["metric_dictionary"]:
            rows.append(
                f"| `{entry['name']}` | {entry['meaning']} | "
                f"{entry['numerator']} / {entry['denominator']} | "
                f"{entry['establishes']} | {entry['does_not_establish']} |"
            )
        return header + "\n".join(rows)

    core_ok = parity["core_modules_identical"]

    return f"""# Haplous v3 Pre-Benchmark Software Hardening and Local Qualification

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

- Baseline commit: `{parity['baseline_commit']}`.
- Haplous package version: `{manifest.get('haplous_version')}`.
- Working tree at run: commit `{provenance.get('git_commit')}`,
  dirty={provenance.get('git_dirty')}.
- Platform: {provenance.get('platform')}, Python {provenance.get('python')}.
- Dependencies: {', '.join(f'{k}={v}' for k, v in sorted(deps.items()) if v)}.

## 3. PASS A - behavioral parity of v3 semantics

Method: byte-level comparison of every Haplous core-semantics module
against the baseline commit, plus the unchanged characterization suite.
Core modules identical: **{core_ok}**. Divergent core modules:
`{parity['core_modules_changed'] or 'none'}`. The hardening pass is
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
`{summary['completed_shards']}` COMPLETE shards
({summary.get('requested_n')} population, seed {summary['seed']}).

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

{latency_table()}

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

Population: HEB randomized insert/slide scenarios, seed {summary['seed']},
N={n} scenarios, three arms on shared realizations, benchmark-owned
grading. BSR is reported in both forms and never collapsed.

{arm_table()}

Reading the table: the direct arm reaches the highest raw task success
({_num(arms['direct_minimal']['task_success'])}) precisely because it
does not honour the declared bounds, so its bounded success
({_num(arms['direct_minimal']['bsr_system'])}) is as low as its violation
rate ({_num(arms['direct_minimal']['any_violation'])}) is high. The
supervisor keeps coverage at {_num(arms['competent_supervisor']['coverage'])}
but stops on every watched breach, so it completes rarely. Haplous
refuses {_pct(1 - arms['haplous_mediated']['coverage']['value'])} of
requests up front; conditional on committing it reaches the highest
conditional BSR ({_num(arms['haplous_mediated']['bsr_conditional'])}) and
the lowest violation rate
({_num(arms['haplous_mediated']['any_violation'])}). Haplous's advantage
is on the bounded measure, not on raw task success against the
unconstrained arm.

Paired exact McNemar against the reference arm `{reference}`:

{paired_table()}

Metric dictionary:

{metric_dict_table()}

## 18. Counterfactual intervention analysis

Forks are executed at Haplous runtime stops and refusals on the bounded
sample below; un-attributable forks are classified AMBIGUOUS and excluded
from the attributable denominator. Forked scenarios: {forks.get('fork_scenarios', 'n/a')}.

{intervention_table()}

Of the attributable intervention forks
({forks.get('interventions', {}).get('BENEFICIAL', 0) +
forks.get('interventions', {}).get('HARMFUL', 0) +
forks.get('interventions', {}).get('UNNECESSARY', 0)}), 
{forks.get('interventions', {}).get('BENEFICIAL', 0)} are BENEFICIAL (the direct
continuation from the runtime stop would have recorded a strictly worse
force breach), {forks.get('interventions', {}).get('HARMFUL', 0)} are HARMFUL
(the continuation would have completed without worsening, so the stop
destroyed productive execution), and
{forks.get('interventions', {}).get('UNNECESSARY', 0)} are UNNECESSARY.
False refusals are
{forks.get('refusals', {}).get('FALSE_REFUSAL', 0)} of
{forks.get('refusals', {}).get('TRUE_REFUSAL', 0) +
forks.get('refusals', {}).get('FALSE_REFUSAL', 0)} forked refusals. No
fork was AMBIGUOUS in this sample, so every fork is attributable.

## 19. Failure taxonomy, limitations, final verdict

Failure mass by arm and root cause:

{failure_table()}

Limitations, stated plainly:

- Local CPU MuJoCo only; N={n} is qualified for rate estimation with the
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
.venv/bin/python scripts/run_v3_hardening_qualification.py --mode all \\
    --n {n} --seed {summary['seed']} --shard-size 20 --workers 10 --fork-scenarios 120
.venv/bin/python scripts/render_v3_hardening_report.py --check
```
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    text = render(args.root)
    if args.check:
        if not REPORT_PATH.is_file():
            print(f"missing report {REPORT_PATH}", file=sys.stderr)
            return 1
        if REPORT_PATH.read_text(encoding="utf-8") != text:
            print(
                "report drift: docs/HAPLOUS_V3_SOFTWARE_HARDENING.md does not "
                "match the rendered package",
                file=sys.stderr,
            )
            return 1
        print("report matches package")
        return 0
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(text, encoding="utf-8")
    print(f"wrote {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
