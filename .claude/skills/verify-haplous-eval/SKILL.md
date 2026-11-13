---
name: verify-haplous-eval
description: Drive the real Haplous Eval surfaces (record a MuJoCo HEB episode into an M0 registry, replay it from the archive to .rrd/.mp4, run the M2 gate, compare systems, render HES reports) and prove a change works with archive-level evidence. Use after any change to recording, replay, M0, the haplous/hes CLIs or HES, or when asked to verify a recording or report.
---

# Verify Haplous Eval

This skill proves behavior at the surface a user touches. A passing pytest run is not proof here. Every claim needs the command, its exit code, and the files it wrote.

All surfaces are short-lived headless CLIs. There is no server to keep alive. "Launch" means preparing an isolated environment. Each drive is one process that exits. Replay always runs in a fresh process, after the recording process has exited.

## Launch

These prerequisites were needed on the reference machine (Linux, CPU only, no GPU).

| Need | Why | Check |
|---|---|---|
| The repo venv | `mujoco` 3.11, `pyarrow`, `rerun-sdk` and the `haplous`/`hes` console scripts live there | `$VENV/bin/python -c "import mujoco"` |
| `PYTHONPATH=$REPO` | The venv's editable install points at the main checkout. In a worktree, `$VENV/bin/haplous`, `$VENV/bin/hes` and `python -I` otherwise import the main checkout's code, not yours | Doctor step 1 |
| `MUJOCO_GL=osmesa` | Headless rendering of the `.mp4` (`egl` also works where available) | Drive B writes an `.mp4` |
| `ffmpeg` and `ffprobe` on `PATH` | Replay encodes the `.mp4` through `ffmpeg`. Evidence reads it with `ffprobe` | `ffprobe -version` |
| The rerun CLI from the venv | `rerun rrd verify` validates the `.rrd`. Use `$VENV/bin/rerun` (0.43.1), not a system `rerun` | `$VENV/bin/rerun --version` |

Paste this block once per shell. Every later block assumes it.

```bash
export REPO=$(git rev-parse --show-toplevel)
export VENV=${VENV:-$(git -C "$REPO" worktree list | head -1 | awk '{print $1}')/.venv}  # main checkout's .venv
export PYTHONPATH=$REPO MUJOCO_GL=osmesa
export W=$(mktemp -d -t verify-haplous-XXXXXX)            # all outputs of this run; honors TMPDIR
export HAPLOUS_RESULTS_DIR=$W/haplous_runs HAPLOUS_ARTIFACTS_DIR=$W/haplous_artifacts
export K=$REPO/.claude/skills/verify-haplous-eval
cd "$REPO"
```

Ready means the Doctor below passes. Isolation is by directory. Two verification runs can proceed side by side as long as each has its own `$W`. Never point a drive at a registry you did not create or copy, except the read-only committed one in `results/recordings/registry`.

## Doctor

Run this first, and again whenever anything looks off. It is read-only.

```bash
$VENV/bin/python -c "import haplous, evaluation, benchmarks; print(haplous.__file__)"
$VENV/bin/haplous doctor
$VENV/bin/hes doctor
$VENV/bin/rerun --version | head -1; ffprobe -version | head -1
$VENV/bin/python -m evaluation.run_registry verify --root results/recordings/registry m2-heb-insert_dropout_large
```

A healthy instance shows all of the following.

- Step 1 prints a path under `$REPO`. A path under another checkout means `PYTHONPATH` is unset and every result would test the wrong code.
- `haplous doctor` exits 0 with `haplous core OK 0.5.0`, `mujoco OK 3.11.0`, `rerun OK`. The `result directory` row names `$W/haplous_runs`. It reads `MISSING` until the first `haplous run` creates it, and doctor still exits 0. A path under `results/external_eval` means Launch did not run. `robosuite`, `maniskill/sapien`, `simplerenv`, `isaaclab`, `ContactBench` `NOT INSTALLED` and `datasets MISSING` are expected and do not block anything here.
- `hes doctor` prints `"mujoco": true`.
- `rerun-cli 0.43.1` and an `ffprobe` version line.
- The committed recording verifies with `"problems": []`. This command uses the read-only `check()`. It does not append to the ledger.

## Drive

Each feature has a recipe in [`features/`](features/README.md). The core journey, about 15 s of compute, is below.

**A. Record a HEB episode into a fresh M0 registry.** About 3 s.

