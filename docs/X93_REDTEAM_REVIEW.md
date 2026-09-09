# RED-TEAM REVIEW (X93) — external benchmark campaign

**INDEPENDENCE STATUS: NOT ACHIEVED.** Subagent review attempts failed
repeatedly without output (infrastructure). This is an
orchestrator-executed adversarial pass over its own work, which is
structurally weaker than an independent red team. Attacks applied
mechanically with tool evidence; creative adversarial independence was
NOT obtained. Results here cannot certify the campaign; they document
the strongest surviving objections.

Executed 2026-09-09, HEAD post-final-eval commit.

## Attack outcomes

1. **FAKE SOTA COMPARISON** — SURVIVES AS OBJECTION. `strong_osc` is the
   same scripted loop as `direct_minimal` with full telemetry (verified:
   0 of 15 seed pairs differ physically). Labeling this a comparison
   against a "strong incumbent controller" would be fake SOTA; the
   report's framing ("the incumbent is the OSC controller itself; no
   competing incumbent supervisor exists in robosuite") is the
   defensible form and must be preserved verbatim in any external use.
2. **WEAK BASELINE** — SURVIVES AS OBJECTION. The policy never
   acquired the nut (0/15 all arms). The measured value is constraint
   enforcement, not task success. Mitigation present: same policy in
   all arms, 25 N limit symmetric (grader :89 vs intent :124, same
   constant as pegsim f_yield) — but no evidence exists in this
   campaign that Haplous preserves or improves task success on an
   external task where the incumbent succeeds.
3. **CHERRY-PICKED TASKS** — PARTIALLY SURVIVES. NutAssemblySquare is
   the only external env evaluated; TwoArmPegInHole was never attempted.
   Isaac REJECTED on platform grounds is honest (darwin, no NVIDIA GPU,
   evidence in results/x10_isaac_disposition.json) but also convenient:
   the one independent-backend comparison that exists (robosuite) still
   runs MuJoCo underneath, so "independent physics backend" is NOT
   demonstrated. N01 solver sensitivity was probed within MuJoCo
   variants + a Drake microprobe only.
4. **SIMULATOR ARTIFACT** — SURVIVES AS OBJECTION (bounded). X41's
   "capture/force/settling survive numerics" holds within MuJoCo
   formulations; pyramidal cone flipped penetration 10x (2.5 vs 24.7 um)
   and the report labels penetration solver-sensitive. The Drake probe
   (sphere-plane vs cylinder-hole) is not apples-to-apples and only
   documents cross-formulation magnitude. Claim "survives across
   contact numerics" is valid; any broader "solver-invariance" claim
   would be overreach and is not made.
5. **GOVERNOR GAMING** — MIXED. G1 was a minimal attenuation-only
   wrapper tested on HEB cells where G0 is largely stable; a negative
   result there does NOT disprove G01 in general (it fails to support
   it in tested cells). Conversely the design was not rigged to pass.
   Honest reading: G1-in-this-form does not earn promotion; G01 remains
   open.
6. **OBSERVER GT CIRCULARITY** — SURVIVES. X52 ground truth is phase
   labels + a force threshold; O0's precision/recall partly measures
   the threshold definition. The artifact acknowledges this and no
   contact-truth claims are made. Cross-dataset behavior claims are
   correspondingly weak (derivative data, 3-5 episodes).
7. **METRIC GAMING (preregistration timing)** — SURVIVES AS
   LIMITATION. PREREGISTER.md was committed after the dev campaign ran
   (before qual/final). The seeds 10-24, metric definitions, and the
   G1 promotion rule were genuinely pre-final; but dev results
   (including 0/15 policy and the 25 N choice) predate the document.
   What later preregistering cannot fix: dev-informed limit choice and
   dev-informed task framing.
8. **HIDDEN BURDEN** — SURVIVES. benchmarks/ is not packaged in the
   wheel (pyproject wheel packages = pegsim/haplous/*), so
   `haplous list` advertises benchmarks that wheel-only users cannot
   run, and `haplous run` fails with an unhandled ModuleNotFoundError
   (clean-room evidence, results/x80_cleanroom_report.md). h5py/pyarrow
   were added to core dependencies for the realdata track (small but
   real install-surface growth).
9. **OVER-CONSERVATIVE ABORT** — SURVIVES. Haplous aborted 15/15 and
   completed nothing on the external task; the campaign contains no
   scenario demonstrating Haplous succeeding where the incumbent also
   succeeds (the internal HEB held-out does show 0.844 BSR with bare
   at 0.789, which is the counter-evidence — but it is internal).
10. **TECHNICAL MAXIMALISM** — the honest existence case after this
    campaign: Haplous demonstrated (a) zero-core-change integration
    across an external framework + governor + real-data tracks, and
    (b) hard-constraint enforcement on identical command streams
    (404-489 N -> capped 25.8-32.6 N, 15/15 terminations at declared
    bounds). It did NOT demonstrate task-success value, superiority
    over a competing incumbent supervisor, cross-engine physics
    independence, or governor value.

## Strongest single objection

The campaign's only external, held-out comparison (robosuite FINAL)
measures constraint enforcement on a task where no arm ever succeeds.
As evidence of an "existence case" it supports "Haplous enforces
declared bounds without core leakage" — nothing stronger.

## Reclassification demanded

None invalidated (no methodology break found in the mechanical pass).
All task-success-adjacent claims must remain absent from any external
use; the force-cap contrast may be cited only with the policy-failure
disclosure attached.
