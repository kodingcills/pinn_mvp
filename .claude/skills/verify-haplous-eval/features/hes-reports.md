# HES reports

The Haplous Evaluation System turns a campaign directory into a metric-vector bundle, evaluates hard gates, and renders a canonical markdown report that embeds a digest of the evidence it came from. A user checks the environment, analyzes, qualifies and reports on one campaign.

## Sub-features

- `hes-doctor` checks the environment and, with `--run`, the campaign directory.
- `hes-analyze` writes the metric-vector bundle as JSON.
- `hes-qualify` evaluates hard gates and exits 1 if any fails.
- `hes-report` renders the canonical report with an evidence digest.
- `hes-report-check` fails when a rendered report no longer matches its evidence.

## How to get to it (user POV)

- Run `hes doctor [--run DIR]`.
- Run `hes analyze RUN [--arm A] [--out FILE] [--quiet]`.
- Run `hes qualify RUN [--arm A]`.
- Run `hes report RUN --out FILE [--check]`.

## Driving it with the CLI

Preconditions:

- Launch and Doctor from `../SKILL.md` passed.
- `results/hes/v3_hardening_qualification` exists. It holds `episodes.parquet` and `manifest.json`.

- **Doctor.** Run `$VENV/bin/hes doctor --run results/hes/v3_hardening_qualification`. Exit 0 with `"run_exists": true`, `"n_episodes": 3000` and arms `competent_supervisor`, `direct_minimal` and `haplous_mediated`.
- **Analyze.** Run `$VENV/bin/hes analyze results/hes/v3_hardening_qualification --out $W/hes_analysis.json --quiet`. Exit 0. The JSON has `analysis.arms` with the three arms and `analysis.baseline_arm` set to `direct_minimal`.
- **Qualify.** Run `$VENV/bin/hes qualify results/hes/v3_hardening_qualification; echo rc=$?`. On this committed campaign, `rc=1`, `"qualified": false` and `"n_violations": 1`. The failing gate is `non_interference`, observed 0.786 against the threshold `>= 0.95`. This is the committed baseline, not a regression.
- **Report.** Run `$VENV/bin/hes report results/hes/v3_hardening_qualification --out $W/hes_report.md`. Exit 0 with `wrote $W/hes_report.md`. The file starts with `<!-- hes-report-digest: dc913cf8e9886bfd -->` and an `Execution authority` table, where `haplous_mediated` bounded success is 0.182 with n=1000.
- **Drift check.** Run `$VENV/bin/hes report results/hes/v3_hardening_qualification --check --out $W/hes_report.md; echo rc=$?`. Output is `report matches the evidence digest` and `rc=0`.

## Gotchas

- `hes report RUN` without `--out` writes to `docs/HES_V0_REPORT.md` in the checkout. Always pass `--out` into `$W`.
- `hes report RUN --check` without `--out` compares against `docs/HES_V0_REPORT.md`, whose digest does not match this campaign. It prints `report drift: expected '<!-- hes-report-digest: dc913cf8e9886bfd -->' not found` and exits 1. That is a wrong comparison target, not evidence drift.
- `hes replay`, `hes counterfactual` and `hes boundary` need a live runner and exit 2 with `NOT_AVAILABLE` (per `docs/HES.md`). They were not exercised and are not the archive replay. Use `haplous replay` for recordings.
- HES does not read M0 recordings yet. A recording-backed HES campaign is `M4.2`.
