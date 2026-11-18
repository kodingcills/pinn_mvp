"""Haplous Studio server (M6.1): every endpoint on committed artifacts, one real
1-episode job end to end, the CPU cap, the Host/Origin/Content-Type guards and
the investigation file."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from evaluation.run_registry import Registry  # noqa: E402
from studio import jobs, viewer_wasm  # noqa: E402
from studio.app import create_app  # noqa: E402
from studio.data import Paths  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
M2 = "m2-heb-insert_dropout_large"
M3 = "m3-heb-slide_disturbance_recoverable_ceiling"
LOGS = ("results/m5/robosuite-log", "results/m5/reassemble-log")
BASE = "http://127.0.0.1:8765"
JSON = {"content-type": "application/json"}
PROGRAM_STATE = ("TASKS.json", "PROGRAM_STATE.md", "KNOWN_ISSUES.md", "DECISIONS.tsv")


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    paths = Paths.under(tmp_path)  # what `haplous studio --root` uses
    assert paths == Paths(REPO, tmp_path / "studio", tmp_path / "investigations", tmp_path / "replays")
    return paths


@pytest.fixture
def client(paths: Paths) -> TestClient:
    return TestClient(create_app(paths, static_dir=paths.studio / "no-static", wasm_cache=paths.studio / "wasm"),
                      base_url=BASE)


def test_artifact_root_resolution(tmp_path: Path, monkeypatch) -> None:
    """HAPLOUS_STUDIO_ROOT, else the checkout's own layout, else a per-user data dir (a wheel install)."""
    monkeypatch.delenv("HAPLOUS_STUDIO_ROOT", raising=False)
    assert Paths.default() == Paths(REPO, REPO / "results/studio", REPO / "docs/eval_program/investigations",
                                    REPO / "replays")
    site = tmp_path / "site-packages"  # no pyproject.toml: not a checkout
    site.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(sys, "platform", "linux")
    assert Paths.default(site) == Paths.under(tmp_path / "xdg/haplous-studio", repo=site)
    monkeypatch.setattr(sys, "platform", "darwin")
    assert Paths.default(site) == Paths.under(tmp_path / "home/Library/Application Support/haplous-studio", repo=site)
    monkeypatch.setenv("HAPLOUS_STUDIO_ROOT", str(tmp_path / "env"))
    assert Paths.default(site) == Paths.under(tmp_path / "env", repo=site)
    assert Paths.default().studio == tmp_path / "env/studio"


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_runs_lists_committed_recordings_and_episode_logs(client: TestClient) -> None:
    runs = {r["id"]: r for r in client.get("/api/runs").json()}
    assert {M2, M3, *LOGS} <= runs.keys()
    keys = {"id", "source", "backend", "scenario_id", "system", "disposition", "bounded_success", "has_3d",
            "ledger_state", "path"}
    assert all(set(r) == keys for r in runs.values())
    assert runs[M2] | {"path": None} == {
        "id": M2, "source": "recordings", "backend": "mujoco-3.11.0", "scenario_id": "insert_dropout_large",
        "system": "haplous_mediated", "disposition": "COMPLETE", "bounded_success": True, "has_3d": True,
        "ledger_state": "VERIFIED", "path": None}
    assert runs[M2]["path"] == f"results/recordings/registry/runs/{M2}"
    assert [(runs[i]["has_3d"], runs[i]["ledger_state"]) for i in LOGS] == [(False, None), (False, None)]
    assert runs["results/m5/robosuite-log"]["backend"] == "robosuite-1.5.2"


def test_run_detail_keeps_null_observations_as_nulls(client: TestClient) -> None:
    detail = client.get(f"/api/runs/{M2}").json()
    art = REPO / "results/recordings/registry/runs" / M2 / "artifacts"
    rows = [r for r in _jsonl(art / "telemetry.jsonl") if r["kind"] == "observation"]
    s = detail["series"]
    assert {len(v) for v in s.values()} == {len(rows)}
    nulls = [i for i, r in enumerate(rows) if r["observation"] is None]
    assert len(nulls) == 401
    assert all(s["force_z_n"][i] is None and s["pose_m"][i] is None for i in nulls)
    assert all(s["force_z_n"][i] == r["observation"]["force_z_n"] for i, r in enumerate(rows) if i not in nulls)
    assert s["t_s"][nulls[0]] == pytest.approx(1.0)
    assert s["command_target_pose_m"][0] is None and s["command_target_pose_m"][-1] is not None
    assert detail["windows"] == [{"name": "dropout", "start_s": 1.0, "end_s": 1.2}]
    events = _jsonl(art / "events.jsonl")
    assert [e["kind"] for e in detail["events"]] == [e["kind"] for e in events]
    assert set(detail["events"][0]) == {"source", "kind", "tick", "t_s", "payload"}
    assert detail["metadata"]["scenario_id"] == "insert_dropout_large" and detail["has_3d"] is True
    assert detail["m0"]["state"] == "VERIFIED" and detail["m0"]["problems"] == []


