# Decision log: Haplous Generalized Execution Evaluation, iteration 1

## D1. Do not fabricate the missing v1.1 starter package

**Evidence.** No v1.1 package (schemas, 38 Track B contract fixtures, G0
definition, work orders, validation utilities) exists in this repository, in
any branch (`main`, `UI`, `claude/charming-feynman-y7k2ir`, this branch), or in
the session's upload/attachment directories (`/mnt/user-data/uploads`,
`/mnt/attach` are empty).

**Decision.** Track B fixture conformance and the G0 comparator are **BLOCKED**.
I did not write 38 substitute fixtures or a G0 definition. Doing so would invent
the specification the engine is supposed to be judged against, and any "38/38
pass" would be self-graded. The M0 schemas are labelled **provisional** for the
same reason.

**Rejected alternative.** Implement a contract engine from the bullet list in
the assignment and call it conformant. Rejected: precedence order, reason-code
vocabulary, lease/epoch semantics and G0's guard set are exactly what fixtures
pin down. Guessing them produces an engine that may need rewriting and evidence
that proves nothing.

## D2. M0 as one module + two JSON Schemas, not a service

`evaluation/run_registry.py` (filesystem, append-only JSONL ledger, `flock`).
**Why:** everything runs single-host on CPU now. A database or service would add
operational surface with no current consumer.
**Libraries (maintained, no custom crypto/canonicalization):** `jsonschema`
4.26 (Draft 2020-12 + `format-nongpl` checkers), `rfc8785` 0.1.4 (Trail of Bits
JCS), `cryptography` 50 (Ed25519).
**Not reused:** `evaluation/experiment.py`'s manifest is an HEB scenario-population
spec (sharding, systems, slide fraction). M0 records *any* run (CPU contract test,
policy-free sim, model-in-the-loop), so it sits beside it rather than replacing it.
**Existing frameworks considered:** MLflow / W&B / DVC track runs, but none
gives canonical-bytes manifests, a hash-chained ledger, or refused lifecycle
transitions out of the box. Adding one would mean wrapping it anyway.
`wandb` is already a LIBERO-Dynamic dependency, but only on the evaluator side.

## D3. Track-conditional manifests

`cpu_contract` **forbids** `simulator`/`policy` blocks. `sim_policy_free`
requires `simulator` and forbids `policy`. `sim_model_in_loop` requires both,
including a 64-hex `checkpoint_sha256`. **Why:** this makes it structurally
impossible to file a CPU contract test with made-up policy metadata, or a model
run without a hashed checkpoint.

## D4. Canonical output must re-read (found by a property test)

Hypothesis found that `float(2**53)` canonicalizes to `9007199254740992`. That
re-parses as an int and `rfc8785` then refuses it (I-JSON range), so the
registry could store a manifest it could never read back. Fix: `canonical_bytes`
re-reads its own output and refuses on failure. A regression test is included.

## D5. Provenance must not be polluted by the recorder

Runs `…-001`, `…-002`, `c0a-upstream-unit-tests-001` record `git_dirty: true`
because the registry wrote `results/m0_registry/` into the checkout before
reading `git status`. Fixed by excluding the registry root and running git in
this repository regardless of cwd (test included). The earlier manifests are
**left as recorded** (immutability). Run `…-003` was taken on a committed tree to
confirm the fix (see EVIDENCE_REPORT.md).

## D6. LIBERO-Dynamic CPU path: formal evaluator + OSMesa

The README preflight is vacuous (0 tasks, exit 0) and can't load the released
carriers. Upstream says to use `eval_dynamic_formal.py`, which runs on CPU with
`MUJOCO_GL=osmesa`. Videos were disabled to keep committed artifacts small.
That is a disclosed deviation from upstream's smoke command.

## D7. GPU choice driven by fixed-latency protocol, not by the paper's GPU

Upstream's formal protocol fixes simulated latency (`--latency-source fixed`),
and π0.5-PyTorch's value (0.147199 s) is published. So GPU speed changes
throughput, not the benchmark condition. Recommendation: one 24 GB L4/4090,
budget cap $5 for G1+G2. Prices could not be read from vendor pages (blocked).

## G0 honesty note (for when the package arrives)

G0 is a component-ablation reference, not a competent industrial incumbent.
Any Haplous-vs-G0 delta will be reported as "incremental behaviour relative to
minimal guards". It must not be presented as market or incumbent performance.

## Next experiment that most reduces uncertainty

1. **Obtain the v1.1 package.** It unblocks Track B and G0, and replaces the
   provisional schemas. No compute needed.
2. **G1 GPU smoke** (≤$5, approval needed): it measures the per-episode sim
   time on GPU, which is the dominant unknown in every cost estimate, and
   checks parity with our CPU smoke.

---

# Iteration 2

## D8. The package is still missing, so dependent work stays stopped

The assignment said the v1.1 package was at `evaluation/spec/haplous_eval_v1_1/`.
It is not in the tree, any remote branch, any PR or the upload directories
(ENGINEERING_STATUS.md lists every check). As instructed, I did not fabricate
fixtures, G0 or schemas.

## D9. Lock-held decisions instead of optimistic re-checks

The transition check ran on a ledger read outside `flock`, so two processes
could both observe `REGISTERED` and both append `started`. A barrier test with a
slowed ledger read reproduces this on the old code. The fix passes a `decide(history)`
callback that `_append` runs on the history it read under the lock. That is one
mechanism shared by transitions, rejections and approvals, instead of a second
re-check. Rejected: per-run lock files (more files, same guarantee on one host).

## D10. Replay key = digest of the signed body, not signature bytes

Ed25519 signatures are deterministic for a given key and message, but keying
replay on the body avoids relying on signature non-malleability.

## D11. GPU type is measured provenance, and required for model-in-the-loop

bf16 numerics vary by GPU type, so `provenance.gpu` comes from `nvidia-smi`
(never caller-supplied). The schema requires it to be non-empty for
`sim_model_in_loop`. `compare()` reports `hardware_comparable`.

## D12. Verify upstream's timing claim with two bounded runs, not reuse

All earlier runs used latency 0, so the fixed-latency mechanism had never been
exercised. Two 1-item runs (0.03 s, which is upstream's example, and
0.147199 s, which is not a whole number of 2 ms substeps) confirm exact,
unquantized holds. They also exposed that the zero policy emits 1-action chunks
(latency charged every step). That is why G1 must re-check the real K=10 case.

## D13. Checkpoint measurement from safetensors headers

Parameter count and weight bytes come from tensor headers (byte-range reads),
and hashes from HF LFS oids, with optional full-download re-hashing. This avoids
trusting model-card prose or secondary sources. GPU size follows a stated
heuristic, then is measured by G1's load check.

## Next experiment that most reduces uncertainty (unchanged order)

1. Supply the v1.1 package (unblocks the largest body of work, no compute).
2. Allow `huggingface.co`, run checkpoint step 0 (free).
3. G1 one episode (≤$5, approval).
