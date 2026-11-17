# Haplous

Haplous is a physical-execution runtime for robots. It decides whether a task may run on a
machine, supervises it while it runs (holds, revalidation, recovery, aborts), and keeps an
evidence trail. This repository also holds the simulation and evaluation tooling used to test
it: a MuJoCo peg-insertion substrate (`pegsim`), the HEB benchmark, the HES evaluation system,
and **Haplous Studio**, a local browser workbench.

Everything here runs on a CPU. No GPU is needed.

## Install

Needs Python 3.12+. For working in this repository (Studio, benchmarks, the test suite), use
[uv](https://docs.astral.sh/uv/):

```bash
uv sync --extra all
```

Check the environment. `doctor` lists each extra as OK or MISSING:

```bash
.venv/bin/haplous doctor
```

The repository builds one distribution, `haplous`. Its core has **no dependencies**: the runtime
uses only the standard library. Everything else is an extra:

| Extra | Adds | For |
|---|---|---|
| (none) | nothing | the runtime: `import haplous`, your own `Machine`, `haplous doctor` |
| `sim` | mujoco, numpy | `pegsim`, the MuJoCo HEB backend, `haplous demo` |
| `eval` | `sim` + registry, replay, plots | HEB/HES evaluation, the M0 run registry, `.rrd` replays |
| `studio` | `eval` + FastAPI, uvicorn | `haplous studio` |
| `dev` | pytest, ruff, mypy | development tools |
| `all` | `eval`, `studio`, `dev` + h5py, hypothesis | the full test suite |

To use the runtime without a checkout, build the wheel and install it (nothing is published to
a package index):

```bash
uv build --wheel
pip install 'dist/haplous-0.5.0-py3-none-any.whl'          # runtime only
pip install 'dist/haplous-0.5.0-py3-none-any.whl[sim]'     # plus the MuJoCo simulator
python examples/stub_machine_contract.py                   # runtime contract on a stub machine
```

A command that needs a missing extra says which one, for example
`haplous demo needs the 'sim' extra (pip install 'haplous[sim]')`. `scripts/wheel_smoke.sh`
builds the wheel and checks it in fresh venvs (bare, `[sim]`, `[studio]`). A Studio installed
from the wheel starts and serves its API, but its run library is empty: committed runs live in
the checkout's `results/`. To uninstall, `pip uninstall haplous`.

## Haplous Studio (start here)

```bash
.venv/bin/haplous studio
```

This starts a local server on `http://127.0.0.1:8765` and opens your browser. It only listens on
your own machine. From the browser you can:

| Screen | What it does |
|---|---|
| **Runs** | Every recorded run (MuJoCo, robosuite, a real Franka arm), with outcome and whether its integrity check passes |
| **Replay** | 3D MuJoCo replay with force, pose and command charts and the event list. Click an event and the 3D view and every chart jump to that moment. Runs without 3D (robosuite, real robot) show charts and events |
| **Compare** | Haplous vs the G1 incumbent vs direct execution on the M4 campaign: paired outcome table with denominators and 95% confidence intervals, read from the committed results |
| **Launch** | Run a small CPU experiment (scenarios × systems). The CPU estimate is shown first; anything over 10 CPU-minutes asks for confirmation |
| **Evidence** | What is verified, by whom, at which commit, and what has never been run |

From a replay, **Create investigation** writes a task file under
`docs/eval_program/investigations/` with the scenario, the failure window and the exact
reproduction command. Hand it to a Claude Code session to work on.

Options: `--port N`, `--no-browser`, and `--root DIR` (write jobs, recordings, replays and
investigations under `DIR` instead of the repository).

The 3D viewer (Rerun, about 70 MB) is downloaded once on first start from a pinned npm package,
checked against its sha512, and cached in `~/.cache/haplous-studio/`. Until it arrives, or when
offline, everything except the 3D panel works.

## Command line

The CLI does everything Studio does and is what scripts and CI use.

```bash
.venv/bin/haplous demo
```

Runs the three reference executions: a success, a refusal and a bounded abort.

```bash
.venv/bin/haplous timeline results/m5/robosuite-log --rrd /tmp/t.rrd --png /tmp/t.png
```

Plots any episode log (force, pose, command, Haplous decisions, fault windows) without loading a
simulator.

```bash
.venv/bin/haplous replay m2-heb-insert_dropout_large --rrd /tmp/m2.rrd
```

Replays a recorded MuJoCo episode from its saved state stream.

```bash
.venv/bin/haplous --help
```

Lists every command (`run`, `eval`, `compare`, `report`, `inspect`, `envelope`, ...).

## Tests

```bash
.venv/bin/python -m pytest tests -q
```

The current baseline and the 4 known pre-existing failures are listed in
`docs/eval_program/KNOWN_ISSUES.md`. The Studio browser test is opt-in; see
`docs/eval_program/REPRODUCE.md` (M6.3).

## Where things are

| Path | Contents |
|---|---|
| `haplous/` | The runtime: capabilities, admission, hold, revalidation, recovery, evidence |
| `pegsim/` | MuJoCo simulation substrate |
| `benchmarks/` | HEB benchmark, G1 incumbent, recording, episode log, MCAP export, real-data import |
| `evaluation/` | M0 run registry, replay, timeline, metrics, statistics |
| `hes/` | Haplous Evaluation System: outcome axes, gates, reports |
| `studio/` | Haplous Studio server (`studio/*.py`) and frontend (`studio/web/`, built into `studio/static/`) |
| `results/` | Committed evidence (raw JSONL trials, campaign metrics, sample logs) |
| `docs/eval_program/` | Program state: `PROGRAM_STATE.md` (start here), task ledger, decisions, known issues, designs, `M4_REPORT.md` |

## What the evidence says so far

- The pre-registered M4 campaign (501 scenarios) found Haplous and the competent incumbent G1
  **tie** on bounded success: `docs/eval_program/M4_REPORT.md`.
- All results are scripted-policy simulation, plus one imported real-robot segment
  (REASSEMBLE, CC-BY-4.0) that is viewable but not scored. Nothing here is evidence about learned
  (VLA) policies or real hardware performance.