@pytest.mark.parametrize("log", LOGS)
def test_run_detail_serves_episode_logs_outside_mujoco(client: TestClient, log: str) -> None:
    detail = client.get(f"/api/runs/{log.replace('/', '%2F')}").json()  # the frontend URL-encodes ids
    assert detail["has_3d"] is False
    meta = json.loads((REPO / log / "metadata.json").read_text())
    obs = [r for r in _jsonl(REPO / log / "telemetry.jsonl") if r["kind"] == "observation"]
    assert detail["metadata"] == meta and detail["m0"] is None
    assert len(detail["series"]["t_s"]) == len(obs)
    assert detail["series"]["pose_m"] == [r["observation"]["pose_m"] for r in obs]
    assert len(detail["events"]) == len(_jsonl(REPO / log / "events.jsonl"))
    assert client.get(f"/api/runs/{log}/rrd").status_code == 404


def test_unknown_run_is_404(client: TestClient) -> None:
    assert client.get("/api/runs/no-such-run").status_code == 404
    assert client.get("/api/runs/no-such-run/rrd").status_code == 404
    assert client.get("/api/no-such-route").status_code == 404


def test_rrd_is_derived_once_and_cached(client: TestClient, paths: Paths) -> None:
    first = client.get(f"/api/runs/{M2}/rrd")
    assert first.status_code == 200 and first.content[:4] == b"RRF2"
    cached = paths.replays / M2 / f"{M2}.rrd"
    assert cached.read_bytes() == first.content
    mtime = cached.stat().st_mtime_ns
    assert client.get(f"/api/runs/{M2}/rrd").content == first.content
    assert cached.stat().st_mtime_ns == mtime


def test_corrupt_run_is_refused_despite_a_cached_rrd_and_not_listed_as_verified(tmp_path: Path) -> None:
    """Issue #23: one flipped telemetry byte. The ledger still says VERIFIED; the list says only that,
    and detail and replay both refuse, even with a .rrd cached from before the corruption."""
    repo = tmp_path / "repo"
    shutil.copytree(REPO / "results/recordings/registry", repo / "results/recordings/registry")
    paths = Paths(repo, tmp_path / "studio", tmp_path / "investigations", tmp_path / "replays")
    cached = paths.replays / M2 / f"{M2}.rrd"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"RRF2 cached before the corruption")
    client = TestClient(create_app(paths, static_dir=tmp_path / "no-static", wasm_cache=tmp_path / "wasm"),
                        base_url=BASE)
    assert client.get(f"/api/runs/{M2}/rrd").content == cached.read_bytes()  # intact: the cache is served

    telemetry = repo / "results/recordings/registry/runs" / M2 / "artifacts/telemetry.jsonl"
    raw = bytearray(telemetry.read_bytes())
    raw[100] ^= 1
    telemetry.write_bytes(bytes(raw))

    listed = {r["id"]: r for r in client.get("/api/runs").json()}[M2]
    assert listed["ledger_state"] == "VERIFIED" and "verified" not in listed
    for url in (f"/api/runs/{M2}", f"/api/runs/{M2}/rrd"):
        res = client.get(url)
        assert res.status_code == 409 and "artifact hash mismatch: telemetry.jsonl" in res.json()["detail"], url
    assert client.get(f"/api/runs/{M3}").status_code == 200  # the intact sibling in the same registry