```bash
$VENV/bin/python scripts/record_episode.py --root $W/registry --run-id j1
```

Exit 0. Stdout is JSON with `"state": "VERIFIED"` and `"outcome": "completed"`. `$W/registry/runs/j1/artifacts/` holds `events.jsonl`, `metadata.json`, `model.mjb`, `model.xml`, `state.arrow`, `stderr.txt`, `stdout.txt` and `telemetry.jsonl`. The default scenario is `insert_dropout_large` under `haplous_mediated`.

**B. Reopen it from the archive alone, in a new process.** About 5 s.

```bash
$VENV/bin/haplous replay j1 --recordings-root $W/registry --ui \
  --rrd $W/j1.rrd --mp4 $W/j1.mp4 --report $W/j1.report.json
```

Exit 0. Stdout ends with `viewer: rerun $W/j1.rrd`. The report has `pose_check.pass: true` with `observations_compared: 3251`, `max_abs_error_m: 0.0`, `tolerance_m: 1e-09`, and `forbidden_modules_loaded: []`. Haplous's interventions are in `$W/registry/runs/j1/artifacts/events.jsonl` (for example `temporal_hold_entered` at tick 1999 and `temporal_revalidation_resumed` at tick 2401) and appear as `events/haplous` markers in the `.rrd`.

**C. Run the full M2 gate from scratch.** About 40 s. It records a new episode, so it is independent of A and B.

```bash
$VENV/bin/python scripts/m2_gate.py --workdir $W/m2 > $W/m2_gate.json; echo rc=$?
```

`rc=0` and all 11 entries of `verdicts` are `true`. To gate the committed recording instead, add `--root results/recordings/registry --run-id m2-heb-insert_dropout_large --existing`. Always pass `--report-out` into `$W`, or omit it. Never point it at `results/`.

## Evidence

Proof standards for this repo:

- Replay from the archive in a fresh process, never from the recording process.
- Capture the command, its exit code, and the files it wrote. A verdict without files is not evidence.
- Recompute what you claim from raw files (`events.jsonl`, `telemetry.jsonl`, `trials.jsonl`, `state.arrow`). Do not quote a summary as its own proof.
- Write every output under `$W`. Never write to `results/` or `docs/` while verifying.

Run the evidence helper on any recording. The optional fourth argument is a trusted registry whose ledger is the reference.

```bash
$K/evidence.sh $W/registry j1 $W/evidence
$K/evidence.sh results/recordings/registry m2-heb-insert_dropout_large $W/evidence-committed results/recordings/registry
```

It prints one line per check and exits 0 only if all pass. A clean recording gives this output.

```text
m0_verify      PASS  problems: []
replay         PASS  {'max_abs_error_m': 0.0, 'observations_compared': 3251, 'pass': True, 'tolerance_m': 1e-09}
rrd_verify     PASS  1 file verified without error.
mp4            PASS  duration=1.833333s sim=1.825s distinct_frames=54
trust_root     PASS  artifact hashes equal the trusted ledger
```

### Which check catches what

Each row below was measured by planting the defect with `plant_defect.py` in a copy of `m2-heb-insert_dropout_large`. The last two rows use the planting helpers in `tests/test_negative_recording.py`. "Re-sealed" means the helper wrote a fresh M0 ledger over the tampered files, ending in `VERIFIED`, as a forger would.

| Planted defect | m0_verify | replay | rrd_verify | mp4 | trust_root | `m2_gate --existing` |
|---|---|---|---|---|---|---|
| `corrupt model.mjb` (byte flipped, ledger kept) | FAIL `artifact hash mismatch: model.mjb` | FAIL rc=2 `replay refused: M0 integrity check failed` | no file | no file | PASS | rc=1 at items 2 and 3 |
| `corrupt telemetry.jsonl` | FAIL `artifact hash mismatch` | FAIL rc=2 | no file | no file | not run | not run |
| `shift-pose 1000 0.001` (observed tick, re-sealed) | PASS | FAIL rc=1 `max_abs_error_m: 0.001, pass: false` | PASS | PASS | FAIL `['telemetry.jsonl']` | rc=1 at item 3 |
| `shift-state 1000 0.001` (observed tick, re-sealed) | PASS | FAIL rc=1 `max_abs_error_m: 0.001` | PASS | PASS | not run | not run |
| `shift-state 2100 0.001` (dropout tick, re-sealed) | PASS | PASS | PASS | PASS | FAIL `['state.arrow']` | **rc=0, 11/11 pass** |
| `temporal_hold_entered` removed from `events.jsonl` (re-sealed) | PASS | PASS | PASS | PASS | FAIL `['events.jsonl']` | **rc=0, 11/11 pass** |
| clock in wrong unit (`timestep_s`, `sim_time_s` or event `t_s` x1000, re-sealed) | PASS | FAIL rc=2 `replay refused: ...` naming the clock | no file | no file | FAIL (the edited file) | rc=1 at item 3 (`timestep_s` case) |

