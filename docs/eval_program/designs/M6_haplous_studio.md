# Design M6: Haplous Studio, release 1

Status: **PROPOSED** 2026-11-16 from the operator's Studio brief. This is release 1 of a larger vision
(Mission Control, Benchmark Explorer, Performance Atlas, Experiment Lab, Capability Lab,
Simulation & Replay, Evidence & Releases). Release 1 ships one complete loop, not seven pages.

## Release 1 definition of done

From the browser, with no terminal after `haplous studio`:

1. Launch a small real CPU experiment (chosen scenarios × arms) and watch it complete.
2. Reopen a recorded execution: 3D MuJoCo replay, synchronized force/pose/command plots, and
   the event list. Clicking an event moves the 3D view and every chart to that sim time.
3. Compare Haplous against the incumbent (G1) and direct execution on a campaign: paired
   outcome matrix with denominators and confidence intervals from the committed metrics.
4. From an unfavourable episode, create an **engineering investigation**: a committed-ready
   task file with scenario, systems, failure window, events and the exact reproduction command.
   A Claude session picks it up and works it through the normal owner → verifier loop.
5. See evidence status: what is verified, by whom, at which commit, and what was never run.

## What is deliberately not in release 1

- Capability Lab running coding agents from a browser button. Release 1 writes the
  investigation; agents are started by a person in a Claude session. Production code is never
  changed from the browser.
- Experiment compiler / adaptive scenario synthesis, Performance Atlas, failure clustering.
  They need a claim-driven compiler that does not exist yet (M7+).
- MuJoCo Studio / WebViewer. The pinned stack is MuJoCo 3.11 (robosuite needs 3.9); we do not
  upgrade physics for a viewer. A spike may test it in an isolated process later.
- LIBERO-Dynamic / VLA runs: shown as **not executed** (GPU, operator approval).

## Architecture (reuse, no second backend)

| Layer | Choice | Reuses |
|---|---|---|
| Launcher | `haplous studio` subcommand: starts the server on 127.0.0.1, opens the browser | `haplous/cli` |
| Server | FastAPI + uvicorn (new optional extra `studio`), package `haplous_studio/` or `evaluation/studio/` | M0 `evaluation.run_registry`, `evaluation.timeline` parsing, `benchmarks.recording`, `evaluation.replay`, campaign `official_metrics.json` / `raw_rows.jsonl`, `docs/eval_program/TASKS.json` |
| Jobs | separate worker subprocesses with a persistent job record on disk; each episode is an M0 run | `benchmarks.recording` / `scripts/record_episode.py` |
| Frontend | React + TypeScript (Vite). Built bundle shipped with the package so `haplous studio` works without npm | — |
| 3D + timelines | Embedded Rerun web viewer (`@rerun-io/web-viewer`, pinned to rerun-sdk 0.43.1) with programmatic time seek; plots also drawn natively so they work for logs with no 3D (robosuite, real REASSEMBLE) | replay `.rrd` (already has 3D geometry), episode log |
| Charts | dashboard-native (one small chart library) | — |

The episode log is the primary record; 3D is shown only where a MuJoCo state stream exists.

## API contract (both owners build against this)

All JSON. Times are sim seconds. Ids are M0 run ids or log paths relative to the repo.

- `GET /api/runs` → `[{id, source, backend, scenario_id, system, disposition, bounded_success, has_3d, verified, path}]`
  from `results/recordings/registry`, `results/m5/*-log`, and Studio's own job registry.
- `GET /api/runs/{id}` → `{metadata, series: {t_s[], force_z_n[], pose_m[[x,y,z]], command_target_pose_m[[x,y,z]]}, events: [{source, kind, tick, t_s, payload}], windows: [{name, start_s, end_s}]}`
  Null observations are gaps (null entries), never zeros.
- `GET /api/runs/{id}/rrd` → the replay `.rrd` (generated once via `evaluation.replay`, cached under `replays/`); 404 when the run has no 3D.
- `GET /api/campaigns` → campaigns found under `results/m4/campaign/` (and later ones) with confirmatory verdicts.
- `GET /api/campaigns/{id}/pairs?a=haplous_mediated&b=g1_incumbent&population=...` →
  `{matrix: {both, a_only, b_only, neither}, n, diff, ci95, rows: [{scenario_id, a, b, stratum, replay_run_id|null}]}` from committed metrics and rows; numbers never recomputed in JS.
- `GET /api/catalog` → scenarios (task, id, stratum/fault family, label DEV only), systems, CPU estimate per episode.
- `POST /api/jobs {scenario_ids[], systems[], confirm: bool}` → 400 unknown ids; 409 if the CPU estimate exceeds the cap (default 10 CPU-minutes) and `confirm` is false; else `{job_id}`.
- `GET /api/jobs`, `GET /api/jobs/{id}` → status, progress, produced run ids, log tail.
- `POST /api/investigations {run_id, t_s, title, note, compare_run_id?}` → writes
  `docs/eval_program/investigations/<date>-<slug>.md` and returns its path.
- `GET /api/evidence` → tasks with status/verdict/commit from `TASKS.json`, plus known issues.

## Safety and integrity rules

- Bind 127.0.0.1 only. Reject requests whose `Host` is not localhost/127.0.0.1 (DNS-rebinding guard);
  state-changing requests require `Content-Type: application/json` and a same-origin `Origin`.
- Jobs run only allow-listed actions (record episodes for catalog scenarios and registered systems).
  No shell strings from the browser.
- Compute rule: every launch shows its CPU estimate first; above the cap it needs explicit confirmation.
- Development and confirmatory evidence are labelled separately. Sealed Tier 3 holdouts and
  confirmatory campaigns cannot be launched from Studio.
- Studio never edits program state files; investigations are new files a person commits.
- Every number displayed comes from a committed or M0-verified artifact; the UI shows its source.

## Units

| Unit | Owner scope | Exit check |
|---|---|---|
| M6.1 server + launcher + jobs | Python: `haplous studio`, API above, job worker, investigation writer, security checks; extra `studio` in pyproject | API tests (httpx TestClient) on committed artifacts; a 1-episode job produces a VERIFIED M0 run; Host/Origin guards red then green; cap refusal |
| M6.2 frontend | TypeScript app: Runs, Replay (Rerun + synced charts + event click-to-seek), Compare, Evidence, Launch, Create investigation; builds against fixtures of the contract | `npm run build` + typecheck + component tests; bundle committed under the server's static dir, size reported |
| M6.3 end-to-end | integrate both; Playwright drives the release-1 definition of done on the real server | E2E test passes headless; screenshots of each step |
