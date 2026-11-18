# Haplous Studio web (M6.2, integrated in M6.3)

React + TypeScript + Vite frontend for the API in `docs/eval_program/designs/M6_haplous_studio.md`.
`npm run build` writes the bundle to `../static/`, which is committed so `haplous studio` works without npm.

```bash
npm ci
npm run dev        # http://localhost:5173, /api/* answered from fixtures/ (dev only)
npm run typecheck
npm test
npm run build
npm run fixtures   # regenerate fixtures/ from committed results, mirroring the real server (asserts they agree with official_metrics.json)
npm run e2e        # Playwright on the real server; see docs/eval_program/REPRODUCE.md (M6.3)
```

Set `STUDIO_RRD_DIR=<dir with <run id>.rrd>` for `npm run dev` to serve real replays to the Rerun viewer.

## Rerun viewer wasm

`@rerun-io/web-viewer@0.43.1` ships a 70 MB `re_viewer_bg.wasm` (17.9 MB gzip). It is not committed.
The app loads it from same-origin `/rerun-web-viewer/re_viewer_bg.wasm`; `npm run build` copies it to
`../static/rerun-web-viewer/` (gitignored). Without it the Replay page shows a notice and the charts and
events still work.

## What the server provides beyond the design text (checked against `scripts/studio_fixtures.py` output in M6.3)

- SPA fallback: serve `index.html` for `/runs/...`, `/compare`, `/launch`, `/evidence`.
- `GET /api/runs` carries `ledger_state` (the M0 ledger state, or null outside M0) instead of the design's `verified`: the list does not re-hash. Detail and `/rrd` re-run the M0 check and answer 409 on failure (#23).
- Run ids may contain `/` (episode-log paths); the client sends them URL-encoded (`%2F`).
- `GET /api/campaigns` → `[{id, source, populations: [{id, n_scenarios, arms[], confirmatory: [{id, candidate, reference, decision}]}]}]`.
- Pairs: `ci95` is `[low, high]`; optional `source` (artifact paths).
- `GET /api/catalog` → `{scenarios: [{id, task, stratum, label}], systems: [{id, cpu_s_per_episode, cpu_source}], cap_cpu_minutes, source}`; `stratum` is the fault family (`physical`, `measurement`, ...), CPU per system is the M4 measured mean (`results/m4/campaign/cost.json`) or the measured fallback.
- Campaign ids are `<results subdir>-<campaign dir>`: `m4-campaign`, `m4-dryrun`.
- `POST /api/jobs` 409 body: `{message, estimate_cpu_minutes, cap_cpu_minutes}` (the client also accepts it under `detail`).
- Jobs: `{job_id, status: queued|running|succeeded|failed, progress: {done, total}, run_ids[], log_tail}`.
- `GET /api/evidence` → `{tasks: [{id, title, status, verdict, commit, owner, proof[]}], known_issues: [{id, title, status}], benchmarks: [{id, name, executed, reason?}], source}`.
