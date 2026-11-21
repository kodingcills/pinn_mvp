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

## Direction (operator review, 2026-11-21)

E20 is archived as a confounded comparison: it found no trustworthy effort advantage either way.
Reusable G1 (`benchmarks/g1lib/`) is the permanent competitive baseline. The evaluator is enough to
improve the runtime; no more evaluator expansion unless a concrete runtime change needs it. Planned
effort split: about 65% runtime and integration, 25% external controller or developer validation,
10% evaluator. No native rewrite, general OEM framework or broad simulation campaign. Customer
discovery runs in parallel (human-led).

Milestone **Runtime Integration Readiness** (issues under #10):
#27 truthful observations and stop semantics (P0) · #28 command validity at receipt time (P0) ·
#29 restart reconciliation, never resume authority (P1). Each follows the AGENTS.md
runtime-semantics workflow: design in the issue, parity, verifier PASS.

## Next actions

1. M0: PR #26 made mergeable (main recorded as an ancestor; its tree equals `764a8fe`). Exact-head
   full suite and HEB parity `results/b7/` (results recorded below once finished).
2. #27, then #28, then #29.
3. A small URSim interface-characterization spike using the official UR client library or ROS 2
   driver. Read-only first: telemetry, program status, controller version, timestamps, reconnect.
   Then ack and authority behaviour. No custom protocol driver.
4. Gate E: only the essential cold-reader path.
5. Deferred: Gate D counterexample search, Studio Atlas, plots.