How to read it:

- `m0_verify` catches any byte change the ledger did not record. It is blind to a re-sealed ledger.
- `replay` compares the replayed `peg_tip` with telemetry at observed ticks only (3251 of 3651 here). It catches re-sealed tampering of state or telemetry at those ticks.
- State at ticks with no observation (the dropout window, ticks 2000 to 2400 here), and model changes that do not move `peg_tip`, are protected only by M0 SHA-256 hashes. This limit is documented in `docs/eval_program/KNOWN_ISSUES.md`. After a re-seal, only `trust_root` against a trusted ledger catches them. For a committed recording, the trusted ledger is the one in git, so also run `git diff --exit-code -- results/recordings`.
- Haplous events in `events.jsonl` (holds, revalidations, aborts) are protected only by M0 SHA-256 hashes. Replay checks each event's `t_s` against its tick, not whether the event happened. After a re-seal, a removed event is caught only by `trust_root`. `tests/test_negative_recording.py::test_resealed_event_removal_is_caught_only_by_trust_root` pins this limit.
- Replay refuses a clock in the wrong unit: `timestep_s` must equal the saved model's timestep, `sim_time_s` must equal tick x timestep within 1e-9 s, and each event's `t_s` must lie within half a tick of its tick.
- `rrd_verify` and `mp4` prove the derived files are well formed. They do not prove the state is genuine.

To reproduce a row:

```bash
$VENV/bin/python $K/plant_defect.py results/recordings/registry m2-heb-insert_dropout_large $W/bad/registry shift-state 2100 0.001
$K/evidence.sh $W/bad/registry m2-heb-insert_dropout_large $W/bad/out results/recordings/registry; echo rc=$?
```

The modes are `corrupt FILE`, `shift-pose TICK DZ_M` and `shift-state TICK DZ_M`. The destination must not exist. The source is only read. `shift-pose` refuses a dropout tick because it has no observation there.

## Cleanup

There are no daemons. Kill only a `rerun` viewer you started yourself, by its PID. Never kill by process name.

```bash
rm -rf "${W:?}/registry" "${W:?}/bad/registry" "${W:?}/m2/registry"   # scratch registries only
ls "$W" "$W/evidence" "$W/bad/out"                                      # evidence survives here
git -C "$REPO" status --short                                 # expect only your intended changes
```

Two traps can leave files in the checkout. Remove them if `git status` shows them.

- `haplous replay` without `--rrd`/`--mp4` writes to `$REPO/replays/<run>/`. It is gitignored, but remove it with `rm -rf "${REPO:?}/replays"`.
- A `Registry` opened on an empty root creates `ledger.jsonl` and `runs/` in the current directory. `evidence.sh` refuses a missing root, but ad-hoc Python can still do this.

## Helpers

- `evidence.sh REGISTRY_ROOT RUN_ID OUT_DIR [TRUSTED_ROOT]` runs the checks in the table above and writes `m0_verify.json`, `replay.out`, `replay_report.json`, `<run>.rrd` and `<run>.mp4` into `OUT_DIR`.
- `plant_defect.py SRC_ROOT RUN_ID DEST_ROOT MODE ARGS...` makes a tampered copy for negative proofs.

## Not built yet

Do not claim to verify these. Their owners in `docs/eval_program/TASKS.json` add them.

- `M3.1`. `constrained_slide` through the same record, replay, M0 and HES path.
- `M3.2`. A pass-through arm, and A/A against direct across both task families.
- `M4.1`. The G1 competent incumbent per task. Today's nearest system is `competent_supervisor` (see `features/compare-systems.md`). It is not G1.
- `M4.2`. A matched direct, G1 and Haplous campaign with HES metrics recomputed from raw rows.
- `M5.1`. A fresh-agent cold start.

There is also no CLI path from a recording to `haplous inspect` or `haplous timeline`. Both reject a recording (see `features/replay-recording.md`).
