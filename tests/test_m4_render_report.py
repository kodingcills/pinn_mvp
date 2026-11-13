"""M4 report renderer: deterministic, sourced from committed artifacts, honest labels."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import m4_render_report as rr  # noqa: E402


def test_dryrun_report_is_deterministic_and_carries_every_required_section():
    text = rr.render("dryrun")
    assert text == rr.render("dryrun")
    assert "1. lo > 0:" in text and "4. Otherwise:" in text
    for heading in ("Headline verdicts", "Every rescued and harmed", "Exclusions", "HES hard gates",
                    "reset 1.0", "reset 0.0", "What this does NOT show", "SCRIPTED-POLICY SIMULATION"):
        assert heading in text
    assert "| C1 | stress" in text and "| C2 | control" in text


def _fake_repo(tmp_path: Path) -> Path:
    (tmp_path / "docs" / "eval_program" / "designs").mkdir(parents=True)
    shutil.copy(rr.PREREG, tmp_path / "docs" / "eval_program" / "designs" / rr.PREREG.name)
    shutil.copytree(REPO / "results" / "m4" / "dryrun", tmp_path / "results" / "m4" / "dryrun",
                    ignore=shutil.ignore_patterns("m0"))
    return tmp_path


def test_positive_verdict_with_a_harmed_scenario_is_labelled_advantage_with_regressions(tmp_path, monkeypatch):
    root = _fake_repo(tmp_path)
    path = root / "results" / "m4" / "dryrun" / "official_metrics.json"
    off = json.loads(path.read_text(encoding="utf-8"))
    rows = [json.loads(x) for x in (path.parent / "raw_rows.jsonl").read_text(encoding="utf-8").splitlines()]
    recorded = {json.loads(x)["scenario_id"] for x in (path.parent / "replay_index.jsonl").read_text().splitlines()}
    sid = next(r["scenario_id"] for r in rows
               if r["metadata"]["population_id"] == "m4_stress@v1" and r["scenario_id"] not in recorded)
    stress = off["populations"]["stress"]
    stress["paired"]["haplous_mediated_vs_g1_incumbent"]["harmed"] = [sid]
    stress["confirmatory"]["C1"].update(decision="Haplous better", n_harmed=1)
    path.write_text(json.dumps(off), encoding="utf-8")
    monkeypatch.setattr(rr, "REPO", root)
    monkeypatch.setattr(rr, "PREREG", root / "docs" / "eval_program" / "designs" / rr.PREREG.name)
    text = rr.render("dryrun")
    assert "**Haplous better; advantage with regressions**" in text
    assert f"| `{sid}` |" in text and "not recorded (cap of 25 pairs)" in text


def test_check_flags_a_hand_edited_report(tmp_path):
    out = tmp_path / "M4_REPORT.md"
    assert rr.main(["--phase", "dryrun", "--out", str(out)]) == 0
    assert rr.main(["--phase", "dryrun", "--out", str(out), "--check"]) == 0
    out.write_text(out.read_text(encoding="utf-8").replace("tie", "Haplous better", 1), encoding="utf-8")
    assert rr.main(["--phase", "dryrun", "--out", str(out), "--check"]) == 1