def test_campaign_pairs_are_the_committed_numbers(client: TestClient) -> None:
    campaigns = {c["id"]: c for c in client.get("/api/campaigns").json()}
    m4 = campaigns["m4-campaign"]
    assert (m4["label"], m4["launchable"], m4["source"]) == (
        "confirmatory", False, "results/m4/campaign/official_metrics.json")
    official = json.loads((REPO / "results/m4/campaign/official_metrics.json").read_text())
    pops = {p["id"]: p for p in m4["populations"]}
    checked = 0
    for pop, body in official["populations"].items():
        assert pops[pop]["n_scenarios"] == body["n_scenarios"]
        assert [(c["id"], c["candidate"], c["reference"], c["decision"]) for c in pops[pop]["confirmatory"]] == [
            (k, c["candidate"], c["reference"], c["decision"]) for k, c in body["confirmatory"].items()]
        for key, pair in body["paired"].items():
            a, b = key.split("_vs_")
            got = client.get("/api/campaigns/m4-campaign/pairs", params={"a": a, "b": b, "population": pop}).json()
            delta = pair["delta_bounded_success"]
            assert got["matrix"] == {"both": pair["both"], "a_only": len(pair["rescued"]),
                                     "b_only": len(pair["harmed"]), "neither": pair["neither"]}
            assert (got["n"], got["diff"], got["ci95"]) == (delta["n_pairs"], delta["delta"],
                                                            [delta["ci_low"], delta["ci_high"]])
            rows = got["rows"]
            assert len(rows) == body["n_scenarios"] == got["n"]
            from_rows = {"both": sum(r["a"] and r["b"] for r in rows), "a_only": sum(r["a"] and not r["b"] for r in rows),
                         "b_only": sum(r["b"] and not r["a"] for r in rows),
                         "neither": sum(not r["a"] and not r["b"] for r in rows)}
            assert from_rows == got["matrix"]  # rows and committed metrics agree
            assert all(r["stratum"] in body["strata"] for r in rows)
            checked += 1
    assert checked == 8
    flipped = client.get("/api/campaigns/m4-campaign/pairs",
                         params={"a": "bare_behavior", "b": "haplous_mediated", "population": "stress"}).json()
    assert flipped["matrix"]["a_only"] == 24 and flipped["matrix"]["b_only"] == 20
    assert flipped["diff"] == 0.0125 and flipped["ci95"] == [-0.028125, 0.053125]


def test_campaign_pairs_refuse_what_is_not_committed(client: TestClient) -> None:
    assert client.get("/api/campaigns/nope/pairs").status_code == 404
    assert client.get("/api/campaigns/m4-campaign/pairs", params={"population": "nope"}).status_code == 404
    assert client.get("/api/campaigns/m4-campaign/pairs",
                      params={"a": "g1_incumbent", "b": "competent_supervisor"}).status_code == 404


def test_catalog_is_the_heb_registry(client: TestClient) -> None:
    from benchmarks.heb import tasks
    from benchmarks.heb.systems import system_by_name

    catalog = client.get("/api/catalog").json()
    assert [s["id"] for s in catalog["scenarios"]] == list(tasks.scenarios_by_id())
    assert {s["label"] for s in catalog["scenarios"]} == {"DEV"}
    assert catalog["scenarios"][0] == {"task": "peg_insertion", "id": "insert_nominal", "stratum": "physical",
                                       "label": "DEV"}
    cost = json.loads((REPO / jobs.COST_JSON).read_text())["cpu_s_per_episode_by_arm"]
    for system in catalog["systems"]:
        assert system_by_name(system["id"]).name == system["id"]
        expected = cost[system["id"]]["mean"] if system["id"] in cost else 3.0  # measured fallback, pinned independently of jobs.py
        assert system["cpu_s_per_episode"] == expected
        assert (jobs.COST_JSON in system["cpu_source"]) == (system["id"] in cost)
    assert {s["id"] for s in catalog["systems"]} - set(cost) == {"direct_minimal"}
    assert catalog["cap_cpu_minutes"] == 10.0


def test_evidence_reads_tasks_and_known_issues(client: TestClient) -> None:
    ev = client.get("/api/evidence").json()
    tasks_doc = json.loads((REPO / "docs/eval_program/TASKS.json").read_text())
    assert [t["id"] for t in ev["tasks"]] == [t["id"] for t in tasks_doc["tasks"]]
    m21 = next(t for t in ev["tasks"] if t["id"] == "M2.1")
    assert (m21["status"], m21["commit"]) == ("verified", "6201f9d4e6b4d554ffdc310264aaff278cb28247")
    assert all(isinstance(t["proof"], list) for t in ev["tasks"])
    failing = [i["id"] for i in ev["known_issues"] if i["status"] == "known failure"]
    assert len(failing) == 4 and "tests/test_rollout.py::TestRunEpisode::test_capture_localization_error_within_clearance" in failing
    assert all({"id", "title", "status"} <= set(i) and i["id"] and i["title"] for i in ev["known_issues"])
    libero = next(b for b in ev["benchmarks"] if b["id"] == "libero-dynamic")
    assert libero["executed"] is False and libero["reason"]


