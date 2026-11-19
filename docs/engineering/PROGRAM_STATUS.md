# Program status (live)

Mandate: `HAPLOUS_AUTONOMOUS_PRODUCT_PROGRAM.md`. Design: `ARCH_NOTE_001_execution_boundary.md`.
Backlog: GitHub Issues. This file holds only the current state and the next action.

- Work branch: `claude/magical-curie-si1vjr`, restarted from `main` @ `a8541bf` (PR #8 merge).
- Nothing runs between sessions. Resume from this file and the open issues.
- Usage: `USAGE_NOT_MEASURED`.
- Commit attribution (operator, 2026-11-19): author and committer `kodingcills`; no Claude
  co-author or session trailers. Branch commits since `a8541bf` were rewritten to match
  (backup ref `backup/pre-author-rewrite` local only); SHAs quoted in issue comments before
  2026-11-19 refer to the pre-rewrite commits.

## Issue graph

| Epic | Children (priority, status) |
|---|---|
| #9 Gate A Developer Alpha | **Gate A acceptance PASS at `9457ddc`** (fresh-clone journey). #13 truth leak (P0, done) · #14 runtime-only distribution (done; mujoco pinned <3.12 in `d860f89`) · #15 job identity + artifact root (done) · #23 corrupt run listed verified / cached replay (done) |
| #10 Gate B Generalized execution | #16 world stepping out of core, envelopes, legacy adapter (done, verifier PASS, integrated `51adcb2..6a4de75`) · #17 async lifecycle + lease (done, verifier PASS with injected delayed ack / stale obs / authority conflict, `e6665b9..76063e8`) · #22 hold semantics (P1, next after #17; Gate C mechanism B) · #18 second command surface (P1, in progress) · #24 machine fault during hold misreported (P0, in progress on #17 head) |
| #11 Gate C Differential value | #19 stale-hold exceedance: diagnosis done (root cause: open-loop hold during the hold); E19 protocol amended `cf70a70`, run after #22 |
| #12 Gate D Evaluator engine | #21 sim divergence graded valid (P0, done; M4 addendum, C1/C2 still ties) · #20 claim-to-experiment compiler (done, verifier PASS, `bd29b4d`; DEV: hold 0/8 stale actuation Haplous = G1, bare 8/8; admission 9/9 = G1, bare 3/9) |

"Done" = verifier PASS and integrated on the branch; issues close when the PR merges to `main`.

GitHub dependency links: the available GitHub integration cannot set native blocked-by links;
dependencies are stated in each issue body ("Blocked by"). Old issues #1–#5 closed as superseded with links.

## Baseline at `a8541bf`

944 passed, 4 failed, 1 skipped, 1 xfailed (983 s, Linux x86_64, Python 3.12.3, MuJoCo 3.11.0).
Caveat: the #13 edit landed mid-run; the result equals the M6 record. The 4 failures are pre-existing and
do not touch product user paths: 3 `test_r05` (locked R05 evidence fingerprints differ by a last-digit
float on this platform; G5 filter check) and `test_rollout` capture (0.503 mm vs 0.5 mm threshold).
Classification: VALIDATED LIMITATION (research-substrate tests), not product blockers.

## Next actions

1. #24 and #18 owners working; on reports: independent verify, integrate, issue comment.
2. After #24: #22 hold semantics, then the amended E19 run (#19 step 3) with an instantiated `g1_parity` arm; #18 second command surface.
3. Open the PR to `main` (do not merge without operator approval) once Gate B lands, or earlier if the operator asks.
4. Full suite on `6a4de75`: 975 passed, 4 failed (the known 4), 1 skipped, 1 xfailed (708 s).
