# v1.1 Package Validation Results

Validated in the artifact-generation environment, November 3, 2026 (America/New_York).

Commands:

```bash
python tools/validate_package.py
python -m pytest -q
```

Observed:

```text
PASS 4 Draft 2020-12 schemas; 38 unique fixture cases; valid CPU/VLA example manifests; complete defaults; strict parser; FormatChecker enabled
.................. [100%]
18 passed in 1.07s
```

**Evidence boundary:** The tests validate schema well-formedness, track-conditioned examples, fixture uniqueness/defaults, G0 reference rule consistency, duplicate JSON key rejection, future-skew/termination fixture presence, rejection of invalid timestamps, and rejection of malformed digests/signature shapes. They do **not** establish that a Haplous execution engine passes the 38 behavioral cases, that real Ed25519 approvals are verified, that a secure ledger is operational, or that LIBERO-Dynamic reproduced. Schema/example digests are synthetic placeholders only.
