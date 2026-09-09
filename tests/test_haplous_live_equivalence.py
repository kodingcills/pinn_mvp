"""Live-execution equivalence: the adapter's stepping session driven by the
insertion behavior must reproduce the validated research substrate
(run_episode) exactly — same physics sequence, same task gates, same
outcome. This is the regression guard for the S01 task-logic port: the
behavior port and the servo session may not diverge from run_episode."""
import math

import numpy as np

from pegsim.rollout import (
    EpisodeConfig,
    VALIDATED_LOOSE_V1,
    run_episode,
)

from haplous.adapters.mujoco_adapter import MuJoCoMachine, MuJoCoMachineConfig
from haplous.behaviors.insertion import (
    InsertionExecutionBehavior,
    insertion_intent,
)

HORIZON_S = 6.0


def _episode_cfg(offset_r, bearing):
    b = VALIDATED_LOOSE_V1
    return EpisodeConfig(
        task=b.task,
        solver=b.solver,
        gains=b.gains,
        insertion=b.insertion,
        z_start=b.z_start,
        initial_offset_r=offset_r,
        initial_offset_theta=bearing,
        max_seconds=HORIZON_S,
        spiral_spec=b.spiral,
        control_decimation=1,
        control_delay_steps=0,
    )


def _run_live(offset_r, bearing):
    machine = MuJoCoMachine(
        VALIDATED_LOOSE_V1, config=MuJoCoMachineConfig(observation_decimation=1)
    )
    intent = insertion_intent(
        VALIDATED_LOOSE_V1.task,
        initial_offset_m=offset_r,
        initial_bearing_rad=bearing,
        require_success_condition=False,
    )
    session = machine.open_session(
        start_position_m=intent.start_position_m, horizon_s=HORIZON_S
    )
    behavior = InsertionExecutionBehavior(VALIDATED_LOOSE_V1)
    obs = session.observe()
    behavior.begin(intent, machine.capabilities(), obs)
    while not session.ended:
        session.apply(behavior.step(obs))
        session.step()
        if session.ended:
            break
        obs = session.observe()
    return session


def test_live_session_matches_run_episode_at_offset():
    offset, bearing = 0.002, 1.0
    tr = run_episode(
        _episode_cfg(offset, bearing), pen_allow=VALIDATED_LOOSE_V1.pen_allow
    )
    session = _run_live(offset, bearing)
    live = session.observations

    # Session tick k and run_episode's tip_pos[k] both record the pose at
    # time k*dt: mj_step leaves site_xpos at the pre-integration state, so
    # both paths log the pose the tick's control acted on.
    n = min(len(live), len(tr.tip_pos))
    live_tip = np.array([o.pose_m for o in live[:n]])
    dev_pos = np.abs(live_tip - tr.tip_pos[:n]).max()
    live_fz = np.array([o.force_z_n for o in live[:n]])
    dev_fz = np.abs(live_fz - tr.contact_force[:n, 2]).max()

    # Both paths read contact force at the same states, but through
    # different solver passes (post-mj_step vs post-mj_forward); the
    # warmstart path shifts the iterative contact solution at impact
    # transients. Measured worst deviation on canonical scenarios is
    # ~1.6 N on a ~15 N reading with bit-identical trajectories, so 2 N
    # bounds observer-path noise without admitting any dynamic divergence.
    assert dev_fz < 2.0, f"axial force diverged: max dev {dev_fz:.3e} N"
    assert dev_pos < 1e-9, f"tip trajectory diverged: max dev {dev_pos:.3e} m"
    # The live session reproduces the substrate's seating outcome.
    assert tr.success, "reference episode did not seat within horizon"
    seated_z = min(o.pose_m[2] for o in live)
    assert seated_z <= -(VALIDATED_LOOSE_V1.task.plate_thickness - 0.00014)


def test_live_session_matches_run_episode_nominal():
    tr = run_episode(
        _episode_cfg(0.0, 0.0), pen_allow=VALIDATED_LOOSE_V1.pen_allow
    )
    session = _run_live(0.0, 0.0)
    live = session.observations
    n = min(len(live), len(tr.tip_pos))
    live_tip = np.array([o.pose_m for o in live[:n]])
    dev = np.abs(live_tip - tr.tip_pos[:n]).max()
    assert dev < 1e-9, f"tip trajectory diverged: max dev {dev:.3e} m"
