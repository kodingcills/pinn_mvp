"""Structure and independence tests for the evaluation engine package."""

from pathlib import Path

import pytest

EVAL_DIR = Path(__file__).resolve().parent.parent / "evaluation"


def test_evaluation_file_set_is_expected():
    expected = {
        "__init__.py",
        "claims.yaml",
        "metrics.yaml",
        "registry.py",
        "stats.py",
        "taxonomy.py",
        "aggregate.py",
    }
    actual = {p.name for p in EVAL_DIR.iterdir() if p.suffix in (".py", ".yaml")}
    assert expected <= actual, f"missing: {expected - actual}"


def test_evaluation_imports_no_haplous_symbols():
    for py in EVAL_DIR.glob("*.py"):
        text = py.read_text()
        assert "import haplous" not in text, f"{py.name} imports haplous"
        assert "from haplous" not in text, f"{py.name} imports haplous"


def test_claim_registry_loads_and_has_all_twelve():
    from evaluation.registry import load_claims

    claims = load_claims()
    ids = [c.claim_id for c in claims]
    assert ids == [f"C{i}" for i in range(1, 13)]
    for c in claims:
        assert c.epistemic_status in (
            "SUPPORTED",
            "SUPPORTED_WITH_CAVEATS",
            "MIXED",
            "WEAKENED",
            "REFUTED_WITHIN_TESTED_SCOPE",
            "UNKNOWN",
            "SUPPORTED_WITHIN_TESTED_SCOPE",
        )
        assert c.statement.strip(), f"{c.claim_id} empty statement"
        assert c.current_evidence or c.claim_id in ("C6", "C11"), (
            f"{c.claim_id} has status but no evidence links"
        )


def test_metric_registry_loads_unique_names():
    from evaluation.registry import load_metrics

    metrics = load_metrics()
    names = [m["name"] for m in metrics]
    assert len(names) == len(set(names))
    required = {"SR", "BSR"}.issubset(
        {m.get("symbol") for m in metrics if m.get("symbol")}
    )


def test_registry_rejects_malformed_claim(tmp_path):
    import yaml

    from evaluation.registry import load_claims

    bad = tmp_path / "claims.yaml"
    bad.write_text(
        yaml.safe_dump({"claims": [{"claim_id": "CX", "name": "X"}]})
    )
    with pytest.raises(ValueError, match="missing fields"):
        load_claims(bad)


def test_registry_rejects_unknown_status(tmp_path):
    import yaml

    from evaluation.registry import load_claims

    base = {
        "claim_id": "CX",
        "name": "X",
        "statement": "s",
        "epistemic_status": "TRUE",
        "primary_metrics": [],
        "benchmarks": [],
        "baselines": [],
        "support_condition": "s",
        "weakening_condition": "w",
        "falsification_condition": "f",
        "known_confounders": [],
        "current_evidence": [],
    }
    bad = tmp_path / "claims.yaml"
    bad.write_text(yaml.safe_dump({"claims": [base]}))
    with pytest.raises(ValueError, match="epistemic_status"):
        load_claims(bad)