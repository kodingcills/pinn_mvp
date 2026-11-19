"""E19 step 1: diagnose the process-force exceedance in an M4 stale_long case.

Reproduces both guarded arms of one frozen M4 stress scenario through the
committed `scripts/m4_campaign.py record-one` path into a scratch store,
checks the peak force against the committed row, reads target / pose / force /
stiffness around hold entry from the recording, and runs one counterfactual
per arm in which the machine re-evaluates its impedance law on the last
accepted target every tick during a hold (a closed-loop hold) instead of
replaying the frozen joint wrench. It also scans every committed M4 stale_long
and rejection_long row for where the force exceedance falls relative to the
fault window. No simulation of the M4 population beyond the one case.

    MUJOCO_GL=osmesa PYTHONPATH=. .venv/bin/python scripts/e19_diagnose.py \
        --store /tmp/.../m19 --scenario-id m4s_stale_long_00116

Writes results/e19/diagnosis/summary.json. Timelines are rendered from the
store with `python -m haplous.cli timeline <store>/<case>_<arm> --png ...`.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
ROWS = REPO / "results" / "m4" / "campaign" / "raw_rows.jsonl"
OUT = REPO / "results" / "e19" / "diagnosis" / "summary.json"
ARMS = ("haplous_mediated", "g1_incumbent")


def committed_rows(scenario_id: str) -> dict[str, dict]:
    rows = (json.loads(line) for line in ROWS.read_text().splitlines())
    return {r["arm"]: r for r in rows if r["scenario_id"] == scenario_id}


def record(store: Path, scenario_id: str, arm: str) -> Path:
    out = store / f"{scenario_id.rsplit('_', 1)[-1]}_{arm}"
    if not (out / "metadata.json").exists():
        out.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [sys.executable, str(REPO / "scripts" / "m4_campaign.py"), "record-one", "--population", "stress",
             "--scenario-id", scenario_id, "--arm", arm],
            check=True, env={**os.environ, "M0_ARTIFACT_DIR": str(out)})
    return out


def read_recording(path: Path) -> dict:
    meta = json.loads((path / "metadata.json").read_text())
    events = [json.loads(line) for line in (path / "events.jsonl").read_text().splitlines()]
    dt = meta["timestep_s"]
    obs, cmds = [], []
    for line in (path / "telemetry.jsonl").read_text().splitlines():
        r = json.loads(line)
        (obs if r["kind"] == "observation" else cmds).append(r)
    window = next(e["payload"] for e in events if e["kind"] == "stale_window_start")
    hold = next((e for e in events if e["kind"] == "temporal_hold_entered"), None)
    hold_tick = hold["tick"] if hold else round(window["start_s"] / dt)
    last_cmd = max((c for c in cmds if c["tick"] < hold_tick), key=lambda c: c["tick"])
    in_hold_cmds = [c for c in cmds if hold_tick <= c["tick"] and c["tick"] * dt <= window["end_s"]]
    peak = max(obs, key=lambda o: abs(o["observation"]["force_z_n"]))
    first_contact = next(o for o in obs if o["tick"] >= hold_tick and o["observation"]["extra"]["in_contact"])
    # MuJoCo auto-resets on divergence: sim time jumps back while the tick counter keeps going.
    reset = next((o for o in obs if o["observation"]["stamp"]["source_s"] < o["tick"] * dt - 1e-6), None)

    def at(tick: int) -> dict:
        o = min(obs, key=lambda o: abs(o["tick"] - tick))["observation"]
        return {"t_s": round(tick * dt, 4), "pose_m": o["pose_m"], "force_z_n": o["force_z_n"],
                "tilt_deg": o["tilt_deg"], "in_contact": o["extra"]["in_contact"]}

    return {
        "outcome": meta["outcome"],
        "stale_window_s": [window["start_s"], window["end_s"]],
        "hold_entered": {"t_s": round(hold_tick * dt, 4), "source": "haplous evidence" if hold else "window start (G1 emits no hold event)"},
        "hold_exited": next(({"t_s": e["t_s"], "kind": e["kind"]} for e in events
                             if e["kind"] in ("temporal_revalidation_resumed", "outcome")), None),
        "last_command_before_hold": {"t_s": round(last_cmd["tick"] * dt, 4), **last_cmd["command"]},
        "commands_applied_during_hold_window": len(in_hold_cmds),
        "state_at_hold_entry": at(hold_tick),
        "first_contact_during_hold": at(first_contact["tick"]),
        "tilt_trace_during_hold": [at(round(t / dt)) for t in (0.54, 0.58, 0.62, 0.66, 0.70, 0.74)],
        "peak_force": at(peak["tick"]),
        "mujoco_auto_reset": None if reset is None else {"tick": reset["tick"], "t_tick_s": round(reset["tick"] * dt, 4),
                                                         "pre_reset_state": at(reset["tick"] - 1)},
        "applied_command_count": len(cmds),
    }


@contextlib.contextmanager
def closed_loop_hold():
    """Probe only: a withheld tick re-applies the last accepted command, so the
    servo law runs on fresh plant state against the held target. Re-armed
    ticks are not logged as applied commands (the policy issued nothing)."""
    from haplous.adapters import mujoco_adapter as ma

    session = ma._MuJoCoSession
    orig_apply, orig_step = session.apply, session.step

    def apply(self, command):
        outcome = orig_apply(self, command)
        if outcome.accepted:
            self._e19_last = command
        return outcome

    def step(self):
        rearmed = self._pending is None and getattr(self, "_e19_last", None) is not None and not self._stopped
        if rearmed:
            self._pending = self._e19_last
        n = len(self.applied_command_times)
        orig_step(self)
        if rearmed and len(self.applied_command_times) > n:
            self.applied_command_times.pop()

    session.apply, session.step = apply, step
    try:
        yield
    finally:
        session.apply, session.step = orig_apply, orig_step


def counterfactual(scenario, arm: str) -> dict:
    from benchmarks.heb import mujoco_backend
    from benchmarks.heb.metrics import grade_run
    from benchmarks.heb.systems import system_by_name

    with closed_loop_hold():
        realization = mujoco_backend.build_realization(scenario)
        run = system_by_name(arm).run(realization, scenario)
        graded, task_success, bounded, violations, metrics = grade_run(scenario, realization.bundle, run)
    return {"disposition": run.disposition, "reason": run.reason, "graded_disposition": graded,
            "task_success": task_success, "bounded_success": bounded,
            "peak_abs_force_n": metrics.get("peak_abs_force_n"), "max_tilt_deg": metrics.get("max_tilt_deg"),
            "hard_violations": [v["code"] if isinstance(v, dict) else v.code for v in violations]}


def m4_window_scan() -> list[dict]:
    from benchmarks.stress_population import stress_population

    pop = {s.id: s for s in stress_population(320, 1113)}
    out = []
    for line in ROWS.read_text().splitlines():
        r = json.loads(line)
        s = pop.get(r["scenario_id"])
        pf = [v for v in r["hard_violations"] if v["code"] == "process_force"]
        windows = s and (s.stale_windows_s or s.command_rejection_windows_s or s.dropout_windows_s)
        if not windows or not pf or r["arm"] not in (*ARMS, "bare_behavior"):
            continue
        (t0, t1, *_), = windows
        out.append({"scenario_id": s.id, "arm": r["arm"], "window_s": [t0, t1], "violation_t_s": pf[0]["t_s"],
                    "value_n": pf[0]["value"], "inside_window": t0 <= pf[0]["t_s"] <= t1,
                    "max_tilt_deg": r["task_metrics"]["max_tilt_deg"], "reason": r["reason"]})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--store", type=Path, required=True)
    ap.add_argument("--scenario-id", default="m4s_stale_long_00116")
    args = ap.parse_args()

    from benchmarks.stress_population import stress_population

    scenario = next(s for s in stress_population(320, 1113) if s.id == args.scenario_id)
    rows = committed_rows(args.scenario_id)
    arms = {}
    for arm in ARMS:
        rec = read_recording(record(args.store, args.scenario_id, arm))
        committed = rows[arm]
        rec["committed_peak_abs_force_n"] = committed["task_metrics"]["peak_abs_force_n"]
        rec["peak_matches_committed"] = abs(rec["peak_force"]["force_z_n"]) == committed["task_metrics"]["peak_abs_force_n"]
        rec["closed_loop_hold_counterfactual"] = counterfactual(scenario, arm)
        arms[arm] = rec
    scan = m4_window_scan()
    summary = {
        "scenario_id": args.scenario_id,
        "population": "m4_stress@v1 (frozen; one case reproduced for diagnosis only)",
        "arms": arms,
        "m4_force_exceedances_vs_fault_window": {
            "n_rows": len(scan),
            "n_inside_window": sum(r["inside_window"] for r in scan),
            "rows": scan,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps({a: {"peak": arms[a]["peak_force"], "match": arms[a]["peak_matches_committed"],
                          "cf": arms[a]["closed_loop_hold_counterfactual"]} for a in ARMS}, indent=1))
    print("exceedances inside fault window:", summary["m4_force_exceedances_vs_fault_window"]["n_inside_window"], "/", len(scan))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