# ---------------------------------------------------------------- guards


@pytest.mark.parametrize("host", ["127.0.0.1:8765", "localhost:8765", "localhost", "127.0.0.1"])
def test_host_guard_accepts_localhost(client: TestClient, host: str) -> None:
    assert client.get("/api/catalog", headers={"host": host}).status_code == 200


@pytest.mark.parametrize("host", ["evil.example:8765", "evil.example", "127.0.0.1.evil.example:8765", "0.0.0.0:8765"])
def test_host_guard_refuses_rebinding(client: TestClient, host: str) -> None:
    assert client.get("/api/runs", headers={"host": host}).status_code == 400


def test_state_changing_requests_need_json(client: TestClient) -> None:
    body = json.dumps({"scenario_ids": ["insert_nominal"], "systems": ["nope"]})
    assert client.post("/api/jobs", content=body, headers={"content-type": "text/plain"}).status_code == 415
    assert client.post("/api/jobs", content=body).status_code == 415
    assert client.post("/api/jobs", content=body, headers=JSON).status_code == 400  # past the guard


def test_state_changing_requests_need_same_origin(client: TestClient) -> None:
    body = {"scenario_ids": ["insert_nominal"], "systems": ["nope"]}
    assert client.post("/api/jobs", json=body, headers={"origin": "http://evil.example"}).status_code == 403
    assert client.post("/api/jobs", json=body, headers={"origin": "http://127.0.0.1:9999"}).status_code == 403
    assert client.post("/api/jobs", json=body, headers={"origin": BASE}).status_code == 400  # past the guard
    assert client.post("/api/jobs", json=body).status_code == 400  # no Origin: non-browser client


# ---------------------------------------------------------------- jobs


def test_jobs_accept_only_catalog_scenarios_and_registered_systems(client: TestClient, paths: Paths) -> None:
    confirmatory = "fleet_insert_000137"  # an M4 campaign scenario: not launchable from Studio
    for body in ({"scenario_ids": [confirmatory], "systems": ["haplous_mediated"]},
                 {"scenario_ids": ["insert_nominal"], "systems": ["rm -rf /"]},
                 {"scenario_ids": [], "systems": ["haplous_mediated"]}):
        assert client.post("/api/jobs", json=body).status_code == 400
    assert not paths.jobs.exists()


def test_jobs_over_the_cpu_cap_need_confirm(client: TestClient, paths: Paths) -> None:
    """The real cap and the real per-system estimate: every catalog scenario x every system is over it."""
    catalog = client.get("/api/catalog").json()
    ids, systems = [s["id"] for s in catalog["scenarios"]], [s["id"] for s in catalog["systems"]]
    res = client.post("/api/jobs", json={"scenario_ids": ids, "systems": systems, "confirm": False})
    assert res.status_code == 409
    body = res.json()
    per_scenario = sum(s["cpu_s_per_episode"] for s in catalog["systems"])
    assert body["estimate_cpu_minutes"] == pytest.approx(len(ids) * per_scenario / 60)
    assert body["estimate_cpu_minutes"] > body["cap_cpu_minutes"] == 10.0
    # the cap is a real boundary: every scenario with one system stays under it
    assert jobs.estimate_cpu_s(ids, systems[:1]) < jobs.CPU_CAP_S
    assert not paths.jobs.exists()


def _app(paths: Paths) -> TestClient:
    """A fresh server process's view: no in-memory worker handles."""
    return TestClient(create_app(paths, static_dir=paths.studio / "no-static", wasm_cache=paths.studio / "wasm"),
                      base_url=BASE)


