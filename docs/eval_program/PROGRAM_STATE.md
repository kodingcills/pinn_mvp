# Program state: Haplous Eval foundation (M2 to M5)

Read this first in a new session. Then `TASKS.json` (DAG and ownership), `DECISIONS.tsv`
(append-only decision log), `KNOWN_ISSUES.md`, `REPRODUCE.md`. The mandate is
`CLAUDE_ORCHESTRATOR_LAUNCH.md` + `HAPLOUS_EVAL_PROGRAM_v2.md` in this directory.

- Integration branch: `claude/magical-curie-si1vjr`, restarted from `main` @ `4ca4e71`.
- Baseline suite on that commit: see KNOWN_ISSUES.md.
- Nothing runs between sessions. There is no background job. Resume from this file.

## Milestone status

| Milestone | Status | Proof |
|---|---|---|
| M2 record → replay → verify | **DONE.** M2.1 **verified** (gate 11/11 by a non-author at 6201f9d); M2.3 verify skill **verified** (cold-reader journey by a non-author at 0c28024) | `results/recordings/m2-heb-insert_dropout_large.replay_report.json`, `scripts/m2_gate.py`, `tests/test_recording_replay.py` |
| M3 two task packs, one pipeline | M3.1 **verified** (slide through unchanged replay/format); M3.2 running; M3.3, M3.4 pending | `results/recordings/m3-heb-slide_disturbance_recoverable_ceiling.replay_report.json`, `tests/test_slide_recording.py` |
| M4 direct / G1 / Haplous campaign | pending | |
| M5 fresh-agent cold start | pending | |

## Architecture decisions in force (details in DECISIONS.tsv)

- The recorder attaches through `benchmarks.heb.mujoco_backend.build_realization(machine_factory=...)`.
  It wraps `MuJoCoMachine`'s session. HEB, `haplous/` and `pegsim/` stay unedited for M2.
- Recording identity is an M0 run (`evaluation/run_registry.py`). No new run-id system.
- The 3D replay is `haplous replay <run> --ui`, filling today's `viewer: not wired` stub.
- The canonical record is the numeric state stream plus events. `.rrd` and `.mp4` are derived.
  Replay uses `mj_setState` + `mj_forward` only. It never steps physics or a controller.

## M2 gate

Every item must be reproduced by a verifier who did not write the code, at the head SHA.

1. One real HEB episode (a `haplous_mediated` peg-insertion scenario with an injected
   fault or window) runs with recording on, via a committed command, then the process exits.
2. Its recording is an M0 run in state `VERIFIED`. `python -m evaluation.run_registry verify`
   reports `problems: []`.
3. A fresh process replays it from the artifacts only. It imports no `haplous.runtime`,
   `benchmarks.heb` or `pegsim.control` (checked, not assumed).
4. Replayed pose of the tracked site matches the recorded numeric samples at every frame
   within a declared tolerance. The comparison is a committed script with JSON output.
5. Seeking to the tick of a recorded Haplous/fault event shows that event and the matching
   state on the same timeline (rrd event marker + state).
6. MP4: `ffprobe` duration matches recorded sim duration / chosen fps. Frames change
   (not static). Derived from the archive, not from a live run.
7. `rerun rrd verify` passes on the `.rrd`.
8. Every artifact hash matches M0. Repeating steps 1 to 7 in a clean temporary
   directory gives the same verdicts.
9. Peak RSS does not grow with rollout length beyond the configured buffers. Measured
   at two horizons, with artifact size per simulated second reported.
10. A/A: the same scenario with and without recording gives identical observations,
    commands and outcome (recorder does not perturb physics).
11. Planted defects are caught. A corrupted model asset, a clipped state stream and a
    missing sample each make replay refuse with a clear error.

## Claude Code adaptation of pstack (audited before any worker was spawned)

| pstack (Cursor) | Here |
|---|---|
| `Task` with Cursor model slugs (`grok-4.7-…`, `claude-opus-5-5-xhigh`) | `Agent` tool, `subagent_type: poteto-agent`, `model` ∈ opus / sonnet / haiku / fable |
| Verifier on a different model family | Different Claude model than the owner (no other families available) |
| `environment: "cloud"` workers, one branch per owner | `isolation: worktree`, local branch. Only the designated branch is pushed; verified commits are integrated onto it |
| `/setup-pstack` writes `~/.cursor/rules/pstack-models.mdc` | Not used. Roles: owner `opus`, verifier `sonnet`, mechanical `haiku` |
| `cursor-team-kit` `/deslop`, `control-cli` | Not available. Replaced by `ruff`, the targeted tests, and running the real CLI |
| `~/.cursor/projects/*/agent-transcripts` | Claude Code keeps transcripts under `~/.claude/projects/`. Not needed for this run |
| `/loop`, background watchers, PR event subscriptions | No persistent process. State lives in these files, and every unit ends in a commit |
| Bun/Node scripts (`watch-pr`, `check-plan`) | Not installed. Not needed |
