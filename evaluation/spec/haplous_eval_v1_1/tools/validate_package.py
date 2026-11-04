"""Validate starter schemas and fixtures. No Tier 3 execution or signing enabled."""
from __future__ import annotations

import json
from pathlib import Path
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]


def reject_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def reject_constants(token):
    raise ValueError(f"invalid JSON constant: {token}")


def reject_unsafe_int(token):
    value = int(token)
    if abs(value) > (2**53 - 1):
        raise ValueError(f"JSON integer exceeds interoperable safe range: {token}")
    return value


def reject_lone_surrogates(value):
    if isinstance(value, str):
        if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
            raise ValueError("unpaired Unicode surrogate is not allowed")
    elif isinstance(value, list):
        for item in value:
            reject_lone_surrogates(item)
    elif isinstance(value, dict):
        for k, v in value.items():
            reject_lone_surrogates(k)
            reject_lone_surrogates(v)


def strict_loads(data: str):
    result = json.loads(data, object_pairs_hook=reject_duplicates, parse_constant=reject_constants, parse_int=reject_unsafe_int)
    reject_lone_surrogates(result)
    return result


def load(path):
    return strict_loads(Path(path).read_text(encoding="utf-8"))


def validator(schema):
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


REQUIRED_INPUT_KEYS = {
    'clock_domain','observation_clock_domain','command_clock_domain','future_skew_tolerance_ms',
    'contract_max_obs_age_ms','command_deadline_ms','required_mode','resource_id',
    'command_resource_id','active_epoch','command_epoch','ack_epoch','authority_owner',
    'command_owner','lease_valid','available_modes','allowed_mode_equivalents',
    'verified_mode_equivalents','required_sensors','available_sensors',
    'mode_specific_max_obs_age_ms','calibration_valid','mode_switch_confirmed',
    'oem_stop_active','non_substitutable_modes','observation_age_ms','command_age_ms','event','execution_state',
}


def g0_decision(scenario):
    """Intentionally narrow, noncompetitive baseline, see BASELINE_SPEC.md."""
    if scenario['oem_stop_active']:
        return 'STOP'
    if scenario['required_mode'] not in scenario['available_modes']:
        return 'BLOCK'
    if scenario['command_age_ms'] > scenario['command_deadline_ms']:
        return 'BLOCK'
    return 'ALLOW'


def validate_all():
    schemas = {f.name.removesuffix(".schema.json"): load(f) for f in sorted((ROOT/'schemas').glob('*.json'))}
    for schema in schemas.values():
        validator(schema)
    if len(schemas) != 4:
        raise AssertionError(f"Expected 4 schemas; found {len(schemas)}")
    fixture = load(ROOT/'fixtures'/'track_b_scenarios.json')
    defaults = fixture['defaults']
    if defaults.keys() != REQUIRED_INPUT_KEYS:
        raise AssertionError(f"defaults mismatch: missing={REQUIRED_INPUT_KEYS - defaults.keys()}, extra={defaults.keys() - REQUIRED_INPUT_KEYS}")
    seen = set()
    for case in fixture['cases']:
        if case['id'] in seen:
            raise AssertionError(f"duplicate case ID {case['id']}")
        seen.add(case['id'])
        if not set(case['overrides']) <= defaults.keys():
            raise AssertionError(f"unknown override in {case['id']}")
        assert case['expected'] in {'ADMIT','REFUSE','SUSPEND','TERMINATE'}, case['id']
        assert case['reason'], case['id']
        actual_g0 = g0_decision({**defaults,**case['overrides']})
        if actual_g0 != case['expected_baseline_g0']:
            raise AssertionError(f"G0 expectation mismatch in {case['id']}: {actual_g0}")
    for example in sorted((ROOT/'examples').glob('manifest-*.example.json')):
        manifest = load(example)
        validator(schemas['manifest']).validate(manifest)
    return len(schemas), len(seen)


if __name__ == '__main__':
    n_schemas, n_cases = validate_all()
    print(f'PASS {n_schemas} Draft 2020-12 schemas; {n_cases} unique fixture cases; valid CPU/VLA example manifests; complete defaults; strict parser; FormatChecker enabled')