def test_one_episode_job_without_a_checkout(tmp_path: Path) -> None:
    """One short recorded episode with no committed results (a wheel install's view): launch, list, open,
    replay, then a restarted server shows the completed job unchanged."""
    (tmp_path / "no-checkout").mkdir()
    paths = Paths.under(tmp_path / "root", repo=tmp_path / "no-checkout")
    client = _app(paths)
    assert client.get("/api/runs").json() == []
    assert client.get("/api/evidence").json()["tasks"] == []
    res = client.post("/api/jobs", json={"scenario_ids": ["insert_nominal"], "systems": ["haplous_mediated"]})
    assert res.status_code == 200, res.text
    job_id = res.json()["job_id"]
    deadline = time.monotonic() + 600
    while (job := client.get(f"/api/jobs/{job_id}").json())["status"] in ("queued", "running"):
        assert time.monotonic() < deadline, job
        time.sleep(0.5)
    assert job["status"] == "succeeded", job
    assert job["progress"] == {"done": 1, "total": 1}
    assert isinstance(job["log_tail"], str) and '"state": "VERIFIED"' in job["log_tail"]
    (run_id,) = job["run_ids"]
    assert [j["job_id"] for j in client.get("/api/jobs").json()] == [job_id]
    assert Registry(paths.registry).check(run_id) == []
    assert Registry(paths.registry).state(run_id) == "VERIFIED"
    listed = {r["id"]: r for r in client.get("/api/runs").json()}[run_id]
    assert [r["id"] for r in client.get("/api/runs").json()] == [run_id]
    assert (listed["source"], listed["ledger_state"], listed["has_3d"]) == ("studio", "VERIFIED", True)
    assert (listed["scenario_id"], listed["system"]) == ("insert_nominal", "haplous_mediated")
    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["m0"]["state"] == "VERIFIED" and detail["m0"]["config"]["studio_job"] == job_id
    assert len(detail["series"]["t_s"]) == detail["metadata"]["n_observations"] > 0 and detail["events"]
    assert client.get(f"/api/runs/{run_id}/rrd").content[:4] == b"RRF2"
    print(f"measured CPU per episode: {job['episodes'][0]['cpu_s']:.2f} s")
    record_bytes = (paths.jobs / f"{job_id}.json").read_bytes()
    restarted = _app(paths)
    assert restarted.get(f"/api/jobs/{job_id}").json() == client.get(f"/api/jobs/{job_id}").json()
    assert (paths.jobs / f"{job_id}.json").read_bytes() == record_bytes


def _running_record(paths: Paths, job_id: str, pid: int) -> dict:
    record = {"job_id": job_id, "created_at": "2026-01-01T00:00:00Z", "status": "running",
              "scenario_ids": ["insert_nominal"], "systems": ["haplous_mediated"], "registry": str(paths.registry),
              "estimate_cpu_s": 3.0, "nonce": "n" * 32, "pid": pid, "error": None, "finished_at": None,
              "episodes": [{"scenario_id": "insert_nominal", "system": "haplous_mediated", "run_id": "r",
                            "state": None, "outcome": None, "cpu_s": None}]}
    paths.jobs.mkdir(parents=True, exist_ok=True)
    (paths.jobs / f"{job_id}.json").write_text(json.dumps(record))
    return record


def _beat(paths: Paths, job_id: str, nonce: str, age_s: float) -> None:
    (paths.jobs / f"{job_id}.heartbeat").write_text(json.dumps({"nonce": nonce, "pid": 1, "t": time.time() - age_s}))


def test_a_live_unrelated_pid_is_not_a_live_worker(client: TestClient, paths: Paths) -> None:
    """Only a fresh heartbeat carrying the record's nonce proves the worker; the pid proves nothing."""
    _running_record(paths, "20260101T000000-aaaaaa", os.getpid())  # this test process: alive, not a worker

    def job() -> dict:
        return client.get("/api/jobs/20260101T000000-aaaaaa").json()

    assert (job()["status"], "no worker heartbeat" in job()["error"]) == ("failed", True)
    _beat(paths, "20260101T000000-aaaaaa", "x" * 32, 0)
    assert (job()["status"], "nonce" in job()["error"]) == ("failed", True)
    _beat(paths, "20260101T000000-aaaaaa", "n" * 32, jobs.STALE_S + 5)
    assert (job()["status"], "stale" in job()["error"]) == ("failed", True)
    _beat(paths, "20260101T000000-aaaaaa", "n" * 32, 0)
    assert (job()["status"], job()["error"]) == ("running", None)


