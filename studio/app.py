"""HTTP shell of Haplous Studio (FastAPI). Routes map 1:1 onto the API contract in
docs/eval_program/designs/M6_haplous_studio.md; the logic lives in data/jobs/investigations."""
from __future__ import annotations

import math
import subprocess
import threading
import webbrowser
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

from studio import data, investigations, jobs, viewer_wasm
from studio.data import NotFound, Paths, Unreadable

ALLOWED_HOSTS = {"localhost", "127.0.0.1"}
STATE_CHANGING = {"POST", "PUT", "PATCH", "DELETE"}
STATIC = Path(__file__).resolve().parent / "static"
NOT_BUILT = ("<!doctype html><title>Haplous Studio</title><h1>Haplous Studio</h1>"
             "<p>The frontend is not built (no <code>studio/static/index.html</code>). The API is at "
             "<a href='/api/runs'>/api/runs</a>.</p>")


class JobRequest(BaseModel):
    scenario_ids: list[str]
    systems: list[str]
    confirm: bool = False


class InvestigationRequest(BaseModel):
    run_id: str
    t_s: float
    title: str
    note: str = ""
    compare_run_id: str | None = None


def _hostname(host: str) -> str:
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def guard(method: str, headers: Any) -> tuple[int, str] | None:
    """DNS-rebinding and cross-site guards. Returns (status, reason) to refuse, else None."""
    host = headers.get("host", "")
    if _hostname(host) not in ALLOWED_HOSTS:
        return 400, f"Host {host!r} is not localhost or 127.0.0.1"
    if method in STATE_CHANGING:
        if headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
            return 415, "state-changing requests require Content-Type: application/json"
        origin = headers.get("origin")
        if origin is not None and origin != f"http://{host}":
            return 403, f"Origin {origin!r} is not this server (http://{host})"
    return None


