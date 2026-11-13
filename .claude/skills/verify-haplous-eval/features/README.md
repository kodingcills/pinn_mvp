# Haplous Eval verification map

This directory is the maintained source for verifying the user-facing behavior of Haplous Eval. Read the index before driving anything, then use the matching feature file as the recipe.

## Baseline preconditions

- Run the Launch block in `../SKILL.md`. It sets `REPO`, `VENV`, `PYTHONPATH`, `MUJOCO_GL`, `W`, `HAPLOUS_RESULTS_DIR`, `HAPLOUS_ARTIFACTS_DIR` and `K`.
- The Doctor in `../SKILL.md` passes, and its first line prints a path under `$REPO`.
- The committed recording `results/recordings/registry` / `m2-heb-insert_dropout_large` is present and verifies with `"problems": []`.
- The committed HES run `results/hes/v3_hardening_qualification` is present.

## Driving conventions

- Run every command from `$REPO` with the venv's binaries (`$VENV/bin/python`, `$VENV/bin/haplous`, `$VENV/bin/hes`, `$VENV/bin/rerun`).
- Write every output under `$W`. Pass explicit output flags (`--rrd`, `--mp4`, `--report`, `--out`) where a command has them.
- Treat committed registries and runs as read-only inputs. To mutate one, copy it first (`plant_defect.py` does this).
- Replay in a new process after the recording process exits.

## Proof and skip reporting

- CLI proof is the command, stdout, stderr and exit code.
- A recording proof includes `evidence.sh` output for that recording (see `../SKILL.md` Evidence).
- A comparison or report proof includes the raw rows it was computed from (`trials.jsonl`, `episodes.parquet`).
- Record the feature ID and entry point used with every artifact.
- Report an unreachable path with the attempted command and the unmet precondition. Do not report a skipped entry point as verified through another path.

## Feature entry contract

Each feature file starts with an H1 title and one paragraph describing the user-visible behavior. It then uses exactly four H2 sections in this order: `Sub-features`, `How to get to it (user POV)`, `Driving it with the CLI`, `Gotchas`.

## Features

- [Record an episode](./record-episode.md) covers running a physical HEB scenario with recording on and sealing it as an M0 run.
- [Replay a recording](./replay-recording.md) covers reopening a historical execution from the archive, exporting `.rrd` and `.mp4`, and reading Haplous's interventions.
- [M2 gate](./m2-gate.md) covers the 11-item record, replay and verify gate, from scratch and on an existing recording.
- [Compare systems](./compare-systems.md) covers `haplous run`, `compare`, `inspect`, `timeline` and `report` for Haplous against the existing incumbent.
- [HES reports](./hes-reports.md) covers `hes doctor`, `analyze`, `qualify` and `report` on a committed campaign.