def test_completed_job_survives_restart_unchanged(paths: Paths) -> None:
    record = {**_running_record(paths, "20260101T000000-bbbbbb", 999_999_999), "status": "succeeded",
              "finished_at": "2026-01-01T00:01:00Z"}
    record["episodes"][0].update(state="VERIFIED", outcome="completed", cpu_s=3.0)
    (paths.jobs / "20260101T000000-bbbbbb.json").write_text(json.dumps(record))
    views = [_app(paths).get("/api/jobs/20260101T000000-bbbbbb").json() for _ in range(2)]
    assert views[0] == views[1] == {**record, "progress": {"done": 1, "total": 1}, "run_ids": ["r"], "log_tail": ""}


def test_corrupt_or_partial_job_record_reads_failed(client: TestClient, paths: Paths) -> None:
    paths.jobs.mkdir(parents=True)
    (paths.jobs / "20260101T000000-cccccc.json").write_text('{"job_id": "20260101T000000-cccccc", "sta')
    (paths.jobs / "20260101T000000-dddddd.json").write_text("[]")
    (paths.jobs / "20260101T000000-cccccc.json.tmp").write_text("{")  # a write that never got renamed
    listed = client.get("/api/jobs").json()
    assert [(j["job_id"], j["status"]) for j in listed] == [("20260101T000000-dddddd", "failed"),
                                                            ("20260101T000000-cccccc", "failed")]
    got = client.get("/api/jobs/20260101T000000-cccccc").json()
    assert "unreadable" in got["error"] and got["progress"] == {"done": 0, "total": 0}


def test_killed_worker_reads_failed_after_restart(client: TestClient, paths: Paths, monkeypatch) -> None:
    """Kill the real worker (and its recording child) mid-job; a restarted server reports it failed."""
    res = client.post("/api/jobs", json={"scenario_ids": ["insert_nominal"], "systems": ["haplous_mediated"]})
    job_id = res.json()["job_id"]
    beat = paths.jobs / f"{job_id}.heartbeat"
    deadline = time.monotonic() + 120
    while not beat.is_file() or client.get(f"/api/jobs/{job_id}").json()["status"] != "running":
        assert time.monotonic() < deadline, client.get(f"/api/jobs/{job_id}").json()
        time.sleep(0.2)
    pid = json.loads((paths.jobs / f"{job_id}.json").read_text())["pid"]
    os.killpg(pid, signal.SIGKILL)  # the worker leads its own session; its zombie pid stays until reaped
    monkeypatch.setattr(jobs, "STALE_S", 1.0)
    time.sleep(1.5)
    job = _app(paths).get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "failed" and "stale" in job["error"], job
    assert json.loads((paths.jobs / f"{job_id}.json").read_text())["status"] == "running"  # the view never writes


# ---------------------------------------------------------------- investigations


def _digests() -> dict[str, str]:
    return {n: hashlib.sha256((REPO / "docs/eval_program" / n).read_bytes()).hexdigest() for n in PROGRAM_STATE}


def test_investigation_file_content(client: TestClient, paths: Paths) -> None:
    before = _digests()
    res = client.post("/api/investigations", json={"run_id": M2, "t_s": 1.1, "title": "Dropout hold at 1.1 s?",
                                                   "note": "Why does the hold end here?", "compare_run_id": M3})
    assert res.status_code == 200, res.text
    path = Path(res.json()["path"])
    assert path.parent == paths.investigations and path.name.endswith("-dropout-hold-at-1-1-s.md")
    text = path.read_text(encoding="utf-8")
    for needle in ("# Investigation: Dropout hold at 1.1 s?", "Why does the hold end here?",
                   "- Scenario: `insert_dropout_large`", "`haplous_mediated`", f"## Run: `{M2}`",
                   f"## Compared run: `{M3}`", "- `dropout`: 1.0000 s to 1.2000 s", "| dropout_window_start |",
                   "| dropout_window_end |", "recorded at commit `624865d13dd2d612946ce4f8333666b80dd01e7e`",
                   "git checkout 624865d13dd2d612946ce4f8333666b80dd01e7e",
                   ".venv/bin/python -m benchmarks.recording --scenario insert_dropout_large --chunk-ticks 1024",
                   f"--run-id {M2}-repro --scenario insert_dropout_large --system haplous_mediated",
                   f"evaluation.replay {M2} --root results/recordings/registry"):
        assert needle in text, needle
    events = _jsonl(REPO / "results/recordings/registry/runs" / M2 / "artifacts/events.jsonl")
    outside = [e["kind"] for e in events if abs(e["t_s"] - 1.1) > 0.5]
    assert outside and all(f"| {k} |" not in text.split("## Compared run")[0] for k in outside)
    again = client.post("/api/investigations", json={"run_id": M2, "t_s": 1.1, "title": "Dropout hold at 1.1 s?"})
    assert again.json()["path"].endswith("-2.md")
    assert _digests() == before


