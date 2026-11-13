import copy
import json
import sys
from pathlib import Path

import pytest
from jsonschema.exceptions import ValidationError

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from validate_package import strict_loads, load, validator, validate_all, g0_decision, REQUIRED_INPUT_KEYS

SCHEMAS={f.name.removesuffix(".schema.json"):load(f) for f in sorted((ROOT/'schemas').glob('*.json'))}
CASES=load(ROOT/'fixtures'/'track_b_scenarios.json')


def test_all_package_validation():
    assert validate_all()==(4,38)


def test_cpu_manifest_passes_without_vla_or_simulator():
    obj=load(ROOT/'examples/manifest-contract.example.json')
    assert 'policy' not in obj and 'benchmark' not in obj
    assert 'simulator' not in obj['environment']
    validator(SCHEMAS['manifest']).validate(obj)


def test_vla_requires_checkpoint():
    obj=load(ROOT/'examples/manifest-vla.example.json')
    del obj['policy']
    with pytest.raises(ValidationError):validator(SCHEMAS['manifest']).validate(obj)


def test_contract_rejects_fake_model_block():
    obj=load(ROOT/'examples/manifest-contract.example.json')
    obj['policy']={'name':'fake'}
    with pytest.raises(ValidationError):validator(SCHEMAS['manifest']).validate(obj)


def test_all_four_schemas_well_formed():
    assert len(SCHEMAS)==4
    for sc in SCHEMAS.values():validator(sc)


@pytest.mark.parametrize('text',['{"a":1,"a":2}','{"outer":{"x":0,"x":1}}','{"n": NaN}','{"n":Infinity}'])
def test_strict_json_parser_rejects_bad_input(text):
    with pytest.raises(ValueError):strict_loads(text)


def test_formatchecker_rejects_invalid_datetime():
    obj=load(ROOT/'examples/manifest-contract.example.json')
    obj['created_at']='not-a-date'
    with pytest.raises(ValidationError):validator(SCHEMAS['manifest']).validate(obj)


def test_formatchecker_rejects_impossible_datetime():
    obj=load(ROOT/'examples/manifest-contract.example.json')
    obj['created_at']='2026-99-99T28:72:01Z'
    with pytest.raises(ValidationError):validator(SCHEMAS['manifest']).validate(obj)


def test_fixture_defaults_total():
    assert set(CASES['defaults']) == REQUIRED_INPUT_KEYS
    for c in CASES['cases']:
        assert set(c['overrides']) <= set(CASES['defaults'])
        assert g0_decision({**CASES['defaults'],**c['overrides']}) == c['expected_baseline_g0']


def test_terminals_and_interactions_present():
    assert sum(c['expected']=='TERMINATE' for c in CASES['cases'])>=3
    ids={c['id'] for c in CASES['cases']}
    assert {'precedence_epoch_over_capability_and_clock','behavioral_native_force_approved_and_verified','mode_dependent_freshness','active_lease_expires','future_one_ms_allowed','future_three_ms_rejected'} <= ids


def test_authorization_signing_fields_would_include_decision():
    envelope=SCHEMAS['approval-envelope']['properties']
    assert 'decision' in envelope and 'payload_digest' in envelope
    assert 'signature_ed25519_base64' in envelope
    assert 'decision' in SCHEMAS['approval-envelope']['description']


def test_invalid_approval_signature_shape():
    d={'schema_version':'1.1','request_id':'example','payload_digest':'sha256:'+'0'*64,'signer_key_id':'test-only',
      'approver_id':'test','decision':'APPROVED','signed_at':'2026-11-03T20:00:00Z','signature_ed25519_base64':'abc'}
    with pytest.raises(ValidationError):validator(SCHEMAS['approval-envelope']).validate(d)


def test_bad_run_manifest_hash_rejected():
    obj=load(ROOT/'examples/manifest-contract.example.json')
    obj['analysis_code_digest']='missing-hash'
    with pytest.raises(ValidationError):validator(SCHEMAS['manifest']).validate(obj)


def test_lone_unicode_surrogate_rejected():
    with pytest.raises(ValueError):
        strict_loads('"\\ud800"')


def test_json_integer_over_safe_range_rejected():
    with pytest.raises(ValueError):
        strict_loads('{"id":9007199254740993}')
