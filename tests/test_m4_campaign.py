"""M4.2a: pre-registration binding, stress population, independent recompute,
and the planted score/trace mismatch (docs/eval_program/designs/M4_preregistration.md)."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import m4_campaign as m4  # noqa: E402
import m4_recompute  # noqa: E402

from benchmarks.heb.population import population  # noqa: E402
from benchmarks.stress_population import STRATA, stress_population  # noqa: E402
from hes.populations import get_population, list_populations  # noqa: E402

DRYRUN = REPO / "results" / "m4" / "dryrun"
NOTE = m4.MARGIN_NOTE


def test_config_hash_is_the_one_registered_in_the_preregistration():
    digest = m4.sha256_bytes(m4.CONFIG.read_bytes())
    assert m4.PREREG_HASH.findall(m4.PREREG.read_text(encoding="utf-8")) == [digest]


def test_registration_refuses_a_config_that_drifted_from_the_preregistration(tmp_path):
    config = tmp_path / "m4_campaign.json"
    cfg = m4.load_config()
    cfg["statistics"]["equivalence_margin"] = 0.2
    config.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    problems = m4.registration_problems(config, m4.PREREG)
    assert any("not the single value registered" in p for p in problems)


def test_registration_refuses_a_pinned_source_that_drifted(tmp_path):
    cfg = m4.load_config()
    cfg["scoring_epoch"]["scoring_sources"]["hes/metrics.py"] = "0" * 64
    config = tmp_path / "m4_campaign.json"
    config.write_text(json.dumps(cfg), encoding="utf-8")
    prereg = tmp_path / "prereg.md"
    prereg.write_text(f"config_sha256: `{m4.sha256_bytes(config.read_bytes())}`\n", encoding="utf-8")
    assert any("scoring_sources drift: hes/metrics.py" in p for p in m4.registration_problems(config, prereg))


def test_m4_populations_are_registered_and_control_extends_dev():
    ids = {p["id"] for p in list_populations()}
    assert {"m4_control@v1", "m4_stress@v1", "dev@v1"} <= ids
    control = get_population("m4_control").generate()
    assert [s.id for s in control[:40]] == [s.id for s in population(40, 7)]


def test_stress_population_is_deterministic_stratified_and_avoids_exclusions():
    short, long = stress_population(20, 1113), stress_population(25, 1113)
    assert short == long[:20]
    strata = [next(t for t in s.tags if t.startswith("stratum:")) for s in short]
    assert sorted(strata) == sorted(f"stratum:{name}" for name in STRATA for _ in range(4))
    for s in get_population("m4_stress").generate():
        assert not (s.path_end_m is not None and s.disturbances)
        assert s.max_force_n is None or s.max_force_n < 100.0
        assert s.max_tilt_deg is None or s.max_tilt_deg < 45.0
        windows = s.dropout_windows_s + tuple(w[:2] for w in s.stale_windows_s) + s.command_rejection_windows_s
        assert all(0.25 <= hi - lo <= 1.0 for lo, hi in windows)


def test_check_aligned_rejects_missing_and_duplicate_pairs():
    rows = [{"scenario_id": "a", "arm": "x"}, {"scenario_id": "a", "arm": "y"}]
    m4.check_aligned(rows, ["x", "y"])
    with pytest.raises(ValueError, match="unpaired"):
        m4.check_aligned(rows[:1], ["x", "y"])
    with pytest.raises(ValueError, match="duplicate"):
        m4.check_aligned(rows + rows[:1], ["x", "y"])


def test_selection_keeps_every_harmed_then_rescued_pair_up_to_the_cap():
    cfg = m4.load_config()
    pair = {"harmed": ["h1", "h2"], "rescued": ["r1"], "both": 0, "neither": 0}
    official = {"populations": {"stress": {"paired": {
        f"{c}_vs_{r}": pair for c, r in cfg["recording"]["comparisons"]}}}}
    entries = m4.select_recordings("campaign", official, cfg, {"stress": ["s1"]})
    assert {e["scenario_id"] for e in entries} == {"h1", "h2", "r1"}
    assert all(len(e["reasons"]) == 1 for e in entries)
    cfg["recording"]["cap_pairs_per_population"] = 2
    capped = m4.select_recordings("campaign", official, cfg, {"stress": ["s1"]})
    assert {e["scenario_id"] for e in capped} == {"h1", "h2"}


@pytest.mark.parametrize("decide", [m4.decide, m4_recompute.decision], ids=["driver", "recompute"])
@pytest.mark.parametrize(("lo", "hi", "label"), [
    (0.01, 0.10, "Haplous better"),
    (0.002, 0.02, "Haplous better" + NOTE),  # overlapped "tie" under the r1 table
    (0.01, 0.05, "Haplous better" + NOTE),
    (0.01, 0.0500001, "Haplous better"),
    (-0.10, -0.01, "G1 better"),
    (-0.02, -0.002, "G1 better" + NOTE),
    (-0.05, -0.01, "G1 better" + NOTE),
    (-0.0500001, -0.01, "G1 better"),
    (-0.05, 0.05, "tie (equivalent within 0.05)"),
    (0.0, 0.03, "tie (equivalent within 0.05)"),
    (-0.03, 0.0, "tie (equivalent within 0.05)"),
    (-0.06, 0.03, "inconclusive at this sample size"),
    (-0.03, 0.06, "inconclusive at this sample size"),
])
def test_decision_rules_are_ordered_first_match_wins(decide, lo, hi, label):
    assert decide(lo, hi, 0.05) == label


def test_exclusion_summary_lists_count_and_ids_for_every_rule():
    summary = m4.exclusion_summary({"a": ["mesh_artifact_named"],
                                    "b": ["mesh_sensitive_slide_disturbance", "g1_unimplemented_check:state_too_stale"],
                                    "c": ["g1_unimplemented_check:missing_force_measurement"]})
    assert summary == {"mesh_artifact_named": {"count": 1, "scenario_ids": ["a"]},
                       "mesh_sensitive_slide_disturbance": {"count": 1, "scenario_ids": ["b"]},
                       "g1_unimplemented_check": {"count": 2, "scenario_ids": ["b", "c"]}}
    assert all(v["count"] == 0 for v in m4.exclusion_summary({}).values())


def test_dryrun_reports_exclusions_from_the_sealed_schedule():
    official = json.loads((DRYRUN / "official_metrics.json").read_text(encoding="utf-8"))
    for pop, metrics in official["populations"].items():
        sched = DRYRUN / "m0" / "runs" / m4.episodes_run_id("dryrun", pop) / "artifacts" / "experiment" / "schedule.json"
        excluded = json.loads(sched.read_text(encoding="utf-8"))["excluded"]
        assert metrics["exclusions"] == m4.exclusion_summary(excluded)
        assert set(metrics["exclusions"]) == set(m4.EXCLUSION_RULES)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=repo, check=True,
                   capture_output=True)


def test_provenance_ignores_the_drivers_own_output_roots(tmp_path, monkeypatch):
    from evaluation import experiment, run_registry

    (tmp_path / "tracked.txt").write_text("x\n", encoding="utf-8")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "init")
    monkeypatch.setattr(run_registry, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(m4, "RESULTS", tmp_path / "results" / "m4")
    m4.exclude_own_outputs_from_dirty(monkeypatch.setattr)
    (tmp_path / "results" / "m4" / "recordings_store").mkdir(parents=True)
    (tmp_path / "results" / "m4" / "recordings_store" / "rec.json").write_text("{}", encoding="utf-8")
    (tmp_path / "results" / "m4" / "raw_rows.jsonl").write_text("{}", encoding="utf-8")
    assert experiment._git_state()[1] is False
    assert run_registry.collect_provenance((), (), tmp_path / "elsewhere")["git_dirty"] is False
    (tmp_path / "tracked.txt").write_text("changed\n", encoding="utf-8")
    assert experiment._git_state()[1] is True
    assert run_registry.collect_provenance((), (), tmp_path / "elsewhere")["git_dirty"] is True


def test_dryrun_provenance_records_a_clean_tree():
    files = list((DRYRUN / "m0" / "runs").glob("*/manifest.json"))
    files += list((DRYRUN / "m0" / "runs").glob("*/artifacts/experiment/provenance.json"))
    assert files
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data.get("provenance", data)["git_dirty"] is False, path


def test_recordings_store_is_gitignored():
    probe = "results/m4/recordings_store/dryrun/m0/ledger.jsonl"
    assert subprocess.run(["git", "check-ignore", "-q", probe], cwd=REPO).returncode == 0


def test_exemplars_are_one_harmed_and_one_rescued_pair_under_5_mb():
    cfg = m4.load_config()
    label = "{}:{}_vs_{}".format
    cand, ref = cfg["recording"]["comparisons"][0]

    def pair(sid, kind, mb):
        return [{"population": "stress", "scenario_id": sid, "arm": arm, "reasons": [label(kind, cand, ref)],
                 "artifacts": {"telemetry.jsonl": {"bytes": int(mb * 1024 * 1024)}}} for arm in (cand, ref)]

    entries = pair("h_big", "harmed", 5.0) + pair("h1", "harmed", 1) + pair("h2", "harmed", 1) + pair("r1", "rescued", 1)
    chosen = m4.choose_exemplars(entries, cfg)
    assert sorted((e["scenario_id"], e["arm"]) for e in chosen) == sorted(
        (sid, arm) for sid in ("h1", "r1") for arm in (cand, ref))
    assert m4.choose_exemplars(pair("p", "pipeline_probe", 1), cfg) == []


def _recompute(rows: Path) -> dict:
    out = subprocess.run([sys.executable, str(REPO / "scripts" / "m4_recompute.py"), "--rows", str(rows),
                          "--config", str(m4.CONFIG)], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)["populations"]


def _official_without_gates() -> dict:
    official = json.loads((DRYRUN / "official_metrics.json").read_text(encoding="utf-8"))
    return {p: {k: v for k, v in m.items() if k not in m4.NOT_RECOMPUTED} for p, m in official["populations"].items()}


def test_independent_recompute_matches_hes_on_the_dryrun_rows():
    assert m4.compare_metrics(_official_without_gates(), _recompute(DRYRUN / "raw_rows.jsonl")) == []


def test_recompute_disagrees_when_a_raw_row_is_altered(tmp_path):
    rows = [json.loads(x) for x in (DRYRUN / "raw_rows.jsonl").read_text(encoding="utf-8").splitlines()]
    target = next(r for r in rows if r["arm"] == "haplous_mediated")
    target["bounded_success"] = not target["bounded_success"]
    planted = tmp_path / "rows.jsonl"
    planted.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    assert m4.compare_metrics(_official_without_gates(), _recompute(planted)) != []


@pytest.fixture(scope="module")
def regenerated(tmp_path_factory):
    """Dry-run copies without the gitignored store (a fresh checkout), the index
    trimmed to one recording; "planted" also carries a wrong digest in the index."""
    out = {}
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(m4, "registration_problems", lambda *a, **k: [])
        for name in ("clean", "planted"):
            base = tmp_path_factory.mktemp(name)
            shutil.copytree(DRYRUN, base / "dryrun")
            index = base / "dryrun" / "replay_index.jsonl"
            entry = json.loads(index.read_text(encoding="utf-8").splitlines()[0])
            if name == "planted":
                entry["artifacts"]["telemetry.jsonl"]["sim_sha256"] = "0" * 64
            index.write_text(json.dumps(entry) + "\n", encoding="utf-8")
            mp.setattr(m4, "RESULTS", base)
            out[name] = (base, entry, m4.validate("dryrun", m4.load_config()))
    return out


def test_absent_bulk_recording_is_regenerated_and_matches_the_index(regenerated):
    from evaluation.run_registry import Registry

    base, entry, problems = regenerated["clean"]
    assert problems == []
    assert Registry(base / "recordings_store" / "dryrun" / "m0").state(entry["m0_run_id"]) == "VERIFIED"


def test_regenerated_recording_with_a_digest_mismatch_fails_validation(regenerated):
    _, _, problems = regenerated["planted"]
    assert any("telemetry.jsonl digest differs from replay_index.jsonl" in p for p in problems), problems


def _present_copy(regenerated, tmp_path, monkeypatch) -> tuple[Path, dict]:
    base, entry, _ = regenerated["clean"]
    shutil.copytree(base, tmp_path, dirs_exist_ok=True)
    monkeypatch.setattr(m4, "RESULTS", tmp_path)
    monkeypatch.setattr(m4, "registration_problems", lambda *a, **k: [])
    return tmp_path / "recordings_store" / "dryrun" / "m0" / "runs" / entry["m0_run_id"] / "artifacts", entry


def test_present_store_recording_validates_without_regenerating(regenerated, tmp_path, monkeypatch):
    _present_copy(regenerated, tmp_path, monkeypatch)
    monkeypatch.setattr(m4, "seal_recording", lambda *a, **k: pytest.fail("regenerated a present recording"))
    assert m4.validate("dryrun", m4.load_config()) == []


def test_planted_score_trace_mismatch_fails_validation(regenerated, tmp_path, monkeypatch):
    """A forger who re-seals M0 (simulated: M0 check returns clean) and flips the
    recorded outcome is still caught by the score/trace comparison."""
    from evaluation.run_registry import Registry

    artifacts, _ = _present_copy(regenerated, tmp_path, monkeypatch)
    meta = json.loads((artifacts / "metadata.json").read_text(encoding="utf-8"))
    meta["outcome"]["bounded_success"] = not meta["outcome"]["bounded_success"]
    (artifacts / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    assert any("M0" in p for p in m4.validate("dryrun", m4.load_config()))
    monkeypatch.setattr(Registry, "check", lambda self, run_id, trusted_keys=None: [])
    problems = m4.validate("dryrun", m4.load_config())
    assert any("bounded_success" in p and "trace" in p for p in problems), problems


def test_score_trace_check_catches_changed_machine_commands(regenerated, tmp_path, monkeypatch):
    artifacts, entry = _present_copy(regenerated, tmp_path, monkeypatch)
    pop_id = m4.load_config()["populations"][entry["population"]]["id"]
    row = next(r for r in map(json.loads, (DRYRUN / "raw_rows.jsonl").read_text(encoding="utf-8").splitlines())
               if (r["metadata"]["population_id"], r["scenario_id"], r["arm"])
               == (pop_id, entry["scenario_id"], entry["arm"]))
    assert m4.score_trace_problems(row, artifacts) == []
    row["task_metrics"]["applied_commands_sha256"] = "0" * 64
    assert any("machine-applied commands differ" in p for p in m4.score_trace_problems(row, artifacts))
