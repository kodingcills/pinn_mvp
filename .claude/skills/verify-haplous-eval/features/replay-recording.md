# Replay a recording

A user reopens a historical MuJoCo execution from its archive alone, without the controller or the Haplous runtime. Replay checks the replayed pose against the recorded samples, exports a Rerun timeline and an MP4 preview, and refuses a recording whose integrity check fails.

## Sub-features

- `replay-pose` replays every tick with `mj_setState` and `mj_forward` and compares `peg_tip` with telemetry at every observed tick.
- `replay-export` writes `.rrd` and `.mp4` from the archive.
- `replay-events` shows Haplous and fault events on the same timeline as state.
- `replay-refuse` refuses a recording that fails M0, has a wrong model digest, has a clipped or gapped state stream, or records a clock in the wrong unit (`timestep_s`, `sim_time_s`, event `t_s`).
- `replay-isolation` loads none of `haplous.runtime`, `benchmarks.heb` or `pegsim.control`.

## How to get to it (user POV)

- Run `haplous replay <run_id> --recordings-root <registry> --ui [--rrd P] [--mp4 P] [--report P]`.
- Call `evaluation.replay.replay()` from Python. It reports a pose mismatch in `pose_check.pass` without raising, so check that field (see KNOWN_ISSUES).
- Open the `.rrd` in a viewer with the printed `rerun <path>` command, on a machine with a display.

## Driving it with the CLI

Preconditions:

- Launch and Doctor from `../SKILL.md` passed.
- A recording exists, either `j1` from `record-episode.md` or the committed `m2-heb-insert_dropout_large` in `results/recordings/registry`.

- **Replay and export.** Run `$VENV/bin/haplous replay m2-heb-insert_dropout_large --recordings-root results/recordings/registry --ui --rrd $W/r.rrd --mp4 $W/r.mp4 --report $W/r.json`. Exit 0 in about 5 s. The report has `n_ticks` 3651, `pose_check` `{"observations_compared": 3251, "max_abs_error_m": 0.0, "tolerance_m": 1e-09, "pass": true}` and `forbidden_modules_loaded: []`.
- **Validate the timeline.** Run `$VENV/bin/rerun rrd verify $W/r.rrd`. Output is `1 file verified without error.` and exit 0.
- **Validate the video.** Run `ffprobe -v error -show_entries format=duration -show_entries stream=nb_frames,r_frame_rate,width,height -of default=nw=1 $W/r.mp4`. Expect `640x480`, `30/1`, `nb_frames=55`, `duration=1.833333`. The report's `sim_duration_s` is 1.825.
- **Read the interventions.** Run `$VENV/bin/python -c "import json; [print(e['tick'], e['source'], e['kind']) for e in map(json.loads, open('results/recordings/registry/runs/m2-heb-insert_dropout_large/artifacts/events.jsonl'))]"`. Expect `2000 machine dropout_window_start`, `1999 haplous temporal_hold_entered`, `2401 haplous temporal_revalidation_resumed` and `3650 haplous outcome`, among 11 events. The same events are `events/haplous` and `events/machine` text logs in the `.rrd` at those ticks.
- **Refusal.** Plant a defect and replay it. See the table in `../SKILL.md` Evidence. A corrupted artifact exits 2 with `replay refused: M0 integrity check failed`. A re-sealed pose shift at an observed tick exits 1 with `pose_check.pass: false`.

## Gotchas

- Without `--rrd`/`--mp4`/`--report`, outputs go to `$REPO/replays/<run_id>/`. The path is gitignored, but delete it in Cleanup.
- The `.rrd` bytes and SHA-256 differ between two exports of the same archive. The `.mp4` is bit-exact. Compare `.rrd` files with `rerun rrd verify` and the event and pose content, never by hash.
- The pose check covers observed ticks only. Ticks in the dropout window (2000 to 2400) are protected only by M0 hashes. A re-sealed change there passes replay (measured).
- `haplous inspect <recording-id>` fails with `FileNotFoundError: run not found or ambiguous`, and `haplous timeline <events.jsonl>` fails with `JSONDecodeError: Extra data`. Neither reads recordings today. Read `events.jsonl` directly.
- Do not launch the `rerun` viewer on a headless machine. `rerun rrd verify` is the headless check.
