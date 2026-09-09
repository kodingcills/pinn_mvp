"""The constrained-contact behavior (Task B): acquire a declared contact
with the plate, then slide along a tangential arc while a force regulator
maintains the interaction. All task knowledge lives here; the machine sees
only servo commands, and the benchmark owns the completion truth.

Control law: while in contact, the axial setpoint is a position servo
around the captured contact reference (z_contact_ref) with an integral
trim on the commanded normal force; laterally, the setpoint advances along
the arc at the slide speed and the impedance spring drags the part against
friction. Loss of contact (a disturbance popping the part up) pauses the
lateral advance and presses at the force cap until contact is regained —
behavior-owned recovery. The runtime layer (max force, tilt, freshness,
deadline) is unaware of any of this."""
from __future__ import annotations

import math
from typing import Callable

from pegsim.rollout import (
    SLIDE_F_CONTACT_N,
    SLIDE_F_MIN_N,
    ValidatedConfig,
)

from haplous.intent import ExecutionIntent, RequirementKind
from haplous.machine import MachineObservation, ServoCommand

_SLIDE_SPEED_MPS = 0.005
_KI_N_PER_N_S = 2.0
_Z_FLOOR_M = -0.002


class SlideExecutionBehavior:
    def __init__(self, bundle: ValidatedConfig) -> None:
        self._k_z = bundle.gains.k_z
        self._k_z_approach = bundle.insertion.k_z_search
        self._v_descent = bundle.insertion.v_descent
        self._f_contact = SLIDE_F_CONTACT_N
        self._mu = bundle.task.friction
        self._k_xy = bundle.gains.k_xy
        self._period_s: float | None = None
        self._last_t: float | None = None
        self._z_contact_ref: float | None = None
        self._trim = 0.0
        self._engaged = False
        self._snapped = False
        self._f_eff = self._f_contact
        self._s = 0.0
        self._r_path = 0.0
        self._theta_start = 0.0
        self._direction = 1.0
        self._arc_len = 0.0
        self._f_floor = SLIDE_F_MIN_N
        self._f_acquire = 0.5 * self._f_floor
        self._f_release = 0.25 * self._f_floor
        self._z_raw = 0.0

    def begin(self, intent: ExecutionIntent, caps, observation) -> None:
        self._period_s = caps.state_period_s
        self._last_t = None
        a = intent.start_position_m
        b = intent.goal_position_m
        self._r_path = math.hypot(a[0], a[1])
        self._theta_start = math.atan2(a[1], a[0])
        dtheta = math.atan2(b[1], b[0]) - self._theta_start
        dtheta = (dtheta + math.pi) % (2.0 * math.pi) - math.pi
        self._direction = 1.0 if dtheta >= 0.0 else -1.0
        self._arc_len = abs(dtheta) * self._r_path
        self._f_floor = next(
            (
                r.limit
                for r in intent.requirements
                if r.kind is RequirementKind.MIN_PROCESS_FORCE_N
            ),
            SLIDE_F_MIN_N,
        )
        self._f_acquire = 0.5 * self._f_floor
        self._f_release = 0.25 * self._f_floor
        tip = observation.pose_m
        prog = self._measured_progress(tip)
        fz = abs(observation.force_z_n)
        if fz >= self._f_acquire:
            # mid-path re-reference: the part is in contact; resume the
            # slide from the measured angular position.
            self._engaged = True
            self._z_contact_ref = tip[2]
            self._f_eff = min(fz, 1.5 * self._f_contact)
        else:
            self._engaged = False
            self._z_raw = tip[2]
            self._f_eff = self._f_contact

    def _measured_progress(self, tip) -> float:
        """Measured along-arc progress from the part's own angle. No
        modular wrap: the declared arcs never cross the angle seam, and a
        wrap would turn sub-microradian measurement noise just below the
        start into an apparent full-circle jump past the end (the false
        snap found in the S04 dev smoke)."""
        raw = (math.atan2(tip[1], tip[0]) - self._theta_start) * self._direction
        return max(raw * self._r_path, 0.0)

    def step(self, observation: MachineObservation) -> ServoCommand:
        assert self._period_s is not None, "begin() before step()"
        tip = observation.pose_m
        fz = abs(observation.force_z_n)
        prog = self._measured_progress(tip)
        now = observation.stamp.source_s
        dt = self._period_s if self._last_t is None else now - self._last_t
        dt = min(max(dt, 0.0), 3.0 * self._period_s)
        self._last_t = now

        if not self._engaged:
            if fz >= self._f_acquire:
                self._engaged = True
                self._z_contact_ref = tip[2]
                self._f_eff = min(fz, 1.5 * self._f_contact)
            else:
                self._z_raw = max(self._z_raw - self._v_descent * dt, _Z_FLOOR_M)
        else:
            if fz < self._f_release:
                # contact lost (disturbance pop): hold the path point and
                # press at the cap until the part settles back onto the
                # surface.
                self._engaged = False
                self._f_eff = 1.5 * self._f_contact
            else:
                self._trim = min(
                    max(
                        self._trim
                        + _KI_N_PER_N_S * (self._f_contact - fz) * dt,
                        -0.5 * self._f_contact,
                    ),
                    0.5 * self._f_contact,
                )
                self._f_eff = min(
                    max(self._f_contact + self._trim, 0.5 * self._f_contact),
                    1.5 * self._f_contact,
                )
                if not self._snapped:
                    # Advance while the measured part is short of the path
                    # end; when the measured tip crosses it, snap the
                    # setpoint onto the measured position and hold (the
                    # soft lateral spring trails the setpoint by the
                    # friction lag, so the measured angle is the only
                    # honest progress signal).
                    if prog >= self._arc_len:
                        self._s = prog
                        self._snapped = True
                    else:
                        self._s = self._s + _SLIDE_SPEED_MPS * dt

        theta = self._theta_start + self._direction * (self._s / self._r_path)
        xy = (
            self._r_path * math.cos(theta),
            self._r_path * math.sin(theta),
        )
        if self._engaged:
            z_des = self._z_contact_ref - self._f_eff / self._k_z
            k_z = self._k_z
        elif self._z_contact_ref is not None:
            z_des = self._z_contact_ref - self._f_eff / self._k_z
            k_z = self._k_z
        else:
            z_des = self._z_raw
            k_z = self._k_z_approach
        return ServoCommand(
            target_pose_m=(xy[0], xy[1], z_des), k_z_n_per_m=k_z
        )


def sliding_behavior_factory(
    bundle: ValidatedConfig,
) -> Callable[[], SlideExecutionBehavior]:
    return lambda: SlideExecutionBehavior(bundle)
