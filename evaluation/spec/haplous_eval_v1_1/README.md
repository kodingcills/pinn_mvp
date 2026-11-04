# Haplous Eval v1.1 — Starter Handoff

**Delivered:** 4 Draft 2020-12 schemas, 38 independent expected-outcome contract fixtures with explicit defaults and precedence, examples for CPU/VLA manifests, validation utility and pytest checks, signing protocol, baseline specification, and two separate Claude work orders.

**Not delivered:** a completed Haplous execution engine, LIBERO-Dynamic reproduction, production M0 registry, Tier 3 isolation/authorization, or physical control validation.

## Start

```bash
python -m pip install 'jsonschema[format]>=4.20' pytest
python tools/validate_package.py
python -m pytest -q
```

The signing protocol describes RFC 8785/JCS + Ed25519. When M0 is implemented, use maintained RFC8785 and Ed25519 libraries. Do not substitute Python `sort_keys=True` for JCS. No signing keys are included.

`examples/` deliberately contains **synthetic schema illustrations with zero digests and dummy commits**. They are NOT real run manifests. Replace all values with recorded hashes on the machine performing the experiment. Never invent VLA checkpoint or simulator fields for CPU contract tests.

## Assignment split

- `CPU_ASSIGNMENT.md`: launch now for M0 + Track B logic.
- `GPU_ASSIGNMENT.md`: separate Claude session on a suitable GPU machine for Campaign 0A stage 0–1.
- `CLAUDE_WORK_ORDER.md`: complete architecture, gates and broader plan.

## Safety and evidence notes

G0 is a deliberately limited comparator, not a competent industrial incumbent. `TERMINATE` and external stop-status fixtures are **software semantics tests only** and do not establish protective stop behavior or functional safety. Tier 3 remains disabled: schema validity is not signature validation, and there is no protected holdout runner in this package.
