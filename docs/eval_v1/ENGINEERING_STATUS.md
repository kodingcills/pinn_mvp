# Engineering status: Haplous Generalized Execution Evaluation

This is the persistent engineering state file. Update it at every iteration.
Last update: 2026-11-04, iteration 1, branch `claude/magical-curie-si1vjr`.

## Milestones

| # | Completion criterion | Status |
|---|---|---|
| 1 | M0 implemented and tested | **DONE**. Schemas provisional (no v1.1 package) |
| 2 | Track B contract engine passes required tests | **BLOCKED**: 38 fixtures not in repo |
| 3 | G0 comparison implemented and labelled | **BLOCKED**: G0 definition not in repo |
| 4 | Every development run can generate a verifiable manifest | **DONE** for CPU/policy-free runs (`record_run`, CLI `run`). Model-in-the-loop manifests are supported by the schema but have never been exercised (GPU) |
| 5 | LIBERO-Dynamic audited at pinned revision | **DONE**: `ef12443`, `LIBERO_DYNAMIC_AUDIT.md`. HF assets unverified (network) |
| 6 | Available CPU-only preflight executed | **DONE**: formal zero-policy smoke ×3, upstream unit tests |
| 7 | GPU work has costed handoff | **DONE**: `GPU_HANDOFF.md`, approval pending. Prices unverified (vendor pages blocked) |
| 8 | Evidence/repro committed to branch | **DONE** (this commit) |

## Implemented

- `evaluation/run_registry.py`: strict JSON, RFC 8785 canonicalization,
  Draft 2020-12 validation with format checking, immutable manifests,
  hash-chained append-only ledger, explicit lifecycle, SHA-256 artifact
  verification, retrieval, comparison, test-only Ed25519 approvals,
  `record_run`, and the CLI (`run|show|verify|compare`).
- `evaluation/schemas/run_manifest.schema.json`,
  `evaluation/schemas/ledger_event.schema.json` (provisional).
- `scripts/run_libero_dynamic_cpu_smoke.py`,
  `scripts/compare_libero_smoke_runs.py`.
- `tests/test_run_registry.py` (47 tests).
- `pyproject.toml`: new `registry` extra. `all` now also declares `h5py`
  (already imported by `tests/test_realdata.py`), the registry deps, and
  `hypothesis`. `uv.lock` updated.

Interfaces: `Registry(root, clock)`, `.register/.start/.finish/.verify/.check/
.get/.compare/.record_approval`, `record_run(...)`, `strict_loads`,
`canonical_bytes`, `sign_approval/verify_approval`. No existing interface was
changed. No capability/refusal/hold/revalidation/recovery code was touched.

## Tests

- Full suite: 459 passed, 4 failed, 1 xfailed (baseline was 412/4/1).
- The 4 failures are pre-existing, with root causes in EVIDENCE_REPORT.md §A:
  `test_r05.py::test_g8_evidence_fingerprints_pass_on_committed_files`,
  `test_r05.py::test_g8_fingerprints_fail_closed_on_drift`,
  `test_r05.py::test_g5_unseatable_pair_leaves_filter_unchanged`
  (last-ULP drift in a locked N5 covariance), and
  `test_rollout.py::TestRunEpisode::test_capture_localization_error_within_clearance`
  (`capture_step is None`; not diagnosed).

## Changes since previous iteration

First iteration. Commits: `8a29040` (M0), `897f161` (Campaign 0A smoke and the
provenance fix), plus this docs commit.

## Blockers and unresolved assumptions

1. **v1.1 starter package absent.** Blocks Track B (38 fixtures), G0, the
   canonical schemas and the work orders. Needs the user to upload or commit it.
2. **Network policy** blocks `huggingface.co`, `www.runpod.io`, `lambda.ai`.
   Checkpoint sizes/revisions and vendor prices are unverified.
3. **Paid GPU** not authorized (approval request in GPU_HANDOFF.md).
4. Assumption: π0.5 ≈ 3B params / ≥16 GB VRAM. Not verified in this session.
5. Pre-existing: `AGENTS.md` test count (396) is stale. `h5py` was undeclared
   (now in `all`).

## Next executable actions

1. When the v1.1 package arrives: run its validation, swap in its schemas
   (keeping M0's tests), implement the Track B engine against the 38 fixtures,
   add independent property tests, and implement G0 as a labelled ablation.
2. With GPU approval: run G1 from GPU_HANDOFF.md, recording every step in M0.
3. With HF allowed: hash the π0.5 checkpoint and pin the HF revision.
4. Workstream D: extract a shared "run outcome" record only when the second
   consumer (Track B traces) exists. Not before.
