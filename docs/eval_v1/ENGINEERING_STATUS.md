# Engineering status: Haplous Generalized Execution Evaluation

This is the persistent engineering state file. Update it at every iteration.
Last update: 2026-11-04, iteration 3, branch `claude/magical-curie-si1vjr`.

## Iteration 3 headline

- **v1.1 package located and verified.** `evaluation/spec/haplous_eval_v1_1_starter.zip`
  has SHA-256 `9c4130709feb80684b34d8a291ca95cd37ef6522cf6629bf59788cb3db41390d`, as
  expected. No unsafe paths. Extracted to `evaluation/spec/haplous_eval_v1_1/`. It
  contains 4 schemas, 38 cases, defaults, BASELINE_SPEC, SIGNING_PROTOCOL,
  CPU/GPU assignments and the validator. The package's own validation
  reproduces before any edit: `validate_package.py` PASS, `pytest` 18 passed.
- **Track B: 38/38** fixture cases pass (decision, reason and required details),
  in two identical M0-recorded runs.
- **G0** implemented exactly per BASELINE_SPEC. It matches its own spec on 38/38
  and agrees with truth on 19/38. This is a mechanism-isolation ablation, not an
  incumbent.
- **M0 reconciled** to the canonical manifest, approval-envelope and signing
  protocol, with a validated projection onto the ledger-event schema. Proven
  earlier fixes are kept.
- **Checkpoint pinned** (HF reachable on the single attempt): π0.5
  `pi05/static_official` @ `ffd70008…`, LeRobot safetensors, 3,616,758,006
  params, 6.96 GiB weights.
- **GPU work:** `ACCELERATOR_DEPENDENT`. Nothing requested or provisioned. The G1
  job script now *requires* an instance-termination command.

## Delivery status

| Item | Status | Evidence |
|---|---|---|
| Reconciled M0 | **DONE** | table below; `tests/test_m0_v11.py` (36), `tests/test_run_registry.py` |
| Track B engine | **DONE** (software semantics only) | `evaluation/track_b.py` |
| 38-case results | **38/38** | runs `track-b-v1.1-001/002`, `results/track_b/` |
| Property / adversarial tests | **DONE**, mutation-checked | `tests/test_track_b.py` (79) |
| G0 comparison | **DONE**, labelled as ablation | report §B |
| Manifests + evidence | **DONE**, canonical v1.1 `contract` manifests | `results/m0_registry/runs/track-b-v1.1-*` |
| Reproduction commands | **DONE** | REPRODUCE.md, iteration 3 |
| Full-size VLA inference | **ACCELERATOR_DEPENDENT** | GPU_HANDOFF.md |

## M0 reconciliation against the canonical v1.1 package

| Canonical requirement | Before (iteration 2) | Satisfied before? | Change | Regression test |
|---|---|---|---|---|
| `manifest.schema.json` v1.1 (track-conditional; contract forbids policy/benchmark) | provisional `m0-run-manifest-v0` | **no**, different shape | new runs validated against the canonical schema with FormatChecker; v0 runs remain verifiable | `test_canonical_contract_manifest_registers`, `test_invalid_canonical_manifests_rejected` (11), `test_legacy_v0_manifests_still_accepted` |
| "All supplied digests must identify real bytes, not placeholders" | n/a | no | zero digests / zero commits refused | `test_package_examples_are_schema_valid_but_refused_as_runs` |
| Tier 3 disabled by default | no tier field | partial | `tier: 3` refused at registration | in `test_invalid_canonical_manifests_rejected` |
| Strict I-JSON, duplicate keys, invalid constants, lone surrogates, FormatChecker | yes | **yes** | none | existing strict-JSON tests; `test_duplicate_key_canonical_manifest_rejected` |
| RFC 8785 via a conformant library | yes (rfc8785 + official vectors) | **yes** | none | `test_official_rfc8785_vectors` |
| Signing message = JCS(envelope minus `signature_ed25519_base64`) | own body format, hex signature | **no** | implemented exactly; base64 signature | `test_signing_message_is_jcs_of_envelope_without_signature` |
| `payload_digest = "sha256:"+hex(SHA256(JCS(payload)))` over the whole payload | n/a | no | implemented; payload mutation fails | `test_mutating_payload_after_signing_fails` (property) |
| Every signed envelope field binds (esp. `decision`) | yes for own format | partial | per canonical envelope | `test_mutating_any_signed_envelope_field_fails` (7), `test_rejected_flipped_to_approved_fails` |
| 6(b) approver identity/permission | key trust only | **no** | `signer_key_id` maps to exactly one `approver_id` | `test_wrong_signer_untrusted_key_and_identity_binding` |
| 6(c)/(d) request_id + recomputed digest | n/a | no | checked | mutation tests |
| 6(e) expiry | n/a | no | `issued_at ≤ signed_at ≤ expires_at` and `now ≤ expires_at` | `test_expired_and_out_of_window_authorization` |
| 6(f) one-time nonce | body-digest replay key | partial | nonce checked under the ledger lock, across all runs | `test_replayed_nonce_rejected_even_on_another_run` |
| 6(g) approved digests | manifest-digest binding | partial | `analysis_code_digest` must equal the run manifest's; the other 8 payload digests have no M0 artifact and are recorded as `unchecked` (never reported as checked) | `test_analysis_digest_binding_and_legacy_runs` |
| 6(h) preflight gates | — | **not implemented** | listed in `unchecked`; no Tier 3 runner exists | — |
| Private keys outside agent processes | signer lived in the registry module | **no** | signing moved to tests only; registry refuses private key material | `test_private_key_material_is_refused` (2) |
| Stored approvals re-verifiable | yes | yes | re-verified on `check(trusted_keys)` | `test_tampered_recorded_approval_detected_by_check` (forged REJECTED→APPROVED with a valid hash chain) |
| `ledger-event.schema.json` (per-run sequence, `sha256:` prior-event digest, actor, event_type enum) | global M0 ledger, own fields | **no** | validated projection `export_v11_events()`. Storage unchanged to keep committed chains valid. M0 `verified`/`integrity_failed` have no v1.1 event_type and are listed as unmapped | `test_projection_onto_canonical_ledger_events` |
| Append-only, ordered, concurrency-safe ledger | yes (iteration-2 lock fix) | **yes** | none | `test_concurrent_processes_cannot_double_transition` |
| Subject-environment provenance, GPU type | M0 extensions | n/a (beyond spec) | kept | existing tests |

