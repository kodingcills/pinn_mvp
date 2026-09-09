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
    name: str
    fixture_radius_m: tuple[float, float]
    dc_levels_m: tuple[float, ...]
    tcp_bias: bool
    encoder_resolution_m: Optional[float]


DEV_STRATA: Final[tuple[StratumSpec, ...]] = (
    StratumSpec(0, "validated_core_clean", (0.0015, 0.00225), (0.0,) * 4, False, None),
    StratumSpec(1, "probe_feasible_clean", (0.0005, 0.0010), (-1e-5, -5e-6, 5e-6, 1e-5), False, None),
    StratumSpec(
        2, "probe_feasible_f1c_hazard", (0.0005, 0.0010),
        (-1e-5, -5e-6, 5e-6, 1e-5), True, 2e-6,
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
    """Locked RNG stream order: (1) offset bearing, (2) radius permutation,
    (3) dc permutation, (4) first probe bearing, (5) tcp octant + yaw sign,
    (6) arm-order bit. No other draws."""
    seed_seq = np.random.SeedSequence([master_seed, stratum.id, replicate_id])
    rng = np.random.Generator(np.random.PCG64(seed_seq))

    theta_e = float(rng.random() * 2.0 * math.pi)
    radius_perm = rng.permutation(n_per_stratum)
    dc_perm = rng.permutation(len(stratum.dc_levels_m))
    theta_p0 = float(rng.random() * 2.0 * math.pi)
    if stratum.tcp_bias:
        octant = int(rng.integers(0, 8))
        yaw_sign = int(rng.integers(0, 2))
        phi = (2 * octant + 1) * math.pi / 8.0
        tcp_translation: Optional[tuple[float, float]] = (
            3e-6 * math.cos(phi),
            3e-6 * math.sin(phi),
        )
        tcp_yaw: Optional[float] = 0.01 if yaw_sign == 0 else -0.01
    else:
        tcp_translation = None
        tcp_yaw = None
    arm_order_bit = int(rng.integers(0, 2))

    radius = _radial_quantile(
        stratum, int(radius_perm[replicate_id % n_per_stratum]), n_per_stratum
    )
    dc = stratum.dc_levels_m[int(dc_perm[replicate_id % len(stratum.dc_levels_m)])]
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


def build_tasks(clearance_offset_m: float) -> tuple[TaskSpec, TaskSpec]:
    """G3: physical task (scene geometry, clearance 0.5 mm + dc) vs control
    task (all control logic, reduction, and the filter keep nominal 0.5 mm)."""
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
    control_task = TaskSpec(
        peg_radius=0.005,
        radial_clearance=NOMINAL_CLEARANCE_M,
        chamfer_width=0.001,
        plate_thickness=0.01,
        peg_length=0.05,
        peg_mass=0.05,
        n_segments=N_SEGMENTS,
        friction=0.4,
    )
    return physical_task, control_task


_SOLVER: Final[SolverSpec] = SolverSpec(timestep=0.0005)
_GAINS: Final[ImpedanceGains] = ImpedanceGains(
    k_xy=1000.0, k_z=2000.0, k_rx=5.0, k_ry=5.0, k_rz=0.05
)
_INSERTION: Final[InsertionProfile] = InsertionProfile(k_z_search=500.0, f_search=1.0)


def _base_episode(control_task: TaskSpec) -> dict:
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


def operational_complete(tr: TrialResult) -> ProbeSideOutcome:
    """Earliest trace index k whose trailing 200-sample window all satisfy
    z_flat - tip_z >= 9.86 mm, with |F_z| <= 25 N and tilt < 10 deg over the
    ENTIRE prefix 0..k. Uses only robot-observable telemetry; samples after k
    are never inspected."""
    depth = tr.z_flat - tr.tip_pos[:, 2]
    window = DEPTH_PERSIST_SAMPLES
    if depth.size < window:
        return ProbeSideOutcome(False, None, None, None, "trace shorter than persistence window")
    depth_ok = depth >= TARGET_DEPTH_M
    false_cum = np.concatenate(([0], np.cumsum(~depth_ok)))
    starts = np.arange(0, depth.size - window + 1)
    falses_in_window = false_cum[starts + window] - false_cum[starts]
    candidate_ks = starts[falses_in_window == 0] + window - 1
    for k in candidate_ks:
        k_int = int(k)
        prefix_force_ok = bool(np.all(np.abs(tr.contact_force[: k_int + 1, 2]) <= MAX_ABS_FZ_N))
        prefix_tilt_ok = bool(np.all(tr.tilt_deg[: k_int + 1] < MAX_TILT_DEG))
        if prefix_force_ok and prefix_tilt_ok:
            return ProbeSideOutcome(
                True,
                k_int,
                np.array(tr.tip_pos[k_int, :2], dtype=float, copy=True),
                float(tr.time[k_int]),
                None,
            )
    # classify why the earliest geometry never completed
    if candidate_ks.size > 0:
        return ProbeSideOutcome(False, None, None, None, "prefix force/tilt bound violated")
    if not np.any(depth_ok):
        return ProbeSideOutcome(False, None, None, None, "FK depth persistence never reached")
    return ProbeSideOutcome(False, None, None, None, "depth persistence never held for 0.1 s")


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


@dataclass
class ProbeEvent:
    attempt_index: int
    bearing_rad: float
    kind: str  # "unseatable" | "UPDATED" | "SALVAGE" | "ABORT"
    nis: Optional[float] = None
    plus_time_s: Optional[float] = None
    minus_time_s: Optional[float] = None


def attempt_probe_pair(
    filter_: StateFilter,
    control_task: TaskSpec,
    c0: NDArray[np.float64],
    bearing_rad: float,
    corruption: Optional[CorruptionModel],
    model,
    data,
) -> tuple[Optional[FilterUpdateRecord], list[ProbeEvent], list[float]]:
    """One registered antipodal pair attempt through real physics. A side
    that fails operational completion leaves the filter untouched (G5)."""
    events: list[ProbeEvent] = []
    times: list[float] = []
    u = np.array([math.cos(bearing_rad), math.sin(bearing_rad)])
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
    if not (plus_out.complete and minus_out.complete):
        events.append(
            ProbeEvent(0, bearing_rad, "unseatable",
                        plus_time_s=plus_out.completion_time_s,
                        minus_time_s=minus_out.completion_time_s)
        )
        return None, events, times
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
                   minus_time_s=minus_out.completion_time_s)
    )
    return record, events, times


