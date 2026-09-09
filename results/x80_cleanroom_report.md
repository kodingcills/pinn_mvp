# CLEANROOM AGENT DEVELOPER PROXY (not real customer evidence)

Node X80. Executed 2026-09-09 against a fresh venv
(`uv venv` + `pip install <repo>`), wheel-only, with only public docs
(README.md, CONTEXT.md, docs/, examples/) in the workspace.

**Independence deviation, disclosed**: the campaign spec calls for an
isolated agent. Three subagent attempts stalled without any output
(deep bg_20036ed7 31m, deep retry bg_1eb08104 31m, general bg_14a9f34b
failed to start); the orchestrator then executed the scripted protocol
directly against the SAME isolated environment (fresh venv install,
no repo source opened beyond the provided doc copies). The environment
and commands are unchanged; agent-level independence was not achieved.
Attempted-core-modifications: 0 (no source access).

## Step results

1. `haplous doctor` — WORKS. mujoco 3.13.0 OK, robosuite/isaaclab/rerun/
   ContactBench NOT INSTALLED, datasets MISSING, result directory MISSING
   (points inside site-packages — a wheel user gets a site-packages path,
   which is actionable but reveals repo-layout assumptions). Exit 0.
2. `haplous list` — WORKS but ADVERTISES benchmarks/tasks/backends that
   the wheel does not ship (benchmarks/ is not in the wheel packages).
   Misleading for a wheel-only user.
3. `haplous run internal-insertion --system haplous_mediated` — FAILS
   with an UNHANDLED traceback:
   `ModuleNotFoundError: No module named 'benchmarks'` from
   haplous/cli/benchmarks.py inside site-packages. Not a graceful
   message; `list` set the user up for this failure.
4. `examples/heb_quickstart.py` with the venv python — **WORKS**:
   feasibility SUPPORTED, outcome COMPLETE. The core runtime path (intent
   -> negotiation -> live execution -> evidence) functions from a
   wheel-only install. Note: the quickstart docstring says it "requires
   the repository checkout", which is inaccurate for this path (pegsim is
   packaged) — a doc-accuracy gap in the favorable direction.
5. Adding a new task from docs alone: the quickstart demonstrates the
   behavior-registration pattern (behaviors={name: factory}) but no doc
   describes adding a benchmark to the CLI registry from outside the
   repo (the registry API exists in haplous.cli.benchmarks but is
   undocumented in the workspace docs). GAP.

## Counts

- Steps attempted: 5/5. Steps successful: 2 (doctor, quickstart).
- Failures: 1 hard (run: traceback), 1 misleading (list advertises
  unshipped benchmarks).
- Errors verbatim: see step 3.
- Documentation gaps: 2 (benchmark extension undocumented; doctor path
  assumptions).
- Attempted core modifications: 0 (no source access).

## Verdict

Wheel-only install: core runtime + quickstart genuinely work;
CLI benchmark surface is broken for wheel users (list/run mismatch).
The in-repo experience works because benchmarks/ exists in the checkout.
Fixes needed: package benchmarks/ (or hide unshipped entries from `list`),
graceful error message for missing benchmark modules, and a documented
extension path.
