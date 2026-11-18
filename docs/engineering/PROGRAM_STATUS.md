# Program status (live)

Mandate: `HAPLOUS_AUTONOMOUS_PRODUCT_PROGRAM.md`. Design: `ARCH_NOTE_001_execution_boundary.md`.
Backlog: GitHub Issues. This file holds only the current state and the next action.

- Work branch: `claude/magical-curie-si1vjr`, restarted from `main` @ `a8541bf` (PR #8 merge).
- Nothing runs between sessions. Resume from this file and the open issues.
- Usage: `USAGE_NOT_MEASURED`.

## Issue graph

| Epic | Children (priority, status) |
|---|---|
| #9 Gate A Developer Alpha | #13 truth leak in `health()` (P0, fixed `03775a2`, verifier PASS; closes with the PR) · #14 runtime-only distribution (P1, integrated `8e675c0`, verifier PASS; closes with the PR) · #15 Studio job identity + artifact root (P1, in progress: E2E passed, suite pending) |
| #10 Gate B Generalized execution | #16 remove world stepping, envelopes, legacy adapter (P1, in progress) · #17 async lifecycle + lease (P1, blocked by #16) · #18 second command surface (P1, blocked by #16, #17) · #22 hold semantics undeclared (P1, blocked by #16; Gate C mechanism B) |
| #11 Gate C Differential value | #19 stale-hold force exceedance (P1): steps 1–2 done `1aefc64`, verifier PASS (root cause: open-loop hold during the hold, not resume); step 3 via #22 |
| #12 Gate D Evaluator engine | #21 diverged/auto-reset episodes graded as valid (P0, in progress) · #20 claim-to-experiment compiler (P1, queued; WIP limit) |

GitHub dependency links: the available GitHub integration cannot set native blocked-by links;
dependencies are stated in each issue body ("Blocked by"). Old issues #1–#5 closed as superseded with links.

## Baseline at `a8541bf`

944 passed, 4 failed, 1 skipped, 1 xfailed (983 s, Linux x86_64, Python 3.12.3, MuJoCo 3.11.0).
Caveat: the #13 edit landed mid-run; the result equals the M6 record. The 4 failures are pre-existing and
do not touch product user paths: 3 `test_r05` (locked R05 evidence fingerprints differ by a last-digit
float on this platform; G5 filter check) and `test_rollout` capture (0.503 mm vs 0.5 mm threshold).
Classification: VALIDATED LIMITATION (research-substrate tests), not product blockers.

## Next actions

1. Resume after the 2026-11-17 usage limit: #16, #21, #15 owners resumed via SendMessage (work was saved in their worktrees). On their reports: independent verify, integrate, comment on the issue.
2. At integration of #16: migrate `examples/stub_machine_contract.py` off `ServoCommand`/`session.step()`.
2. After #16: #17 then #18; #22 (hold semantics) and the amended E19 run (#19 step 3).
3. #15 after #14; #20 when WIP allows.
