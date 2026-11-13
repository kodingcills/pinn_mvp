# Compare systems

A user runs the internal insertion benchmark under different systems, such as `haplous_mediated` and the existing `competent_supervisor` incumbent, and compares their summary metrics side by side. This uses `haplous run` and `haplous compare`, with `inspect`, `timeline` and `report` for one run.

## Sub-features

- `run-benchmark` runs one benchmark, system, scenario and seed, and writes a run directory.
- `compare-runs` tabulates summary metrics of two runs per system.
- `inspect-run` explains one execution's terminal result.
- `timeline-run` prints the normalized execution timeline of a run.
- `report-run` prints the path of a run's summary.

## How to get to it (user POV)

- Run `haplous list` to see benchmarks (`internal-insertion`, `internal-slide`) and systems (`direct_minimal`, `bare_behavior`, `competent_supervisor`, `haplous_mediated`).
- Run `haplous run <benchmark> --system <s> --scenario <id> --seed <n>`.
- Run `haplous compare <run_a> <run_b>`, `haplous inspect <run>`, `haplous timeline <run>` and `haplous report <run>`.

## Driving it with the CLI

Preconditions:

- Launch from `../SKILL.md` ran, so `HAPLOUS_RESULTS_DIR=$W/haplous_runs` and `HAPLOUS_ARTIFACTS_DIR=$W/haplous_artifacts`.

- **Run Haplous.** Run `H=$($VENV/bin/haplous run internal-insertion --system haplous_mediated --scenario insert_dropout_large --seed 0 | awk '/run_id/{print $2}')`. Exit 0 in about 1 s. `$W/haplous_runs/internal-insertion/$H/` holds `manifest.json`, `summary.json`, `trials.jsonl` and `artifact_hashes.json`.
- **Run the incumbent.** Run `C=$($VENV/bin/haplous run internal-insertion --system competent_supervisor --scenario insert_dropout_large --seed 0 | awk '/run_id/{print $2}')`. Exit 0.
- **Compare.** Run `$VENV/bin/haplous compare $C $H`. Exit 0. The table has one block per system. On this scenario and seed, `competent_supervisor` shows `task_successes 0` and `bounded_success_rate_among_accepted 0.0`, and `haplous_mediated` shows `task_successes 1` and `1.0`.
- **Recompute from raw rows.** Run `$VENV/bin/python -c "import json,sys; [print(r['system'], r['bounded_success'], r['graded_disposition']) for r in map(json.loads, open(sys.argv[1]))]" $W/haplous_runs/internal-insertion/$C/trials.jsonl` and the same for `$H`. Expect `competent_supervisor False ABORTED` and `haplous_mediated True COMPLETE`, which agree with the comparison table.
- **Inspect.** Run `$VENV/bin/haplous inspect $H`. Exit 0 with `[insert_dropout_large] haplous_mediated: COMPLETE`.
- **Report.** Run `$VENV/bin/haplous report $H`. Exit 0, printing the path of `summary.json`.

## Gotchas

- Without `HAPLOUS_RESULTS_DIR`, runs are written into `$REPO/results/external_eval/`, which is committed evidence. Always run the Launch block first.
- `haplous run` records no evidence events. `inspect` prints `(no evidence events recorded for this execution)` and `timeline` prints only the scenario header, even on `insert_dropout_large` where Haplous holds and revalidates. To see interventions, record the episode (`record-episode.md`) and read `events.jsonl`.
- One trial is anecdote, not a comparison. A defensible incumbent comparison is `M4.1` (G1 incumbent) plus `M4.2` (matched campaign) in `docs/eval_program/TASKS.json`. Neither is built.
- `haplous report` prints a path and does not render anything. `--open` is not exercised here.
- `haplous eval internal|external` was not exercised by this skill. It runs whole campaigns. Do not claim it verified.