@dataclass
class HaplousExecution:
    guided: bool
    production_start_xy: NDArray[np.float64]
    used_fallback: bool
    spiral_usage: str  # "guided_drop" | "guided_spiral" | "baseline_fallback"
    probe_events: list[ProbeEvent] = field(default_factory=list)
    probe_side_motions: int = 0
    probe_time_s: float = 0.0
    filter_records: list[FilterUpdateRecord] = field(default_factory=list)
    nees: Optional[float] = None
    h_error_norm_m: Optional[float] = None
    confidently_wrong: bool = False


def nees_and_errors(
    filter_: StateFilter, true_hole_xy: NDArray[np.float64]
) -> tuple[Optional[float], Optional[float], bool]:
    h_hat = filter_.state_m[:2]
    p_hh = filter_.covariance_m2[:2, :2]
    err = h_hat - np.asarray(true_hole_xy, dtype=float)
    if filter_._observation_history:  # noqa: SLF001 - evaluation-only read
        solved = np.linalg.solve(p_hh, err)
        nees = float(err @ solved)
        sigma = np.sqrt(np.diag(p_hh))
        confidently_wrong = bool(np.any(np.abs(err) > 3.0 * sigma))
    else:
        nees = None
        confidently_wrong = False
    return nees, float(np.linalg.norm(err)), confidently_wrong


def run_haplous_arm(
    scenario: Scenario,
    candidate: HaplousCandidate,
    baseline_v_t_m_per_s: float,
    *,
    model,
    data,
) -> tuple[HaplousExecution, TrialResult, dict]:
    """The locked treatment arm. Returns the execution record, the one
    designated production trial, and the evaluation diagnostics dict."""
    _, control_task = build_tasks(scenario.clearance_offset_m)
    c0 = scenario.c0_xy_m
    corruption = corruption_for(scenario)
    filter_ = make_filter(c0)
    execution = HaplousExecution(
        guided=False,
        production_start_xy=c0.copy(),
        used_fallback=True,
        spiral_usage="baseline_fallback",
    )
    updated_count = 0
    for attempt in range(candidate.max_pair_attempts):
        bearing = scenario.first_probe_bearing_rad + BEARING_OFFSETS_RAD[attempt]
        record, events, times = attempt_probe_pair(
            filter_, control_task, c0, bearing, corruption, model, data
        )
        for ev in events:
            ev.attempt_index = attempt
        execution.probe_events.extend(events)
        execution.probe_side_motions += 2
        execution.probe_time_s += sum(times)
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

    nees, h_err, cw = nees_and_errors(filter_, np.zeros(2))
    execution.nees = nees
    execution.h_error_norm_m = h_err
    execution.confidently_wrong = cw

    if updated_count >= candidate.required_updated_pairs:
        execution.guided = True
        execution.used_fallback = False
        h_hat = filter_.state_m[:2]
        execution.production_start_xy = h_hat.copy()
        if candidate.guided_execution == "straight_drop":
            execution.spiral_usage = "guided_drop"
            prod_cfg = episode_from_world_start(control_task, h_hat, spiral=None)
        else:
            execution.spiral_usage = "guided_spiral"
            prod_cfg = episode_from_world_start(
                control_task, h_hat,
                spiral=spiral_for(control_task, baseline_v_t_m_per_s),
            )
    else:
        prod_cfg = episode_from_world_start(
            control_task, c0,
            spiral=spiral_for(control_task, baseline_v_t_m_per_s),
        )
    production = run_episode(prod_cfg, pen_allow=PEN_ALLOW_M, model=model, data=data)
    diagnostics: dict = {
        "final_filter_state_m": filter_.state_m.tolist(),
        "final_filter_h_cov_m2": filter_.covariance_m2[:2, :2].tolist(),
        "updated_pair_count": updated_count,
    }
    return execution, production, diagnostics


def run_baseline_arm(
    scenario: Scenario,
    v_t_m_per_s: float,
    *,
    model,
    data,
) -> TrialResult:
    """The locked baseline arm: one full production insertion from c0."""
    _, control_task = build_tasks(scenario.clearance_offset_m)
    cfg = episode_from_world_start(
        control_task,
        scenario.c0_xy_m,
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


def grade_production(tr: TrialResult, physical_clearance_m: float) -> Grade:
    """Primary outcome recomputed from the full trace with the PHYSICAL
    task: geometry persisted 0.1 s by 16.3 s, |F_z| <= 25 N and tilt < 10 deg
    over the whole episode."""
    tol = physical_clearance_m + PEN_ALLOW_M
    tip_z = tr.tip_pos[:, 2]
    tip_lat = np.linalg.norm(tr.tip_pos[:, :2], axis=1)
    mouth_lat = tr.mouth_lateral  # peg axis crossing z=0 vs world origin (true hole)
    depth = tr.z_flat - tip_z
    window = DEPTH_PERSIST_SAMPLES
    geometry_ok = (
        (depth >= TARGET_DEPTH_M)
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
    force_integral = float(np.trapezoid(np.abs(tr.contact_force[:, 2]), tr.time))
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
