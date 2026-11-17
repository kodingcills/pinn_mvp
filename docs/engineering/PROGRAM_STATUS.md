# Program status (live)

Mandate: `HAPLOUS_AUTONOMOUS_PRODUCT_PROGRAM.md`. Design: `ARCH_NOTE_001_execution_boundary.md`.
Backlog: GitHub Issues. This file holds only the current state and the next action.

- Work branch: `claude/magical-curie-si1vjr`, restarted from `main` @ `a8541bf` (PR #8 merge).
- Nothing runs between sessions. Resume from this file and the open issues.
- Usage: `USAGE_NOT_MEASURED`.

## Issue graph

| Epic | Children (priority, status) |
|---|---|
| #9 Gate A Developer Alpha | #13 truth leak in `health()` (P0, fixed on branch, verifying) · #14 runtime-only distribution (P1, in progress) · #15 Studio job identity + artifact root (P1, blocked by #14) |
| #10 Gate B Generalized execution | #16 remove world stepping, envelopes, legacy adapter (P1, in progress) · #17 async lifecycle + lease (P1, blocked by #16) · #18 second command surface (P1, blocked by #16, #17) |
| #11 Gate C Differential value | #19 stale-hold force exceedance (P1, queued; WIP limit) |
| #12 Gate D Evaluator engine | #20 claim-to-experiment compiler (P1, queued; WIP limit) |

GitHub dependency links: the available GitHub integration cannot set native blocked-by links;
dependencies are stated in each issue body ("Blocked by"). Old issues #1–#5 closed as superseded with links.

## Baseline at `a8541bf`

Pending (full suite running). Expected from the M6 record: 944 passed, 4 failed (3 `test_r05`, 1
`test_rollout`), 1 skipped, 1 xfailed.

## Next actions

1. Independent verification of #13 at its commit.
2. #14 owner and #16 owner working in parallel worktrees (disjoint files).
3. After #16 lands: #17, then #18; start #19 steps 1–2 when WIP allows.
