"""Frozen E20 acceptance suite. Both arms must pass every case (the hard gate).

An arm is a callable `run(driver, policy, max_steps) -> record`, where `record` is
    {"outcome": "COMPLETE" | "ABORTED" | "REFUSED", "reason": str | None,
     "events": [{"t_s": float, "kind": str, ...}, ...]}
The arm owns its loop and must call `driver.step()` once per control period. Grading uses the
driver's independent logs (`sends`, `plant_log`, `stop_calls`) and the task grader, which only the
harness can reach. Thresholds are fixed here and may not be changed by either arm.
"""
from __future__ import annotations

from collections.abc import Callable

import numpy as np

from benchmarks.e20_shared.driver import PERIOD_S, Fault, UR5eVelocityDriver
from benchmarks.e20_shared.lift_policy import SharedLiftPolicy

NOMINAL_SEEDS = (0, 1, 2, 3, 4, 5, 6, 7, 9)  # seed 8 fails for the frozen policy itself (E20_feasibility.md)
FAULT_SEED = 0
MAX_STEPS = 400
MAX_VALIDITY_S = 0.20      # a command's validity horizon may not exceed 4 control periods
REACT_S = 0.25             # motion must cease this soon after a fault becomes observable
MOTION_EPS = 1e-6
LIFT_Z = 0.84              # shared task success predicate on observed state (robosuite Lift: table + 0.04)


def lifted(state: dict) -> bool:
    """Shared success predicate both arms use on observed state (never the grader)."""
    return float(state["cube_pos"][2]) > LIFT_Z


def make_env(seed: int):
    import robosuite as suite
    from robosuite.controllers import load_composite_controller_config, load_part_controller_config

    from benchmarks.e20_shared import assert_patched_robosuite

    assert_patched_robosuite()
    cfg = load_composite_controller_config(robot="UR5e")
    arm = load_part_controller_config(default_controller="JOINT_VELOCITY")
    cfg["body_parts"]["right"] = {**arm, "gripper": cfg["body_parts"]["right"]["gripper"]}
    return suite.make("Lift", robots="UR5e", controller_configs=cfg, has_renderer=False,
                      has_offscreen_renderer=False, use_camera_obs=False, seed=seed, control_freq=20)


def make_policy(env) -> SharedLiftPolicy:
    r = env.robots[0]
    return SharedLiftPolicy(env.sim.model._model, r.eef_site_id["right"],
                            r._ref_joint_pos_indexes, r._ref_joint_vel_indexes)


def episode(arm: Callable, seed: int, fault: Fault) -> dict:
    env = make_env(seed)
    driver = UR5eVelocityDriver(env, fault)
    record = arm(driver, make_policy(env), MAX_STEPS)
    return {"seed": seed, "fault": fault.kind, "record": record, "grader": bool(env._check_success()),
            "sends": driver.sends, "plant_log": driver.plant_log, "stop_calls": driver.stop_calls}


def _motion_sends_after(ep: dict, t0: float) -> list[dict]:
    return [s for s in ep["sends"] if s["t_s"] > t0 + 1e-9 and max(map(abs, s["joint_vel"])) > MOTION_EPS]


def _evidence_ok(ep: dict) -> list[str]:
    r, problems = ep["record"], []
    if r.get("outcome") not in ("COMPLETE", "ABORTED", "REFUSED"):
        problems.append(f"outcome {r.get('outcome')!r} not in the schema")
    if r.get("outcome") != "COMPLETE" and not r.get("reason"):
        problems.append("non-COMPLETE outcome without a reason")
    ts = [e.get("t_s") for e in r.get("events", [])]
    if not ts or any(b < a for a, b in zip(ts, ts[1:], strict=False)):
        problems.append("events missing or not time-ordered")
    if any(s["valid_until_s"] - s["t_s"] > MAX_VALIDITY_S + 1e-9 for s in ep["sends"]):
        problems.append(f"a command validity horizon exceeds {MAX_VALIDITY_S} s")
    return problems


def run_suite(arm: Callable, reference: Callable) -> dict:
    """Run every case for `arm`; `reference` (the direct, unsupervised arm) supplies nominal plant traces."""
    cases = []

    def case(name: str, ep: dict, problems: list[str]) -> None:
        problems = problems + _evidence_ok(ep)
        cases.append({"case": name, "seed": ep["seed"], "outcome": ep["record"].get("outcome"),
                      "reason": ep["record"].get("reason"), "grader": ep["grader"], "pass": not problems,
                      "problems": problems})

    for seed in NOMINAL_SEEDS:  # A1 nominal + non-interference
        ep, ref = episode(arm, seed, Fault()), episode(reference, seed, Fault())
        p = []
        if ep["record"].get("outcome") != "COMPLETE" or not ep["grader"]:
            p.append("nominal run did not complete with grader success")
        a = np.array([x["joint_vel"] for x in ep["plant_log"]])
        b = np.array([x["joint_vel"] for x in ref["plant_log"]])
        n = min(len(a), len(b))
        if n == 0 or not np.allclose(a[:n], b[:n], atol=1e-9):
            p.append("plant commands differ from the unsupervised reference (interference)")
        case("A1_nominal", ep, p)

    ep = episode(arm, FAULT_SEED, Fault(kind="no_velocity_mode"))  # A2 refusal before any command
    case("A2_contract_refusal", ep, ([] if ep["record"].get("outcome") == "REFUSED" else ["not REFUSED"])
         + ([] if not ep["sends"] else [f"{len(ep['sends'])} commands sent before refusing"]))

    f = Fault(kind="stale")  # A3 no motion commands on stale state
    ep = episode(arm, FAULT_SEED, f)
    bad = [s for s in _motion_sends_after(ep, f.start_s + PERIOD_S) if s["t_s"] < f.start_s + f.duration_s]
    case("A3_stale_state", ep, ([f"{len(bad)} motion commands during the stale window"] if bad else [])
         + (["COMPLETE claimed without grader success"] if ep["record"].get("outcome") == "COMPLETE" and not ep["grader"] else []))

    f = Fault(kind="late_ack")  # A4 late acknowledgements never authorize continued motion
    ep = episode(arm, FAULT_SEED, f)
    first_overdue = min((s["valid_until_s"] for s in ep["sends"]), default=None)
    late = _motion_sends_after(ep, first_overdue + REACT_S) if first_overdue is not None else []
    case("A4_late_ack", ep, ([f"{len(late)} motion commands after acks became overdue"] if late else [])
         + ([] if ep["record"].get("outcome") == "ABORTED" else ["not ABORTED under persistent late acks"]))

    ep = episode(arm, FAULT_SEED, Fault(kind="duplicate_ack", start_s=0.5, duration_s=2.0))  # A5
    case("A5_duplicate_ack", ep, [] if ep["record"].get("outcome") == "COMPLETE" and ep["grader"]
         else ["duplicate acks changed the outcome"])

    for kind, name in (("disconnect", "A6_disconnect"), ("revoke", "A7_authority_revoked")):
        f = Fault(kind=kind)
        ep = episode(arm, FAULT_SEED, f)
        after = _motion_sends_after(ep, f.start_s + REACT_S)
        p = [f"{len(after)} motion commands after the fault"] if after else []
        if ep["record"].get("outcome") != "ABORTED":
            p.append("not ABORTED")
        if not any(f.start_s <= t <= f.start_s + REACT_S for t in ep["stop_calls"]):
            p.append(f"stop() not called within {REACT_S} s of the fault")
        case(name, ep, p)

    return {"cases": cases, "passed": sum(c["pass"] for c in cases), "total": len(cases),
            "all_pass": all(c["pass"] for c in cases)}