def create_app(paths: Paths | None = None, static_dir: Path = STATIC, wasm_cache: Path | None = None) -> FastAPI:
    paths = paths or Paths.default()
    wasm_cache = wasm_cache or viewer_wasm.default_cache()
    app = FastAPI(title="Haplous Studio", docs_url=None, redoc_url=None, openapi_url=None)
    workers: dict[str, subprocess.Popen] = {}

    @app.middleware("http")
    async def _guards(request: Request, call_next):
        refused = guard(request.method, request.headers)
        if refused is not None:
            return JSONResponse({"detail": refused[1]}, status_code=refused[0])
        return await call_next(request)

    def _call(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except NotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except Unreadable as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/runs")
    def runs() -> list[dict[str, Any]]:
        return data.list_runs(paths)

    @app.get("/api/runs/{run_id:path}/rrd")
    def run_rrd(run_id: str) -> FileResponse:
        return FileResponse(_call(data.ensure_rrd, paths, run_id), media_type="application/octet-stream",
                            filename=f"{run_id}.rrd")

    @app.get("/api/runs/{run_id:path}")
    def run(run_id: str) -> dict[str, Any]:
        return _call(data.run_detail, paths, run_id)

    @app.get("/api/campaigns")
    def campaigns() -> list[dict[str, Any]]:
        return data.list_campaigns(paths)

    @app.get("/api/campaigns/{campaign_id}/pairs")
    def pairs(campaign_id: str, a: str = "haplous_mediated", b: str = "g1_incumbent",
              population: str | None = None) -> dict[str, Any]:
        return _call(data.campaign_pairs, paths, campaign_id, a, b, population)

    @app.get("/api/catalog")
    def catalog() -> dict[str, Any]:
        return {"scenarios": list(data.scenario_catalog().values()),
                "systems": [{"id": s, "cpu_s_per_episode": jobs.CPU_S_PER_EPISODE} for s in data.SYSTEMS],
                "cap_cpu_minutes": jobs.CPU_CAP_S / 60,
                "source": "benchmarks.heb.tasks.scenarios_by_id(); CPU per episode measured once (one insert_nominal "
                          "x haplous_mediated episode via studio.jobs, 2.99 s), applied to every system"}

    @app.post("/api/jobs")
    def launch(req: JobRequest) -> dict[str, Any]:
        catalog_ids = data.scenario_catalog()
        unknown = sorted({s for s in req.scenario_ids if s not in catalog_ids}
                         | {y for y in req.systems if y not in data.SYSTEMS})
        if unknown or not req.scenario_ids or not req.systems:
            raise HTTPException(400, f"unknown or missing scenario ids / systems: {unknown}; only catalog "
                                     "(DEV) scenarios and registered systems can be launched")
        scenario_ids, systems = list(dict.fromkeys(req.scenario_ids)), list(dict.fromkeys(req.systems))
        estimate = jobs.estimate_cpu_s(len(scenario_ids) * len(systems))
        if estimate > jobs.CPU_CAP_S and not req.confirm:
            return JSONResponse({"message": f"estimated {estimate / 60:.1f} CPU-minutes exceeds the "
                                            f"{jobs.CPU_CAP_S / 60:g} CPU-minute cap; resend with confirm=true",
                                 "estimate_cpu_minutes": estimate / 60, "cap_cpu_minutes": jobs.CPU_CAP_S / 60},
                                status_code=409)
        record = jobs.new_job(paths.jobs, paths.registry, scenario_ids, systems)
        workers[record["job_id"]] = jobs.start_worker(paths.jobs, record["job_id"])
        return {"job_id": record["job_id"], "estimate_cpu_s": estimate}

    @app.get("/api/jobs")
    def list_jobs() -> list[dict[str, Any]]:
        return [j for j in (jobs.view(paths.jobs, i, workers) for i in jobs.list_ids(paths.jobs)) if j is not None]

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        found = jobs.view(paths.jobs, job_id, workers) if job_id.replace("-", "").isalnum() else None
        if found is None:
            raise HTTPException(404, f"unknown job {job_id!r}")
        return found

    @app.post("/api/investigations")
    def investigate(req: InvestigationRequest) -> dict[str, Any]:
        if not math.isfinite(req.t_s) or not req.title.strip():
            raise HTTPException(400, "t_s must be finite and title non-empty")
        path = _call(investigations.write, paths, run_id=req.run_id, t_s=req.t_s, title=req.title.strip(),
                     note=req.note, compare_run_id=req.compare_run_id)
        return {"path": paths.rel(path)}

    @app.get("/api/evidence")
    def evidence() -> dict[str, Any]:
        return data.evidence(paths)

    @app.get(f"/rerun-web-viewer/{viewer_wasm.NAME}")
    def rerun_wasm() -> Response:
        found = viewer_wasm.locate(static_dir, wasm_cache)
        if found is None:
            raise HTTPException(404, f"Rerun web viewer {viewer_wasm.VERSION} wasm is not available (not built and "
                                     f"not downloaded into {wasm_cache}); charts and events still work")
        return FileResponse(found, media_type="application/wasm")

    @app.get("/api/{rest:path}")
    def api_404(rest: str) -> Response:
        raise HTTPException(404, f"no API route /api/{rest}")

    @app.get("/{rest:path}")
    def spa(rest: str) -> Response:
        index = static_dir / "index.html"
        if not index.is_file():
            return HTMLResponse(NOT_BUILT)
        target = (static_dir / rest).resolve()
        if rest and target.is_file() and target.is_relative_to(static_dir.resolve()):
            return FileResponse(target)
        return FileResponse(index)

    return app


def serve(port: int = 8765, open_browser: bool = True) -> None:
    import uvicorn

    url = f"http://127.0.0.1:{port}/"
    if open_browser:
        threading.Timer(1.0, webbrowser.open, (url,)).start()
    print(f"Haplous Studio at {url} (Ctrl-C to stop)", flush=True)
    viewer_wasm.ensure_in_background(STATIC, viewer_wasm.default_cache())
    uvicorn.run(create_app(), host="127.0.0.1", port=port, log_level="warning")