def test_investigation_refuses_bad_input(client: TestClient, paths: Paths) -> None:
    assert client.post("/api/investigations", json={"run_id": "nope", "t_s": 1.0, "title": "x"}).status_code == 404
    assert client.post("/api/investigations", json={"run_id": M2, "t_s": 1.0, "title": " "}).status_code == 400
    assert not paths.investigations.exists()


# ---------------------------------------------------------------- static, CLI


def test_spa_fallback_and_not_built_page(paths: Paths) -> None:
    assert "frontend is not built" in TestClient(create_app(paths, static_dir=paths.studio / "x"),
                                                 base_url=BASE).get("/runs/abc").text
    static = paths.studio / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<html>app</html>")
    (static / "assets" / "app.js").write_text("console.log(1)")
    (paths.studio / "secret.txt").write_text("secret")
    client = TestClient(create_app(paths, static_dir=static), base_url=BASE)
    assert client.get("/").text == "<html>app</html>"
    assert client.get("/replay/m2").text == "<html>app</html>"
    assert client.get("/assets/app.js").text == "console.log(1)"
    assert client.get("/..%2Fsecret.txt").text == "<html>app</html>"


def _fake_tarball(wasm: bytes) -> tuple[bytes, str]:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, payload in ((viewer_wasm.MEMBER, wasm), ("package/package.json", b"{}")):
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
    data = buf.getvalue()
    return data, "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode()


def test_viewer_wasm_integrity_match_is_cached_and_mismatch_refused(tmp_path: Path, monkeypatch) -> None:
    tarball, integrity = _fake_tarball(b"\0asm-fake")
    cache = tmp_path / "cache"
    with pytest.raises(viewer_wasm.IntegrityError):
        viewer_wasm.install(tarball, cache)  # the pinned real integrity: a fake tarball never matches
    monkeypatch.setattr(viewer_wasm, "INTEGRITY", integrity[:-4] + "AAA=")
    urls: list[str] = []
    assert viewer_wasm.fetch(cache, download=lambda url: urls.append(url) or tarball, log=lambda _m: None) is None
    assert urls == [viewer_wasm.TARBALL_URL] and not (cache / viewer_wasm.NAME).exists()
    monkeypatch.setattr(viewer_wasm, "INTEGRITY", integrity)
    path = viewer_wasm.fetch(cache, download=lambda url: tarball, log=lambda _m: None)
    assert path == cache / viewer_wasm.NAME and path.read_bytes() == b"\0asm-fake"
    assert sorted(p.name for p in cache.iterdir()) == [viewer_wasm.NAME]


def test_viewer_wasm_route_prefers_static_then_cache(paths: Paths) -> None:
    static, cache = paths.studio / "static", paths.studio / "wasm"
    client = TestClient(create_app(paths, static_dir=static, wasm_cache=cache), base_url=BASE)
    url = f"/rerun-web-viewer/{viewer_wasm.NAME}"
    missing = client.get(url)
    assert missing.status_code == 404 and "charts and events still work" in missing.json()["detail"]
    cache.mkdir(parents=True)
    (cache / viewer_wasm.NAME).write_bytes(b"cached")
    got = client.get(url)
    assert (got.content, got.headers["content-type"]) == (b"cached", "application/wasm")
    (static / "rerun-web-viewer").mkdir(parents=True)
    (static / "rerun-web-viewer" / viewer_wasm.NAME).write_bytes(b"built")
    assert client.get(url).content == b"built"


def test_cli_studio_is_wired_and_lazy() -> None:
    code = ("import sys, haplous.cli\n"
            "assert not [m for m in sys.modules if m.split('.')[0] in ('fastapi', 'uvicorn', 'studio')]\n"
            "haplous.cli._parser().parse_args(['studio', '--port', '9', '--no-browser'])\n")
    subprocess.run([sys.executable, "-c", code], cwd=REPO, check=True)
