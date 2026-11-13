# M2 gate

The M2 gate runs the record, exit, replay and verify slice as 11 numbered checks and prints one JSON verdict per item. A user runs it from scratch in a clean directory, or against an existing recording.

## Sub-features

- `gate-scratch` records a new episode in a workdir and gates it.
- `gate-existing` gates an already recorded run in a given registry root.
- `gate-items` covers items 1 (record), 2 (M0 `VERIFIED`), 3 (no runtime imports), 4 (pose matches), 5 (event sync), 6 (mp4), 7 (`rerun rrd verify`), 8 (hashes match M0), 9 (bounded RSS), 10 (A/A non-interference) and 11 (planted defects).

## How to get to it (user POV)

- Run `scripts/m2_gate.py --workdir <dir>` for a scratch run.
- Run `scripts/m2_gate.py --root <registry> --run-id <id> --existing --workdir <dir> [--report-out <file>]` for an existing run.

## Driving it with the CLI

Preconditions:

- Launch and Doctor from `../SKILL.md` passed.
- `$W/m2` does not exist.

- **Scratch gate.** Run `$VENV/bin/python scripts/m2_gate.py --workdir $W/m2 > $W/m2_gate.json; echo rc=$?`. `rc=0` in about 40 s. All 11 `verdicts` are `true`. `gates.4_pose_matches` has `observations_compared` 3251. `gates.6_mp4` has `frames` 55 and `distinct_frames` 54. `gates.9_bounded_rss.overhead_growth_kb` is far below `budget_kb` 2048 (312 measured).
- **Read the verdicts.** Run `$VENV/bin/python -c "import json; print(json.load(open('$W/m2_gate.json'))['verdicts'])"`. Every value is `True`.
- **Existing gate.** Run `$VENV/bin/python scripts/m2_gate.py --root results/recordings/registry --run-id m2-heb-insert_dropout_large --existing --workdir $W/m2-existing --report-out $W/m2-existing.report.json > $W/m2_existing.json; echo rc=$?`. `rc=0` in about 37 s with 11 of 11 `true`.
- **Gate a tampered copy.** Run `$VENV/bin/python $K/plant_defect.py results/recordings/registry m2-heb-insert_dropout_large $W/bad-gate/registry corrupt model.mjb`, then the existing gate with `--root $W/bad-gate/registry`. `rc=1`, and the gate stops at `3_replay` after `2_m0_verified` fails.

## Gotchas

- REPRODUCE.md's existing-run command passes `--report-out results/recordings/m2-heb-insert_dropout_large.replay_report.json`. That overwrites a committed file with new paths and a new `.rrd` hash. Redirect it into `$W` when verifying.
- Items 9 to 11 do not read the archive under test. They record their own episodes or run `tests/test_recording_replay.py`. A pass on them says nothing about the recording you passed with `--existing`.
- The gate trusts the registry's ledger. A recording re-sealed after a state change in the dropout window passes 11 of 11 (measured). Use `evidence.sh` with a trusted root for that case.
- On failure the JSON stops at the first failing stage, so later item keys are missing rather than `false`.
