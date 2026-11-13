# Reproduce

Environment: `uv sync --extra all --extra dev` (rerun-sdk is declared in the `replay` and `all` extras).
Headless rendering: `MUJOCO_GL=osmesa` (or `egl`); `ffmpeg`/`ffprobe` on PATH.

- In a git worktree there is no `.venv`: use the main checkout's `.venv/bin/python` and set
  `PYTHONPATH=<worktree root>`, or the venv's `haplous`/`hes` import the main checkout's code.
- Never point `--report-out` at the committed `results/recordings/*.replay_report.json`: `.rrd`
  bytes differ between exports of the same archive, so the committed report would be rewritten.
- `hes report RUN` without `--out` overwrites `docs/HES_V0_REPORT.md`; pass `--out`.

## M2: record, exit, replay from the archive

Full gate on the committed recording (rc 0 means all 11 items pass):

```bash
.venv/bin/python scripts/m2_gate.py --root results/recordings/registry --run-id m2-heb-insert_dropout_large --existing --workdir replays/m2-heb-insert_dropout_large --report-out replays/m2-heb-insert_dropout_large/replay_report.json
```

Same gate from scratch in a clean directory (records a new episode first):

```bash
.venv/bin/python scripts/m2_gate.py --workdir /tmp/m2-clean
```

Open a recording (exports `.rrd` + `.mp4` under `replays/` and prints the `rerun` command):

```bash
.venv/bin/haplous replay m2-heb-insert_dropout_large --recordings-root results/recordings/registry --ui
```

Tests: `.venv/bin/python -m pytest tests/test_recording_replay.py -q` (10 tests).
