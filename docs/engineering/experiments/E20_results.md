# E20 results: incremental integration effort, Haplous vs a reusable G1

Protocol: `E20_preregistration.md` (frozen `f95b49b`). Feasibility: `E20_feasibility.md`.
Known harness ambiguities (recorded before results): `E20_known_ambiguities.md`.
Combined independent verifier: PASS on acceptance equivalence, effort-ledger integrity, isolation
and G phase-1 parity. Arms merged on the branch as `5578d4a` (H) and `1ade524` (G).

## Mechanical decision (section 5)

**R = 170.6 s / 34.7 s = 4.92 → rule 3: "the reusable G1 approach beats Haplous on marginal effort."**

**This decision is not reliable, and it should not be quoted without the caveats below.** The
verifier reached the same judgement independently.

## Measurements

| | H (Haplous) | G (reusable G1) |
|---|---|---|
| Acceptance | 15/15 on the first run (reproduced by the verifier) | 15/15 on the first run (reproduced) |
| One-time phase | none (existing runtime) | 842 s: `benchmarks/g1lib/` extracted; G1 HEB traces byte-identical on all 27 scenarios |
| **Marginal phase, net of acceptance runtime (primary)** | **170.6 s** | **34.7 s** |
| Marginal code | 191-line arm + 17 additive core lines (`JointVelocityTarget`, `CommandMode.JOINT_VELOCITY`) | 117-line arm |
| Acceptance runs before 15/15 | 1 | 1 |
| Whole builder session (context only) | 164k tokens, 39 tool calls, 26 min | 179k tokens, 44 tool calls, 31 min |

## Why the mechanical result is unreliable

1. **Confounded phase split.** The G builder read the frozen driver and acceptance suite before
   designing the library, and placed the E20-driven generalisations in the untimed phase 1:
   asynchronous ack validity, late-ack and duplicate handling, the authority guard and the hold
   hook. By line count that is roughly 55–75 of 278 library lines. The verifier's estimate:
   booking ≥ ~80 s of phase 1 to phase 2 removes rule 3; booking ≥ ~220 s gives R ≤ 0.67
   (rule 1). The ledger has no sub-marks, so the true split cannot be measured.
2. **Measurement floor.** G's 34.7 s is roughly one model turn plus tool latency; neither arm
   needed a second acceptance run. Agent seconds at this scale do not resolve engineering effort.
3. **One builder per arm**, no interval.
4. **Unverifiable reading time.** H reported about 10 minutes of reading before its start mark;
   the ledger cannot confirm this or show that reading was treated symmetrically.

Context totals including G's one-time phase: G about 877 s vs H about 171 s. Section 5 makes
these context only.

## What this does establish

- Both a competent reusable supervisor and Haplous let an agent integrate a new robot model and
  command surface with **all 15 execution-safety acceptance cases** passing on the first attempt,
  in minutes. Neither shows a large integration-cost advantage here.
- Haplous needed only additive core changes (a new payload type and mode) and reused its existing
  admission, ack ledger, lease, hold and evidence machinery unchanged.
- A reusable G1 library (`benchmarks/g1lib/`) now exists with byte-identical G1 behaviour. Future
  comparisons get a fair, productized incumbent.

## Interpretation for the company hypothesis

Read as "**no material difference demonstrated, with a possible Haplous gap**", not as a G1 win and
not as support for T03. The decisive uncertainty stands: there is no evidence yet that competent
supervision becomes meaningfully cheaper because Haplous exists. A follow-up that could discriminate
needs (a) builders who have not seen the target driver before their library phase, or no library
phase at all with both foundations frozen beforehand; (b) several integrations or builders per arm
for an interval; and (c) a task harder than one adapter file, so that effort rises above the
measurement floor. Any commercial reading still requires review by a real robotics developer.
