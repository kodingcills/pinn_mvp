"""R05 locked comparison engine (campaign: pegsim R-series closure).

Implements exactly the design locked in
``.opencode/haplous/state/locked-tests/R05-v1.yaml``. Every physics motion is
an ``EpisodeConfig`` executed by ``pegsim.rollout.run_episode`` (campaign Rule
1); this module composes episodes and post-processes traces -- it never steps
MuJoCo itself.

Arm semantics (locked):

- baseline: one production insertion from the commanded center ``c0`` with
  the full VALIDATED_LOOSE_V1 spiral at the selected tangential speed.
- haplous: registered antipodal probe pairs (start ``c0 +- 1 mm * u``), each
  side judged by the operational-completion criterion (FK depth persistence,
  observed force/tilt bounds -- never simulator truth), sensor corruption,
  nominal-clearance reduction, and a gated ``StateFilter`` batch update whose
  prior mean is ``c0``. After the candidate policy's required UPDATED count,
  one production insertion is guided from the filter's ``h`` estimate;
  otherwise (probe failure, SALVAGE exhaustion, or ABORT) one full baseline
  production runs from ``c0``.

Rule Zero: the treatment consumes only commanded geometry and measured FK /
wrench / tilt telemetry. True hole position, physical clearance, and
``TrialResult.success`` appear only in evaluation fields produced by
:func:`grade_production` and the NEES diagnostics, and the truth-poison test
(``tests/test_r05.py``) asserts they cannot influence any treatment decision.

Known shared-infrastructure caveat (locked as risk K6): ``run_episode``'s
capture branch triggers on the peg mouth's distance to the world origin -- the
true hole. Both arms use the identical controller, so the paired comparison
is internally fair; the repair is deferred to the next campaign.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Final, Optional

import mujoco
import numpy as np
from numpy.typing import NDArray

from pegsim.error_injection import CorruptionModel, TcpCalibrationBias
from pegsim.filter import FilterOutcome, FilterUpdateRecord, StateFilter
from pegsim.probing import (
    apply_sensor_corruption,
    load_n5_n64_pair_bias_m,
    load_n5_pair_measurement_model,
    reduce_measurement,
)
from pegsim.rollout import (
    DEPTH_COMMIT,
    EpisodeConfig,
    ImpedanceGains,
    InsertionProfile,
    SolverSpec,
    TaskSpec,
    TrialResult,
    run_episode,
)
from pegsim.search import DELTA_MAX_SPEC_M, SpiralSpec

ROOT = Path(__file__).resolve().parents[1]
N5_RAW_PATH: Final[Path] = ROOT / "results" / "n5_antipodal_raw.jsonl"
N5_SUMMARY_PATH: Final[Path] = ROOT / "results" / "n5_antipodal_summary.json"

# ---------------------------------------------------------------------------
# Locked constants (R05-v1.yaml; do not tune)
# ---------------------------------------------------------------------------

N_SEGMENTS: Final[int] = 64
NOMINAL_CLEARANCE_M: Final[float] = 0.0005
PROBE_RADIUS_M: Final[float] = 0.001
PEN_ALLOW_M: Final[float] = 12e-6
MAX_SECONDS: Final[float] = 16.3
Z_START_M: Final[float] = 0.005
PLATE_TILT_DEG: Final[float] = 0.0
TARGET_DEPTH_M: Final[float] = 0.00986  # plate 10mm - 2 x 70um measured residual
DEPTH_PERSIST_SAMPLES: Final[int] = 200  # 0.1 s at dt = 0.5 ms
MAX_ABS_FZ_N: Final[float] = 25.0
MAX_TILT_DEG: Final[float] = 10.0
BEARING_OFFSETS_RAD: Final[tuple[float, ...]] = (
    0.0,
    math.pi / 2.0,
    2.399963229728653,
)
BASELINE_V_T_CANDIDATES_M_PER_S: Final[tuple[float, ...]] = (
    0.005,
    0.010,
    0.025,
    0.050,
    0.100,
)
DEV_MASTER_SEED: Final[int] = 50501
HELDOUT_MASTER_SEED: Final[int] = 50502
DEV_N_PER_STRATUM: Final[int] = 4
HELDOUT_N_PER_STRATUM: Final[int] = 16

# G8 locked evidence fingerprints (full float64 precision; exact match).
N5_RAW_SHA256: Final[str] = (
    "f3c5fa7304a545531863f9df7498b3d03f271d01e5365eff8b5c1edb5a6e506c"
)
N5_SUMMARY_SHA256: Final[str] = (
    "87d254728736c137f29149d8411a00864f5ce0a0857d6aac962573a8734a66bf"
)
LOCKED_RADIAL_VARIANCE_M2: Final[float] = 5.639250829400083e-13
LOCKED_TANGENTIAL_VARIANCE_M2: Final[float] = 2.8422632826887354e-12
LOCKED_RADIAL_SIDE_COV_M2: Final[float] = -4.844441351432012e-13
LOCKED_TANGENTIAL_SIDE_COV_M2: Final[float] = -2.788102814967945e-12
LOCKED_PENETRATION_BIAS_M: Final[float] = 1.1423122550128134e-06


@lru_cache(maxsize=1)
def _evidence_model():
    return load_n5_pair_measurement_model(), load_n5_n64_pair_bias_m()


def assert_evidence_fingerprints() -> None:
    """G8: fail closed before any physics if the N5 evidence changed."""
    for path, expected in (
        (N5_RAW_PATH, N5_RAW_SHA256),
        (N5_SUMMARY_PATH, N5_SUMMARY_SHA256),
    ):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != expected:
            raise RuntimeError(
                f"locked N5 evidence fingerprint mismatch for {path.name}: "
                f"expected {expected}, found {digest}"
            )
    model, bias = _evidence_model()
    locked = {
        "radial_variance_m2": (model.radial_variance_m2, LOCKED_RADIAL_VARIANCE_M2),
        "tangential_variance_m2": (
            model.tangential_variance_m2,
            LOCKED_TANGENTIAL_VARIANCE_M2,
        ),
        "radial_side_covariance_m2": (
            model.radial_side_covariance_m2,
            LOCKED_RADIAL_SIDE_COV_M2,
        ),
        "tangential_side_covariance_m2": (
            model.tangential_side_covariance_m2,
            LOCKED_TANGENTIAL_SIDE_COV_M2,
        ),
    }
    for name, (found, expected) in locked.items():
        if found != expected:
            raise RuntimeError(
                f"locked N5 covariance scalar {name} drifted: expected "
                f"{expected!r}, found {found!r}"
            )
    if not math.isclose(
        bias, LOCKED_PENETRATION_BIAS_M, rel_tol=1e-12
    ):
        raise RuntimeError(
            f"locked N5 penetration bias drifted: expected "
            f"{LOCKED_PENETRATION_BIAS_M!r}, found {bias!r}"
        )


# ---------------------------------------------------------------------------
# Scenario manifest generation (locked RNG stream order)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StratumSpec:
    """One locked challenge stratum (fixture radius range, corruption)."""

    id: int
@dataclass(frozen=True)
class StratumSpec:
    id: int
    name: str
    fixture_radius_m: tuple[float, float]
    dc_levels_m: tuple[float, ...]
    tcp_bias: bool
    encoder_resolution_m: Optional[float]
    # R05-v3: a stratum block may pin the exact TCP hazard realization
    # (translation, yaw). When pinned, the balanced clause (C) permutation is
    # still consumed but its values are unused for this stratum.
    tcp_fixed: Optional[tuple[tuple[float, float], float]] = None


DEV_STRATA: Final[tuple[StratumSpec, ...]] = (
    StratumSpec(0, "validated_core_clean", (0.0015, 0.00225), (0.0,) * 4, False, None),
    StratumSpec(1, "probe_feasible_clean", (0.0005, 0.0010), (-1e-5, -5e-6, 5e-6, 1e-5), False, None),
    StratumSpec(
        2, "probe_feasible_f1c_hazard", (0.0005, 0.0010),
        (-1e-5, -5e-6, 5e-6, 1e-5), True, 2e-6,
        tcp_fixed=((3e-6, 0.0), 0.01),
    ),
    StratumSpec(3, "probe_boundary_clean", (0.0010, 0.0015), (-1e-5, -5e-6, 5e-6, 1e-5), False, None),
    StratumSpec(4, "search_edge_clean", (0.00225, 0.00425), (-1e-5, -5e-6, 5e-6, 1e-5), False, None),
)

HELDOUT_STRATA: Final[tuple[StratumSpec, ...]] = (
    StratumSpec(0, "validated_core_clean", (0.0015, 0.00225), (0.0,), False, None),
    StratumSpec(1, "probe_feasible_clean_part_variation", (0.0005, 0.0010), (-1e-5, -5e-6, 5e-6, 1e-5), False, None),
    StratumSpec(
        2, "probe_feasible_tcp_hazard", (0.0005, 0.0010),
        (-1e-5, -5e-6, 5e-6, 1e-5), True, 2e-6,
    ),
    StratumSpec(3, "probe_feasibility_boundary_clean", (0.0010, 0.0015), (-1e-5, -5e-6, 5e-6, 1e-5), False, None),
    StratumSpec(4, "beyond_validated_search_edge_clean", (0.00225, 0.00425), (-1e-5, -5e-6, 5e-6, 1e-5), False, None),
)


def _radial_quantile(stratum: StratumSpec, index: int, n: int) -> float:
    """Area-uniform radius r_j = sqrt(r_min^2 + u_j (r_max^2 - r_min^2)),
    u_j = (j + 0.5) / n, before the seeded permutation (stream 2)."""
    r_min, r_max = stratum.fixture_radius_m
    u = (index + 0.5) / n
    return math.sqrt(r_min**2 + u * (r_max**2 - r_min**2))


@dataclass(frozen=True)
class Scenario:
    """One exogenous challenge case, identical for both arms."""

    scenario_id: str
    stratum_id: int
    stratum_name: str
    replicate_id: int
    seed: int
    fixture_radius_m: float
    offset_bearing_rad: float
    c0_x_m: float
    c0_y_m: float
    clearance_offset_m: float
    tcp_translation_m: Optional[tuple[float, float]]
    tcp_yaw_rad: Optional[float]
    encoder_resolution_m: Optional[float]
    first_probe_bearing_rad: float
    arm_order_bit: int

    @property
    def c0_xy_m(self) -> NDArray[np.float64]:
        return np.array([self.c0_x_m, self.c0_y_m])

    @property
    def physical_clearance_m(self) -> float:
        return NOMINAL_CLEARANCE_M + self.clearance_offset_m


def generate_scenario(
    master_seed: int,
    stratum: StratumSpec,
    replicate_id: int,
    n_per_stratum: int,
) -> Scenario:
    """Locked generation (R05-v2): one stratum-level balanced assignment
    (radii each used once; dc levels balanced; tcp octants twice each) plus
    exactly three per-scenario draws: offset bearing, first probe bearing,
    arm-order bit. No other draws."""
    assign_seq = np.random.SeedSequence([master_seed, stratum.id, 0xFFFFFFFF])
    assign_rng = np.random.Generator(np.random.PCG64(assign_seq))
    radius_perm = assign_rng.permutation(n_per_stratum)
    dc_slots = stratum.dc_levels_m * (n_per_stratum // len(stratum.dc_levels_m))
    dc_perm = assign_rng.permutation(n_per_stratum)
    tcp_perm = assign_rng.permutation(16)

    seed_seq = np.random.SeedSequence([master_seed, stratum.id, replicate_id])
    rng = np.random.Generator(np.random.PCG64(seed_seq))
    theta_e = float(rng.random() * 2.0 * math.pi)
    theta_p0 = float(rng.random() * 2.0 * math.pi)
    arm_order_bit = int(rng.integers(0, 2))

    if stratum.tcp_bias:
        pair_id = int(tcp_perm[replicate_id])
        if stratum.tcp_fixed is not None:
            (tx, ty), yaw = stratum.tcp_fixed
            tcp_translation = (tx, ty)
            tcp_yaw = yaw
        else:
            octant = pair_id % 8
            yaw_sign = pair_id // 8
            phi = (2 * octant + 1) * math.pi / 8.0
            tcp_translation = (3e-6 * math.cos(phi), 3e-6 * math.sin(phi))
            tcp_yaw = 0.01 if yaw_sign == 0 else -0.01
    else:
        tcp_translation = None
        tcp_yaw = None

    radius = _radial_quantile(stratum, int(radius_perm[replicate_id]), n_per_stratum)
    dc = dc_slots[int(dc_perm[replicate_id])]
    return Scenario(
        scenario_id=f"s{stratum.id:02d}-r{replicate_id:02d}",
        stratum_id=stratum.id,
        stratum_name=stratum.name,
        replicate_id=replicate_id,
        seed=int(seed_seq.generate_state(1, dtype=np.uint64)[0]),
        fixture_radius_m=radius,
        offset_bearing_rad=theta_e,
        c0_x_m=radius * math.cos(theta_e),
        c0_y_m=radius * math.sin(theta_e),
        clearance_offset_m=dc,
        tcp_translation_m=tcp_translation,
        tcp_yaw_rad=tcp_yaw,
        encoder_resolution_m=stratum.encoder_resolution_m,
        first_probe_bearing_rad=theta_p0,
        arm_order_bit=arm_order_bit,
    )


def generate_manifest(
    phase: str, *, master_seed: int, strata: tuple[StratumSpec, ...], n_per_stratum: int
) -> list[Scenario]:
    scenarios: list[Scenario] = []
    for stratum in strata:
        for replicate in range(n_per_stratum):
            scenarios.append(
                generate_scenario(master_seed, stratum, replicate, n_per_stratum)
            )
    return scenarios


def scenario_to_dict(scenario: Scenario) -> dict:
    return {
        "scenario_id": scenario.scenario_id,
        "stratum_id": scenario.stratum_id,
        "stratum_name": scenario.stratum_name,
        "replicate_id": scenario.replicate_id,
        "seed": scenario.seed,
        "fixture_radius_m": scenario.fixture_radius_m,
        "offset_bearing_rad": scenario.offset_bearing_rad,
        "c0_x_m": scenario.c0_x_m,
        "c0_y_m": scenario.c0_y_m,
        "clearance_offset_m": scenario.clearance_offset_m,
        "tcp_translation_m": (
            list(scenario.tcp_translation_m)
            if scenario.tcp_translation_m is not None
            else None
        ),
        "tcp_yaw_rad": scenario.tcp_yaw_rad,
        "encoder_resolution_m": scenario.encoder_resolution_m,
        "first_probe_bearing_rad": scenario.first_probe_bearing_rad,
        "arm_order_bit": scenario.arm_order_bit,
    }


def canonical_manifest_json(scenarios: list[Scenario]) -> str:
    return json.dumps(
        [scenario_to_dict(s) for s in scenarios],
        sort_keys=True,
        indent=1,
    )


# ---------------------------------------------------------------------------
# Locked configuration and episode construction
# ---------------------------------------------------------------------------


def build_control_task() -> TaskSpec:
    """The NOMINAL control task. This is the ONLY task construction the
    policy-visible arm functions may receive: it carries no dc, so the
    Rule Zero boundary is structural, not conventional (G10)."""
    return TaskSpec(
        peg_radius=0.005,
        radial_clearance=NOMINAL_CLEARANCE_M,
        chamfer_width=0.001,
        plate_thickness=0.01,
        peg_length=0.05,
        peg_mass=0.05,
        n_segments=N_SEGMENTS,
        friction=0.4,
    )


def build_tasks(clearance_offset_m: float) -> tuple[TaskSpec, TaskSpec]:
    """G3: physical task (scene geometry, clearance 0.5 mm + dc) vs control
    task (all control logic, reduction, and the filter keep nominal 0.5 mm).
    Offline/model-construction use only; arm functions take build_control_task()."""
    physical_task = TaskSpec(
        peg_radius=0.005,
        radial_clearance=NOMINAL_CLEARANCE_M + clearance_offset_m,
        chamfer_width=0.001,
        plate_thickness=0.01,
        peg_length=0.05,
        peg_mass=0.05,
        n_segments=N_SEGMENTS,
        friction=0.4,
    )
    control_task = build_control_task()
    return physical_task, control_task


_SOLVER: Final[SolverSpec] = SolverSpec(timestep=0.0005)
_GAINS: Final[ImpedanceGains] = ImpedanceGains(
    k_xy=1000.0, k_z=2000.0, k_rx=5.0, k_ry=5.0, k_rz=0.05
)
_INSERTION: Final[InsertionProfile] = InsertionProfile(k_z_search=500.0, f_search=1.0)


def _base_episode(control_task: TaskSpec) -> dict:
    if control_task.radial_clearance != NOMINAL_CLEARANCE_M:
        raise RuntimeError(
            "G3 fail-closed: control episodes must use the NOMINAL clearance "
            f"({NOMINAL_CLEARANCE_M} m), got {control_task.radial_clearance}"
        )
    return dict(
        task=control_task,
        solver=_SOLVER,
        gains=_GAINS,
        insertion=_INSERTION,
        z_start=Z_START_M,
        max_seconds=MAX_SECONDS,
        depth_commit=DEPTH_COMMIT,
        control_decimation=1,
        control_delay_steps=0,
    )


def episode_from_world_start(
    control_task: TaskSpec,
    world_start_xy: NDArray[np.float64],
    *,
    spiral: Optional[SpiralSpec],
) -> EpisodeConfig:
    """Episode whose peg starts at the given world point (which is also the
    spiral center when a spiral is present), per EpisodeConfig's offset
    convention: initial_offset_r/theta place the peg and the commanded
    nominal; the true hole stays at the world origin."""
    start = np.asarray(world_start_xy, dtype=float)
    radius = float(np.linalg.norm(start))
    return EpisodeConfig(
        **_base_episode(control_task),
        initial_offset_r=radius,
        initial_offset_theta=math.atan2(start[1], start[0]),
        spiral_spec=spiral,
    )


def probe_episode_at(
    control_task: TaskSpec,
    c0: NDArray[np.float64],
    bearing_rad: float,
) -> EpisodeConfig:
    """G2: the locked N2/N5 probe primitive launched from the SCENARIO
    center: commanded world start points are exactly c0 +- 1 mm * u(theta)."""
    u = np.array([math.cos(bearing_rad), math.sin(bearing_rad)])
    start = np.asarray(c0, dtype=float) + PROBE_RADIUS_M * u
    # G2 assertion: the constructed episode's initial condition must be the
    # commanded world point, not the origin-centered F1c special case.
    expected_radius = float(np.linalg.norm(start))
    cfg = episode_from_world_start(control_task, start, spiral=None)
    rebuilt = np.array([
        cfg.initial_offset_r * math.cos(cfg.initial_offset_theta),
        cfg.initial_offset_r * math.sin(cfg.initial_offset_theta),
    ])
    if not np.allclose(rebuilt, start, rtol=0.0, atol=1e-15):
        raise RuntimeError(
            "probe construction lost the commanded world start: "
            f"expected {start!r}, built {rebuilt!r}"
        )
    if not math.isclose(expected_radius, float(np.linalg.norm(rebuilt)), rel_tol=0.0, abs_tol=1e-15):
        raise RuntimeError("probe radius mismatch")
    return cfg


def spiral_for(control_task: TaskSpec, v_t_m_per_s: float) -> SpiralSpec:
    return SpiralSpec(
        task=control_task, delta_max=DELTA_MAX_SPEC_M, v_t=v_t_m_per_s
    )


# ---------------------------------------------------------------------------
# Operational completion (locked: FK depth persistence + observed bounds)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProbeSideOutcome:
    complete: bool
    completion_index: Optional[int]
    measured_tip_xy_m: Optional[NDArray[np.float64]]
    completion_time_s: Optional[float]
    reason: Optional[str]
    prefix_peak_abs_fz_n: Optional[float] = None


def operational_complete(tr: TrialResult) -> ProbeSideOutcome:
    """Earliest trace index k whose trailing 200-sample window all satisfy
    z_flat - tip_z >= 9.86 mm, with |F_z| <= 25 N and tilt < 10 deg over the
    ENTIRE prefix 0..k. Chronological scan: returns at the FIRST valid k and
    never inspects samples after it. Uses only robot-observable telemetry."""
    depth = tr.z_flat - tr.tip_pos[:, 2]
    window = DEPTH_PERSIST_SAMPLES
    if depth.size < window:
        return ProbeSideOutcome(
            False, None, None, None, "trace shorter than persistence window"
        )
    depth_ok = depth >= TARGET_DEPTH_M
    abs_fz = np.abs(tr.contact_force[:, 2])
    prefix_fz_max = np.maximum.accumulate(abs_fz)
    prefix_tilt_max = np.maximum.accumulate(tr.tilt_deg)
    window_falses = int(np.count_nonzero(~depth_ok[: window - 1]))
    for k in range(window - 1, depth.size):
        window_falses += int(not depth_ok[k])
        if k >= window:
            window_falses -= int(not depth_ok[k - window])
        if window_falses == 0:
            if (
                prefix_fz_max[k] <= MAX_ABS_FZ_N
                and prefix_tilt_max[k] < MAX_TILT_DEG
            ):
                return ProbeSideOutcome(
                    True,
                    k,
                    np.array(tr.tip_pos[k, :2], dtype=float, copy=True),
                    float(tr.time[k]),
                    None,
                    float(prefix_fz_max[k]),
                )
            return ProbeSideOutcome(
                False, None, None, None,
                "prefix force/tilt bound violated",
                float(prefix_fz_max[k]),
            )
    if not np.any(depth_ok):
        return ProbeSideOutcome(
            False, None, None, None,
            "FK depth persistence never reached",
            float(prefix_fz_max[-1]),
        )
    return ProbeSideOutcome(
        False, None, None, None, "depth persistence never held for 0.1 s",
        float(prefix_fz_max[-1]),
    )


# ---------------------------------------------------------------------------
# Treatment: probe schedule, filter, guidance (locked candidate policies)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HaplousCandidate:
    id: str
    max_pair_attempts: int
    required_updated_pairs: int
    guided_execution: str  # "straight_drop" | "recentered_full_spiral"


HAPLOUS_CANDIDATES: Final[tuple[HaplousCandidate, ...]] = (
    HaplousCandidate("H1", 1, 1, "straight_drop"),
    HaplousCandidate("H2", 1, 1, "recentered_full_spiral"),
    HaplousCandidate("H3", 3, 1, "straight_drop"),
    HaplousCandidate("H4", 3, 1, "recentered_full_spiral"),
    HaplousCandidate("H5", 3, 2, "recentered_full_spiral"),
)


def make_filter(c0: NDArray[np.float64]) -> StateFilter:
    """The locked filter: prior mean c0 (never the world origin), 1 mm^2
    initial covariance, placeholder single-side R (overridden per pair by the
    measured N5 covariance), F1a-rev2 process priors and gate limits."""
    _, bias = _evidence_model()
    return StateFilter(
        initial_state_m=[float(c0[0]), float(c0[1]), 0.0],
        initial_covariance_m2=np.diag([1.0e-6, 1.0e-6, 1.0e-6]),
        measurement_covariance_m2=np.diag([1.0e-12, 1.0e-12]),
        q_h_m2_per_cycle=4.0e-14,
        q_c_m2_per_cycle=1.0e-18,
        penetration_m=bias,
        consecutive_gate_limit=3,
    )


def corruption_for(scenario: Scenario) -> Optional[CorruptionModel]:
    if scenario.tcp_translation_m is None:
        return None
    assert scenario.tcp_yaw_rad is not None
    return CorruptionModel(
        tcp_bias=TcpCalibrationBias(
            translation_m=list(scenario.tcp_translation_m),
            yaw_rad=scenario.tcp_yaw_rad,
        ),
        encoder_resolution_m=scenario.encoder_resolution_m,
    )


@dataclass(frozen=True)
class PolicyView:
    """G10: the ONLY scenario content the arm functions receive. It carries
    no hidden physical clearance dc and no truth-side fields, so the Rule
    Zero boundary is structural: treatment code cannot consume what it is
    never handed."""

    c0_xy_m: NDArray[np.float64]
    first_probe_bearing_rad: float
    corruption: Optional[CorruptionModel]


def policy_view(scenario: Scenario) -> PolicyView:
    """Driver-side projection of the exogenous scenario to the policy-visible
    subset (commanded center, registered first bearing, measurement-corruption
    channel). Everything else — dc, grading, truth — stays offline."""
    return PolicyView(
        c0_xy_m=scenario.c0_xy_m,
        first_probe_bearing_rad=scenario.first_probe_bearing_rad,
        corruption=corruption_for(scenario),
    )


@dataclass
class ProbeEvent:
    attempt_index: int
    bearing_rad: float
    kind: str  # "unseatable" | "UPDATED" | "SALVAGE" | "ABORT"
    nis: Optional[float] = None
    plus_time_s: Optional[float] = None
    minus_time_s: Optional[float] = None
    plus_reduced_xy_m: Optional[NDArray[np.float64]] = None
    minus_reduced_xy_m: Optional[NDArray[np.float64]] = None


def attempt_probe_pair(
    filter_: StateFilter,
    control_task: TaskSpec,
    c0: NDArray[np.float64],
    bearing_rad: float,
    corruption: Optional[CorruptionModel],
    model,
    data,
) -> tuple[
    Optional[FilterUpdateRecord],
    list[ProbeEvent],
    list[float],
    list[float],
    TrialResult,
    TrialResult,
]:
    """One registered antipodal pair attempt through real physics. A side
    that fails operational completion leaves the filter untouched (G5).
    Returns the filter record (None if the pair was incomplete), the events,
    per-side completion/timeout times, per-side peak |F_z| for pipeline force
    accounting, and both complete side traces (evidence; never consumed by
    any treatment decision)."""
    events: list[ProbeEvent] = []
    times: list[float] = []
    peaks: list[float] = []
    plus_cfg = probe_episode_at(control_task, c0, bearing_rad)
    minus_cfg = probe_episode_at(control_task, c0, bearing_rad + math.pi)
    plus_tr = run_episode(plus_cfg, pen_allow=PEN_ALLOW_M, model=model, data=data)
    minus_tr = run_episode(minus_cfg, pen_allow=PEN_ALLOW_M, model=model, data=data)
    plus_out = operational_complete(plus_tr)
    minus_out = operational_complete(minus_tr)
    times.extend([
        plus_out.completion_time_s if plus_out.complete else MAX_SECONDS,
        minus_out.completion_time_s if minus_out.complete else MAX_SECONDS,
    ])
    peaks.extend([
        plus_out.prefix_peak_abs_fz_n
        if plus_out.prefix_peak_abs_fz_n is not None
        else float(np.max(np.abs(plus_tr.contact_force[:, 2]))),
        minus_out.prefix_peak_abs_fz_n
        if minus_out.prefix_peak_abs_fz_n is not None
        else float(np.max(np.abs(minus_tr.contact_force[:, 2]))),
    ])
    if not (plus_out.complete and minus_out.complete):
        events.append(
            ProbeEvent(0, bearing_rad, "unseatable",
                        plus_time_s=plus_out.completion_time_s,
                        minus_time_s=minus_out.completion_time_s)
        )
        return None, events, times, peaks, plus_tr, minus_tr
    assert plus_out.measured_tip_xy_m is not None
    assert minus_out.measured_tip_xy_m is not None
    plus_est = reduce_measurement(
        apply_sensor_corruption(plus_out.measured_tip_xy_m, corruption),
        bearing_rad,
        NOMINAL_CLEARANCE_M,
    )
    minus_est = reduce_measurement(
        apply_sensor_corruption(minus_out.measured_tip_xy_m, corruption),
        bearing_rad + math.pi,
        NOMINAL_CLEARANCE_M,
    )
    model_pair, _ = _evidence_model()
    record = filter_.update_pair(
        plus_est,
        minus_est,
        bearing_rad,
        pair_measurement_covariance_m2=model_pair.covariance_at(bearing_rad),
    )
    events.append(
        ProbeEvent(0, bearing_rad, record.outcome.value,
                   nis=record.normalized_innovation_squared,
                   plus_time_s=plus_out.completion_time_s,
                   minus_time_s=minus_out.completion_time_s,
                   plus_reduced_xy_m=plus_est,
                   minus_reduced_xy_m=minus_est)
    )
    return record, events, times, peaks, plus_tr, minus_tr


@dataclass
class HaplousExecution:
    guided: bool
    production_start_xy: NDArray[np.float64]
    used_fallback: bool
    spiral_usage: str  # "guided_drop" | "guided_spiral" | "baseline_fallback"
    probe_events: list[ProbeEvent] = field(default_factory=list)
    probe_side_motions: int = 0
    probe_time_s: float = 0.0
    probe_peak_fz_n: list[float] = field(default_factory=list)
    filter_records: list[FilterUpdateRecord] = field(default_factory=list)
    final_filter_state_m: Optional[NDArray[np.float64]] = None
    final_filter_h_cov_m2: Optional[NDArray[np.float64]] = None
    updated_pair_count: int = 0


def nees_and_errors(
    h_state_m: NDArray[np.float64],
    h_cov_m2: NDArray[np.float64],
    true_hole_xy: NDArray[np.float64],
    has_accepted_records: bool,
) -> tuple[Optional[float], float, bool]:
    """Offline (G10) 2-DOF fixture NEES and the 3-sigma confidently-wrong
    flag, computed from the filter's final state/covariance OUTSIDE the arm
    functions. NEES is reported only once the filter accepted at least one
    pair; never consumed by any treatment decision."""
    h_hat = np.asarray(h_state_m, dtype=float)[:2]
    p_hh = np.asarray(h_cov_m2, dtype=float)
    err = h_hat - np.asarray(true_hole_xy, dtype=float)
    if not has_accepted_records:
        return None, float(np.linalg.norm(err)), False
    solved = np.linalg.solve(p_hh, err)
    nees = float(err @ solved)
    sigma = np.sqrt(np.diag(p_hh))
    confidently_wrong = bool(np.any(np.abs(err) > 3.0 * sigma))
    return nees, float(np.linalg.norm(err)), confidently_wrong


def production_plan(
    candidate: HaplousCandidate,
    updated_count: int,
    filter_: StateFilter,
    c0: NDArray[np.float64],
    control_task: TaskSpec,
    baseline_v_t_m_per_s: float,
) -> tuple[bool, EpisodeConfig, str, NDArray[np.float64]]:
    """G4: the single designated production decision. Guided execution
    centers at the filter's h estimate exactly; fallback re-runs the frozen
    baseline from c0. There is never a second production episode."""
    if updated_count >= candidate.required_updated_pairs:
        h_hat = filter_.state_m[:2].copy()
        if candidate.guided_execution == "straight_drop":
            cfg = episode_from_world_start(control_task, h_hat, spiral=None)
            return True, cfg, "guided_drop", h_hat
        cfg = episode_from_world_start(
            control_task, h_hat,
            spiral=spiral_for(control_task, baseline_v_t_m_per_s),
        )
        return True, cfg, "guided_spiral", h_hat
    cfg = episode_from_world_start(
        control_task, c0,
        spiral=spiral_for(control_task, baseline_v_t_m_per_s),
    )
    return False, cfg, "baseline_fallback", c0.copy()


def run_haplous_arm(
    policy: PolicyView,
    control_task: TaskSpec,
    candidate: HaplousCandidate,
    baseline_v_t_m_per_s: float,
    *,
    model,
    data,
) -> tuple[HaplousExecution, TrialResult, list[tuple[str, TrialResult]]]:
    """The locked treatment arm. Inputs are policy-visible only (G10): the
    nominal control task, the projected PolicyView, the candidate policy, and
    the frozen baseline spiral speed. Returns the execution record, the one
    designated production trial, and all probe side traces (evidence; never
    consumed by any treatment decision)."""
    c0 = policy.c0_xy_m
    corruption = policy.corruption
    filter_ = make_filter(c0)
    execution = HaplousExecution(
        guided=False,
        production_start_xy=c0.copy(),
        used_fallback=True,
        spiral_usage="baseline_fallback",
    )
    probe_traces: list[tuple[str, TrialResult]] = []
    updated_count = 0
    for attempt in range(candidate.max_pair_attempts):
        bearing = policy.first_probe_bearing_rad + BEARING_OFFSETS_RAD[attempt]
        record, events, times, peaks, plus_tr, minus_tr = attempt_probe_pair(
            filter_, control_task, c0, bearing, corruption, model, data
        )
        probe_traces.append(("plus", plus_tr))
        probe_traces.append(("minus", minus_tr))
        for ev in events:
            ev.attempt_index = attempt
        execution.probe_events.extend(events)
        execution.probe_side_motions += 2
        execution.probe_time_s += sum(times)
        execution.probe_peak_fz_n.extend(peaks)
        if record is None:
            continue  # unseatable pair: filter untouched, next bearing
        execution.filter_records.append(record)
        if record.outcome == FilterOutcome.UPDATED:
            updated_count += 1
            if updated_count >= candidate.required_updated_pairs:
                break
        elif record.outcome == FilterOutcome.ABORT:
            break  # never execute from the ABORT-reinitialized h
        # SALVAGE: rejected update not used for guidance; next bearing.

    execution.final_filter_state_m = filter_.state_m.copy()
    execution.final_filter_h_cov_m2 = filter_.covariance_m2.copy()
    execution.updated_pair_count = updated_count

    guided, prod_cfg, spiral_usage, start = production_plan(
        candidate, updated_count, filter_, c0, control_task, baseline_v_t_m_per_s
    )
    execution.guided = guided
    execution.used_fallback = not guided
    execution.spiral_usage = spiral_usage
    execution.production_start_xy = start
    production = run_episode(prod_cfg, pen_allow=PEN_ALLOW_M, model=model, data=data)
    return execution, production, probe_traces


def build_physical_model(scenario: Scenario):
    """G3 fail-closed: the single MuJoCo construction site. The compiled scene
    ALWAYS uses the physical task (clearance 0.5 mm + dc); control logic keeps
    the nominal task. Returns (model, data, xml_sha256) so rows can record the
    exact geometry identity."""
    import hashlib as _hashlib

    from pegsim.scene import build_mjcf

    physical, control = build_tasks(scenario.clearance_offset_m)
    assert physical.radial_clearance == NOMINAL_CLEARANCE_M + scenario.clearance_offset_m
    assert control.radial_clearance == NOMINAL_CLEARANCE_M
    xml = build_mjcf(physical, _SOLVER, PLATE_TILT_DEG)
    model = mujoco.MjModel.from_xml_string(xml)
    return model, mujoco.MjData(model), _hashlib.sha256(xml.encode("utf-8")).hexdigest()


def run_baseline_arm(
    policy: PolicyView,
    control_task: TaskSpec,
    v_t_m_per_s: float,
    *,
    model,
    data,
) -> TrialResult:
    """The locked baseline arm: one full production insertion from c0."""
    cfg = episode_from_world_start(
        control_task,
        policy.c0_xy_m,
        spiral=spiral_for(control_task, v_t_m_per_s),
    )
    return run_episode(cfg, pen_allow=PEN_ALLOW_M, model=model, data=data)


# ---------------------------------------------------------------------------
# Offline grading (physical task; never consumed by treatment)
# ---------------------------------------------------------------------------


@dataclass
class Grade:
    success: bool
    seat_time_s: float
    peak_abs_fz_n: float
    force_integral_n_s: float
    max_tilt_deg: float
    failure_class: str
    completion_index: Optional[int] = None


def grade_production(tr: TrialResult, physical_clearance_m: float) -> Grade:
    """Primary outcome recomputed from the full trace with the PHYSICAL
    task: ABSOLUTE tip_z <= -9.86 mm in world coordinates (the z_flat-relative
    criterion is reserved for probe-side completion), tip/mouth lateral
    <= physical clearance + 12 um, geometry persisted 0.1 s by 16.3 s,
    |F_z| <= 25 N and tilt < 10 deg over the whole episode."""
    tol = physical_clearance_m + PEN_ALLOW_M
    tip_z = tr.tip_pos[:, 2]
    tip_lat = np.linalg.norm(tr.tip_pos[:, :2], axis=1)
    mouth_lat = tr.mouth_lateral  # peg axis crossing z=0 vs world origin (true hole)
    window = DEPTH_PERSIST_SAMPLES
    geometry_ok = (
        (tip_z <= -TARGET_DEPTH_M)
        & (tip_lat <= tol)
        & (mouth_lat <= tol)
    )
    false_cum = np.cumsum(~geometry_ok)
    false_cum = np.concatenate(([0], false_cum))
    seat_index: Optional[int] = None
    for k in range(window - 1, geometry_ok.size):
        if false_cum[k + 1] - false_cum[k + 1 - window] == 0:
            seat_index = k
            break
    peak_fz = float(np.max(np.abs(tr.contact_force[:, 2])))
    max_tilt = float(np.max(tr.tilt_deg))
    integral_end = (seat_index + 1) if seat_index is not None else tr.time.size
    force_integral = float(
        np.trapezoid(
            np.abs(tr.contact_force[:integral_end, 2]), tr.time[:integral_end]
        )
    )
    envelope_ok = peak_fz <= MAX_ABS_FZ_N and max_tilt < MAX_TILT_DEG
    success = seat_index is not None and envelope_ok
    if success:
        failure_class = "none"
    elif peak_fz > MAX_ABS_FZ_N:
        failure_class = "contact_envelope"
    elif max_tilt >= MAX_TILT_DEG:
        failure_class = "tilt_guard"
    elif tr.spiral_exhausted:
        failure_class = "spiral_exhausted"
    else:
        failure_class = "timeout"
    seat_time = float(tr.time[seat_index]) if seat_index is not None else MAX_SECONDS
    return Grade(
        success=success,
        seat_time_s=seat_time,
        peak_abs_fz_n=peak_fz,
        force_integral_n_s=force_integral,
        max_tilt_deg=max_tilt,
        failure_class=failure_class,
        completion_index=seat_index,
    )


# ---------------------------------------------------------------------------
# Locked analysis: one-sided exact McNemar
# ---------------------------------------------------------------------------


def exact_mcnemar_one_sided(n10: int, n01: int) -> float:
    """p = P[X >= n10] for X ~ Binomial(n10 + n01, 0.5); p = 1.0 if m = 0."""
    m = n10 + n01
    if m == 0:
        return 1.0
    p = 0.0
    for k in range(n10, m + 1):
        p += math.comb(m, k) * 0.5**m
    return min(1.0, p)


def paired_table(baseline_success: list[bool], haplous_success: list[bool]) -> dict:
    assert len(baseline_success) == len(haplous_success)
    n10 = sum(1 for h, b in zip(haplous_success, baseline_success) if h and not b)
    n01 = sum(1 for h, b in zip(haplous_success, baseline_success) if b and not h)
    n11 = sum(1 for h, b in zip(haplous_success, baseline_success) if h and b)
    n00 = sum(1 for h, b in zip(haplous_success, baseline_success) if not h and not b)
    n = len(baseline_success)
    delta = (n10 - n01) / n
    return {
        "n": n,
        "n10_haplous_only": n10,
        "n01_baseline_only": n01,
        "n11_both": n11,
        "n00_neither": n00,
        "delta_hat": delta,
        "p_haplous_superior": exact_mcnemar_one_sided(n10, n01),
        "p_baseline_superior": exact_mcnemar_one_sided(n01, n10),
    }
