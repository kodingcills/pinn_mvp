"""E20 feasibility: the frozen shared Lift policy completes UR5e Lift under JOINT_VELOCITY (nominal).

    MUJOCO_GL=osmesa <robosuite venv>/bin/python -I scripts/feasibility/e20_shared_policy_nominal.py OUT.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from benchmarks.e20_shared.lift_policy import SharedLiftPolicy  # noqa: E402
from scripts.feasibility.e20_ur5e_joint_velocity import finite  # noqa: E402

SEEDS = tuple(range(10))
MAX_STEPS = 400  # 20 s at 20 Hz


def episode(seed: int) -> dict:
    env = make_env_seeded(seed)
    obs = env.reset()
    r = env.robots[0]
    policy = SharedLiftPolicy(env.sim.model._model, r.eef_site_id["right"],
                              r._ref_joint_pos_indexes, r._ref_joint_vel_indexes)
    for step in range(MAX_STEPS):
        obs, *_ = env.step(policy.act(obs))
        if env._check_success():
            return {"seed": seed, "success": True, "steps": step + 1, "sim_time_s": round(float(env.sim.data.time), 3),
                    "finite": finite(env), "final_phase": policy.phase}
    return {"seed": seed, "success": False, "steps": MAX_STEPS, "final_phase": policy.phase, "finite": finite(env),
            "cube_z": round(float(obs["cube_pos"][2]), 4)}


def make_env_seeded(seed: int):
    import robosuite as suite
    from robosuite.controllers import load_composite_controller_config, load_part_controller_config
    cfg = load_composite_controller_config(robot="UR5e")
    arm = load_part_controller_config(default_controller="JOINT_VELOCITY")
    cfg["body_parts"]["right"] = {**arm, "gripper": cfg["body_parts"]["right"]["gripper"]}
    return suite.make("Lift", robots="UR5e", controller_configs=cfg, has_renderer=False,
                      has_offscreen_renderer=False, use_camera_obs=False, seed=seed, control_freq=20)


if __name__ == "__main__":
    rows = [episode(s) for s in SEEDS]
    report = {"policy": "benchmarks/e20_shared/lift_policy.py", "seeds": list(SEEDS), "episodes": rows,
              "successes": sum(r["success"] for r in rows)}
    Path(sys.argv[1]).write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps(report, indent=1))
    sys.exit(0 if report["successes"] == len(SEEDS) else 1)
