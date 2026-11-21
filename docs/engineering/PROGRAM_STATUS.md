# Program status (live)

Mandate: `HAPLOUS_AUTONOMOUS_PRODUCT_PROGRAM.md`. Design: `ARCH_NOTE_001_execution_boundary.md`.
Backlog: GitHub Issues. This file holds only the current state and the next action.

- Work branch: `claude/magical-curie-si1vjr`, restarted from `main` @ `a8541bf` (PR #8 merge).
- Nothing runs between sessions. Resume from this file and the open issues.
- Usage: about $379 / 725M tokens / 4 h agent time by 2026-11-21 (operator's session meter);
  driven mostly by per-issue owner + verifier subagents.
- Cost rule (operator, 2026-11-21): one combined independent review per gate, not per issue;
  small fixes done directly by the coordinator without an agent; no long simulation re-runs
  unless the code under test changed since the last run.
- Commit attribution (operator, 2026-11-19): author and committer `kodingcills`; no Claude
  co-author or session trailers. Branch commits since `a8541bf` were rewritten to match
  (backup ref `backup/pre-author-rewrite` local only); SHAs quoted in issue comments before
  2026-11-19 refer to the pre-rewrite commits.

## Issue graph

| Epic | Children (priority, status) |
|---|---|
| #9 Gate A Developer Alpha | **Gate A acceptance PASS at `9457ddc`** (fresh-clone journey). #13 truth leak (P0, done) · #14 runtime-only distribution (done; mujoco pinned <3.12 in `d860f89`) · #15 job identity + artifact root (done) · #23 corrupt run listed verified / cached replay (done) |
| #10 Gate B Generalized execution | **Gate B acceptance PASS at `658e01c`** (fresh clone; injected delayed ack, stale obs, authority conflict). |
| |  #16 world stepping out of core, envelopes, legacy adapter (done, verifier PASS, integrated `51adcb2..6a4de75`) · #17 async lifecycle + lease (done, verifier PASS with injected delayed ack / stale obs / authority conflict, `e6665b9..76063e8`) · #22 hold semantics (closed not planned: built, measured, REVERT; code kept at `73976f3` via merge `809564b`, runtime unchanged) · #18 second command surface (done, verifier PASS, `cbdc6a5..658e01c`; robosuite JointPositionController) · #24 machine fault during hold misreported (P0, done, verifier PASS, `6067f07..73db19d`) |
| #11 Gate C Differential value | **Gate C criterion met (negative):** E19 decision REVERT, NO INCREMENTAL VALUE also true (verifier PASS at `73976f3`, evidence `b3d5779`). #19 closed. M4 stale-hold exceedances = simulator open-loop-hold artifact. Next hypothesis awaiting operator direction (integration-effort vs async-controller comparison) |
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

1. Open a PR from this branch to `main` for review (do not merge without operator approval).
2. Gate C next hypothesis: proposed on #11 (integration effort; async-controller semantics vs a fair G1).
3. Gate D remaining: counterexample minimisation / boundary search, plots, Studio Performance Atlas and issue-from-replay.
4. Gate E: integration beta and cold-reader handoff; then `PRODUCT_RELEASE_READOUT.md`.
5. Done: HEB parity at HEAD, `results/b6/parity.json`, 108/108 identical vs `76063e8` (closes the Gate B b4 caveat).
6. E20 (Gate C, integration effort vs reusable G1): feasibility PASS (`E20_feasibility.md`), pre-registration frozen at `f95b49b`; arms H and G started 2026-11-21T16:31Z in isolated worktrees; then one combined verifier.