**Proposed smallest spec fix (not applied):** add `VERIFIED` and
`INTEGRITY_FAILED` to `ledger-event.schema.json` `event_type`. Integrity
verification is otherwise unrepresentable in the canonical ledger.

## Track B: what the engine decides where the fixtures are silent

No fixture is internally contradictory. A spec-faithful engine reproduces all 38
labels. These behaviours are engine choices (fail-closed), documented in the
`evaluation/track_b.py` header and pinned by tests:
`INPUT_INVALID` (missing, unknown or ill-typed keys, including misspellings),
`AUTHORITY_EPOCH_INVALID` (newer epoch), REVOKE / LEASE_EXPIRED while IDLE →
REFUSE, lost lease while ACTIVE under any event → TERMINATE, the future-skew
bound applied to the command clock too, no clock conversion is ever "proven",
and the first authorized+verified substitute wins. **Coverage gap found:** no
fixture exercises REVOKE while IDLE. A mutant that terminated there passed all
38 fixtures, and a property test now covers it.

## Tests

- Iteration-3 baseline (before edits): 488 passed, 4 failed, 1 xfailed, the same
  4 known failures.
- Final: see EVIDENCE_REPORT.md §A3.
- The 4 known failures are unchanged and not debugged further (3 R05 last-ULP
  drifts; the rollout capture misses its threshold by 3 µm,
  `results/diagnostics/`).

## CPU simulation status (Campaign 0A, unchanged)

The 488-pass baseline is kept. No new physics work. **Caveat recorded:** the
LIBERO-Dynamic zero policy returns 1-action chunks, so `sync_hold` pays the
fixed latency at **every** control step. A real chunked VLA (π0.5 config
`chunk_size: 50`, evaluated with `--replan-steps 10`) pays it once per chunk. So
the dummy-policy runs **overweight latency exposure by roughly the chunk length**
relative to a real policy, and cannot stand in for VLA timing behaviour.

## Remaining dependencies

**Tier 1 (CPU, no approval):** G1 incumbent specification (BASELINE_SPEC
requires one before any competitive claim). Numeric/statistical decision logic
(CPU_ASSIGNMENT item 6, "separate future work"). Optional adoption of the
proposed ledger `event_type` additions.

**Tier 2 (approval + accelerator):** GPU step 1, the one-episode π0.5 run
(`scripts/gpu/g1_one_episode.sh`; "g1" there is the GPU step, not the G1 incumbent), then the subset and full population, all on
one GPU type.

**Tier 3 (disabled):** sealed holdouts, protected runner, preflight gates
(6(h)), verification of the 8 payload digests M0 cannot check, production key
custody/revocation, external claims, physical robots.

## Next justified experiment

**G1 incumbent (BASELINE_SPEC "competent configured incumbent") specification on CPU (Tier 1).** Every Track B comparison so far
is against G0, which by construction cannot show anything about competent
practice. Specifying G1 from real software (for example ROS 2 controller-manager
lifecycle + `ros2_control` hardware-interface claiming + a configured watchdog)
and scoring it on the same 38 fixtures is the cheapest experiment that could
**reject** the hypothesis that Haplous's contract layer adds anything beyond a
well-configured incumbent. It needs no GPU. If a GPU becomes available, the
next step is GPU step 1 (the one-episode π0.5 run) to measure peak memory and confirm the
executed horizon.
