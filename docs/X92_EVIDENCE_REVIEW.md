# INDEPENDENT EVIDENCE REVIEW (X92) — external benchmark campaign

**INDEPENDENCE STATUS: NOT ACHIEVED.** Five subagent review attempts
failed without output (inactivity stalls x4 with zero files written;
one harness model-config error). This review was therefore executed
mechanically by the orchestrator against the raw artifacts with the
checklist the independent reviewer would have received. The mechanical
checks below are tool-verified; independent adversarial judgment was
NOT obtained. Treat this as REVIEW PENDING for any independent
re-review; the verdict strength is limited accordingly.

Executed 2026-09-09, HEAD post-final-eval commit.

## Check results

1. **Baseline fairness** — PASS. In the FINAL run
   (20260909T213850Z), all 15 direct/strong seed pairs are identical in
   (peak_force_n, n_steps, n_contact_steps): 0 of 15 differ. The three
   systems share env, seed, policy, and OSC controller; only mediation
   differs. `strong_osc` is honestly labeled in the runner as the same
   loop with full telemetry.
2. **Metric validity** — PASS. The 25 N process limit appears
   identically in the mediated intent (`MAX_PROCESS_FORCE_N hard 25.0`,
   scripts/measure_external_robosuite.py:124) and the grader
   (`bounded_success=success and peak <= 25.0`, :89; `breach=peak>25`,
   :96). Peak force is measured from task telemetry
   (`task_result()["peak_force_n"]`, computed from ee_force
   observations), not inferred. The same 25 N value is the pegsim
   canonical f_yield, so the limit is not bespoke to this comparison.
3. **Held-out integrity** — PASS. Manifest/trial seeds are exactly dev
   0-4, qual 5-9, final 10-24; zero overlap across the three runs;
   final seeds were never used before the freeze tag
   `haplous-external-eval-v0`.
4. **Unsupported-claims scan** — one caution: the run summary table in
   docs/HAPLOUS_EXTERNAL_BENCHMARK_REPORT.md states explicitly that the
   0/15 policy failure is a policy weakness, not an incumbent-controller
   weakness, and that direct/strong are the same loop. Required reading
   intact. No claim found that overreaches the data.
5. **Framework leakage** — PASS. `git diff aa46c24^..HEAD -- haplous/`
   contains only `haplous/adapters/external.py` (+115) and
   `haplous/cli/*` (+596). Guarded core semantics modules: 0 changed.
   AST guard (tests/test_external_adapter.py:
   test_no_external_framework_imports_in_haplous_package) bans
   robosuite/isaaclab/pycontact/contactbench across all of haplous/.
6. **Sample adequacy** — ADEQUATE for the measured contrast
   (unmediated 404-489 N peaks vs mediated 25.8-32.6 N cap, 15 paired
   seeds; the contrast is ~16x and 15/15 vs 0/15). NOT adequate for
   rare-failure-rate estimation or for any task-success claim (0/15
   success in all arms on a policy that never acquired the nut).
7. **Negative-result handling** — PASS. G1 governor (BSR 11/22 -> 10/22,
   P95 +1.4 s), O2 observer (precision 0.056), and the 0/15 policy are
   recorded prominently in the report, STATE.md, and summaries.
8. **X41/X52 statistical honesty** — X41: 48/48 with per-variant
   medians; the report labels pyramidal-cone penetration (2.5 vs 24.7
   um) as solver-sensitive rather than hiding it. X52: the summary
   itself declares the GT limitation (phase labels + force threshold,
   "not independent contact truth") — circularity is acknowledged in
   the artifact, not just the report.

## Verdict

VERDICT: SUPPORTED WITH CAVEATS (mechanical audit only; independent
review PENDING).

Caveats (severity):
1. (blocker for claim strength, not validity) No independent reviewer
   judgment was obtained; infrastructure failures recorded (5x).
   Independent re-review required before any external use of this
   report's conclusions.
2. (concern) The robosuite comparison demonstrates constraint
   enforcement on identical command streams, NOT task success
   (0/15 all arms) and NOT superiority over a competing incumbent
   supervisor (none exists in robosuite).
3. (concern) Overhead cannot be read from wall-clock differences
   (mediation terminates trials earlier); per-tick overhead unmeasured
   this campaign.
4. (note) Empty run directories from smoke tests existed and were
   removed from the registry (never committed).
