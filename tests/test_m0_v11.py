"""M0 reconciliation against the canonical Haplous Eval v1.1 package.

Covers the M0 test list in CLAUDE_WORK_ORDER.md and the test plan in SIGNING_PROTOCOL.md:
manifest schema (track-conditional, FormatChecker), placeholders, Tier 3 refusal, approval
envelope signing/verification, field tampering, expiry, wrong signer, replayed nonce, key
material refusal, and the projection onto ledger-event.schema.json.

Signing happens ONLY here, with ephemeral test keys (protocol: the private key stays
outside agent processes; the registry verifies only).
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from hypothesis import given, settings
from hypothesis import strategies as st

from evaluation.run_registry import (
    V11,
    Registry,
    RegistryError,
    export_v11_events,
    payload_digest,
    schema_errors,
    signing_message,
    strict_loads,
)

SPEC = Path(__file__).resolve().parents[1] / "evaluation" / "spec" / "haplous_eval_v1_1"
T0 = datetime(2026, 11, 4, 12, tzinfo=UTC)


def d(tag: str) -> str:
    return "sha256:" + hashlib.sha256(tag.encode()).hexdigest()


class Clock:
    def __init__(self):
        self.t = T0

    def __call__(self):
        self.t += timedelta(seconds=1)
        return self.t


def contract_manifest(run_id="c1", **over):
    m = {
        "schema_version": "1.1", "run_id": run_id, "tier": 1, "track": "contract",
        "created_at": "2026-11-04T12:00:00Z",
        "sources": {"haplous": {"uri": "https://github.com/kodingcills/pinn_mvp", "commit": "504fcc7" + "a" * 33}},
        "environment": {"python": "3.12.3", "hardware": "cpu", "clock_semantics": "not_applicable"},
        "run_command": ["python", "-m", "evaluation.track_b"], "analysis_code_digest": d("analysis"),
        "artifacts": [{"uri": "fixtures/track_b_scenarios.json", "sha256": d("fx")[7:]}],
        "execution": {"stack": "track_b_engine", "mode": "deterministic"},
        "contract_suite": {"name": "track_b", "fixture_set_digest": d("fx"), "truth_protocol_version": "1.1",
                           "baseline_profile": "baseline_g0_minimal_guards"},
    }
    m.update(over)
    return m


@pytest.fixture
def reg(tmp_path):
    return Registry(tmp_path / "reg", clock=Clock())


# ---------------- manifests ----------------


def test_canonical_contract_manifest_registers(reg):
    reg.register(contract_manifest())
    assert reg.state("c1") == "REGISTERED"
    assert reg.manifest("c1")["track"] == "contract"


@pytest.mark.parametrize("name", ["manifest-contract.example.json", "manifest-vla.example.json"])
def test_package_examples_are_schema_valid_but_refused_as_runs(reg, name):
    # README: examples carry zero digests / dummy commits and are NOT real run manifests.
    example = strict_loads((SPEC / "examples" / name).read_bytes())
    assert schema_errors(V11["manifest"], example) == []
    with pytest.raises(RegistryError, match="placeholder|path-safe"):
        reg.register(example)
    assert reg.events()[-1]["event"] == "rejected_input"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda m: m.update(tier=3),  # Tier 3 disabled
        lambda m: m.update(run_id="../x"),
        lambda m: m.update(created_at="2026-02-30T00:00:00Z"),  # FormatChecker
        lambda m: m.update(created_at="2026-11-04"),
        lambda m: m.update(policy={"name": "p", "checkpoint_digest": d("c"), "inference_impl": "x", "seed_policy": "fixed"}),
        lambda m: m.update(benchmark={"name": "b", "protocol_version": "1", "scenario_population": "x",
                                      "split": "development", "task_config_digest": d("t")}),
        lambda m: m.pop("contract_suite"),
        lambda m: m.update(analysis_code_digest="sha256:" + "0" * 64),
        lambda m: m["contract_suite"].update(fixture_set_digest="sha256:" + "0" * 64),
        lambda m: m.update(track="vla"),  # vla requires benchmark/policy
        lambda m: m.update(unknown_field=1),
    ],
)
def test_invalid_canonical_manifests_rejected(reg, mutate):
    m = contract_manifest()
    mutate(m)
    with pytest.raises(RegistryError):
        reg.register(m)
    assert [e["event"] for e in reg.events()] == ["rejected_input"]


def test_duplicate_key_canonical_manifest_rejected(reg):
    text = json.dumps(contract_manifest())[:-1] + ', "tier": 0}'
    with pytest.raises(RegistryError, match="duplicate"):
        reg.register(text.encode())


def test_legacy_v0_manifests_still_accepted(reg):
    from tests.test_run_registry import manifest as v0_manifest

    reg.register(v0_manifest("legacy"))
    assert reg.state("legacy") == "REGISTERED"


# ---------------- approvals (SIGNING_PROTOCOL v1.1) ----------------


def raw_public(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def auth_payload(**over):
    p = {
        "schema_version": "1.1", "request_id": "req-1",
        "issued_at": "2026-11-04T11:00:00Z", "expires_at": "2026-11-04T18:00:00Z",
        "nonce": "n" * 40, "candidate_digest": d("cand"), "incumbent_digest": d("inc"),
        "policy_checkpoint_digest": d("ckpt"), "benchmark_digest": d("bench"),
        "trusted_runner_digest": d("runner"), "scorer_digest": d("scorer"), "analysis_code_digest": d("analysis"),
        "holdout_allocation_policy": {"pool_id": "pool", "selection_rule": "next_available", "scenario_count": 1},
        "analysis_plan": {"primary_metric": "m", "effect_direction": "higher_better", "min_worthwhile_effect_pp": 1,
                          "alpha": 0.05, "test": "t", "cluster_unit": "u", "success_rule": "success rule",
                          "guardrails": ["g"], "missing_data_policy": "count as failure"},
        "budget": {"gpu_hours_max": 0, "wall_clock_hours_max": 1, "cost_usd_max": 0},
        "output_release_policy": "aggregate_only", "stop_conditions": ["s"],
        "preflight_report_digest": d("pre"), "variant_registry_digest": d("var"),
    }
    p.update(over)
    assert schema_errors(V11["authorization-payload"], p) == []
    return p


def sign(key: Ed25519PrivateKey, payload, decision="APPROVED", **over):
    env = {"schema_version": "1.1", "request_id": payload["request_id"], "payload_digest": payload_digest(payload),
           "signer_key_id": "k1", "approver_id": "alice", "decision": decision, "signed_at": "2026-11-04T12:00:00Z"}
    env.update(over)
    env["signature_ed25519_base64"] = base64.b64encode(key.sign(signing_message(env))).decode()
    return env


@pytest.fixture
def signer():
    key = Ed25519PrivateKey.generate()
    return key, {"k1": {"public_key": raw_public(key), "approver_id": "alice"}}


def test_signing_message_is_jcs_of_envelope_without_signature(signer):
    key, _ = signer
    env = sign(key, auth_payload())
    unsigned = {k: v for k, v in env.items() if k != "signature_ed25519_base64"}
    assert signing_message(env) == signing_message(unsigned)
    assert b"signature_ed25519_base64" not in signing_message(env) and b'"decision":"APPROVED"' in signing_message(env)


def test_approve_and_reject_recorded(reg, signer):
    key, trusted = signer
    reg.register(contract_manifest())
    rec = reg.record_approval("c1", auth_payload(), sign(key, auth_payload()), trusted, now=T0)
    assert rec["authorized"] is True and rec["unchecked"]
    rec2 = reg.record_approval("c1", auth_payload(nonce="m" * 40), sign(key, auth_payload(nonce="m" * 40), "REJECTED"), trusted, now=T0)
    assert rec2["authorized"] is False and rec2["decision"] == "REJECTED"
    assert reg.check("c1", trusted) == ["run has no completion record"]


ENVELOPE_MUTATIONS = {
    "decision": "REJECTED",
    "request_id": "req-2",
    "payload_digest": d("other"),
    "signer_key_id": "k2",
    "approver_id": "mallory",
    "signed_at": "2026-11-04T12:00:01Z",
    "schema_version": "1.0",
}


@pytest.mark.parametrize("field", sorted(ENVELOPE_MUTATIONS))
def test_mutating_any_signed_envelope_field_fails(reg, signer, field):
    key, trusted = signer
    trusted = {**trusted, "k2": trusted["k1"]}  # even a trusted alternate key id must not rescue it
    reg.register(contract_manifest())
    env = sign(key, auth_payload())
    env[field] = ENVELOPE_MUTATIONS[field]
    with pytest.raises(RegistryError):
        reg.record_approval("c1", auth_payload(), env, trusted, now=T0)


def test_rejected_flipped_to_approved_fails(reg, signer):
    key, trusted = signer
    reg.register(contract_manifest())
    env = sign(key, auth_payload(), "REJECTED")
    env["decision"] = "APPROVED"
    with pytest.raises(RegistryError, match="signature"):
        reg.record_approval("c1", auth_payload(), env, trusted, now=T0)


@settings(max_examples=40, deadline=None)
@given(field=st.sampled_from(["nonce", "expires_at", "budget", "analysis_plan", "output_release_policy", "scorer_digest"]))
def test_mutating_payload_after_signing_fails(tmp_path_factory, field):
    key = Ed25519PrivateKey.generate()
    trusted = {"k1": {"public_key": raw_public(key), "approver_id": "alice"}}
    reg = Registry(tmp_path_factory.mktemp("p"), clock=Clock())
    reg.register(contract_manifest())
    env = sign(key, auth_payload())
    changed = {"nonce": "z" * 40, "expires_at": "2026-11-05T18:00:00Z",
               "budget": {"gpu_hours_max": 99, "wall_clock_hours_max": 1, "cost_usd_max": 0},
               "analysis_plan": {**auth_payload()["analysis_plan"], "alpha": 0.5},
               "output_release_policy": "investigative_release", "scorer_digest": d("evil")}[field]
    with pytest.raises(RegistryError, match="payload_digest"):
        reg.record_approval("c1", auth_payload(**{field: changed}), env, trusted, now=T0)


def test_wrong_signer_untrusted_key_and_identity_binding(reg, signer):
    key, trusted = signer
    other = Ed25519PrivateKey.generate()
    reg.register(contract_manifest())
    with pytest.raises(RegistryError, match="signature"):  # trusted key id, wrong private key
        reg.record_approval("c1", auth_payload(), sign(other, auth_payload()), trusted, now=T0)
    with pytest.raises(RegistryError, match="untrusted"):
        reg.record_approval("c1", auth_payload(), sign(key, auth_payload(), signer_key_id="k9"), trusted, now=T0)
    bob = {"k1": {"public_key": raw_public(key), "approver_id": "bob"}}
    with pytest.raises(RegistryError, match="identity"):
        reg.record_approval("c1", auth_payload(), sign(key, auth_payload()), bob, now=T0)


def test_expired_and_out_of_window_authorization(reg, signer):
    key, trusted = signer
    reg.register(contract_manifest())
    with pytest.raises(RegistryError, match="expired"):
        reg.record_approval("c1", auth_payload(), sign(key, auth_payload()), trusted, now=T0 + timedelta(hours=7))
    with pytest.raises(RegistryError, match="window"):
        p = auth_payload()
        reg.record_approval("c1", p, sign(key, p, signed_at="2026-11-04T10:00:00Z"), trusted, now=T0)


def test_replayed_nonce_rejected_even_on_another_run(reg, signer):
    key, trusted = signer
    reg.register(contract_manifest())
    reg.register(contract_manifest("c2"))
    p, env = auth_payload(), sign(key, auth_payload())
    reg.record_approval("c1", p, env, trusted, now=T0)
    for run in ("c1", "c2"):
        with pytest.raises(RegistryError, match="replayed nonce"):
            reg.record_approval(run, p, env, trusted, now=T0)


def test_analysis_digest_binding_and_legacy_runs(reg, signer):
    key, trusted = signer
    reg.register(contract_manifest(analysis_code_digest=d("different")))
    with pytest.raises(RegistryError, match="analysis_code_digest"):
        reg.record_approval("c1", auth_payload(), sign(key, auth_payload()), trusted, now=T0)


def test_schema_invalid_envelope_shapes(reg, signer):
    key, trusted = signer
    reg.register(contract_manifest())
    env = sign(key, auth_payload())
    for bad in ({**env, "signature_ed25519_base64": "AAAA"}, {**env, "extra": 1},
                {**env, "decision": "MAYBE"}, {**env, "signed_at": "2026-13-01T00:00:00Z"}):
        with pytest.raises(RegistryError, match="invalid"):
            reg.record_approval("c1", auth_payload(), bad, trusted, now=T0)


@pytest.mark.parametrize("material", ["private_object", "private_bytes"])
def test_private_key_material_is_refused(reg, signer, material):
    key, _ = signer
    from cryptography.hazmat.primitives.serialization import NoEncryption, PrivateFormat

    value = key if material == "private_object" else key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    reg.register(contract_manifest())
    with pytest.raises(RegistryError, match="private"):
        reg.record_approval("c1", auth_payload(), sign(key, auth_payload()), {"k1": {"public_key": value, "approver_id": "alice"}}, now=T0)


def test_tampered_recorded_approval_detected_by_check(reg, signer):
    from evaluation.run_registry import canonical_bytes, sha256_hex

    key, trusted = signer
    reg.register(contract_manifest())
    reg.record_approval("c1", auth_payload(), sign(key, auth_payload(), "REJECTED"), trusted, now=T0)
    # Forge REJECTED -> APPROVED in the stored record and re-hash the (last) ledger line, so the
    # hash chain alone cannot see it; only signature re-verification can.
    lines = reg.ledger_path.read_bytes().splitlines()
    event = json.loads(lines[-1])
    event["payload"]["approval"]["envelope"]["decision"] = "APPROVED"
    event["payload"]["approval"]["decision"] = "APPROVED"
    event["hash"] = sha256_hex(canonical_bytes({k: v for k, v in event.items() if k != "hash"}))
    lines[-1] = canonical_bytes(event)
    reg.ledger_path.write_bytes(b"\n".join(lines) + b"\n")
    reg.events()  # chain still verifies
    assert "approval signature invalid" in reg.check("c1", trusted)


# ---------------- ledger projection ----------------


def test_projection_onto_canonical_ledger_events(reg, signer):
    key, trusted = signer
    reg.register(contract_manifest())
    reg.start("c1")
    (reg.run_dir("c1") / "artifacts").mkdir()
    (reg.run_dir("c1") / "artifacts" / "r.json").write_text("{}")
    reg.finish("c1", success=True, exit_code=0)
    reg.verify("c1")
    reg.record_approval("c1", auth_payload(), sign(key, auth_payload()), trusted, now=T0)
    out = export_v11_events(reg, "c1")
    types = [e["event_type"] for e in out["events"]]
    assert types == ["RUN_REGISTERED", "RUN_STARTED", "RUN_FINISHED", "APPROVED"]
    assert [e["sequence"] for e in out["events"]] == [0, 1, 2, 3]
    assert out["events"][0]["previous_event_digest"] is None
    assert all(e["previous_event_digest"].startswith("sha256:") for e in out["events"][1:])
    assert out["unmapped_m0_events"] == [{"m0_seq": 3, "m0_event": "verified"}]
    for e in out["events"]:
        assert schema_errors(V11["ledger-event"], e) == []
