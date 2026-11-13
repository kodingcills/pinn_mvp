# Haplous Eval — autonomous engineering program v2

This is a **hand-off to a coding agent**, not implemented code and not measured physics results.

## Files

- `HAPLOUS_EVAL_PROGRAM_v2.md` — detailed source-grounded architecture, four milestone contracts (M2–M5), component ownership, technical defaults, safety/evidence gates, and the Definition of Done.
- `CLAUDE_ORCHESTRATOR_LAUNCH.md` — standalone executable Claude Code instructions. It works even if the detailed spec is not uploaded, but supplying both improves fidelity.

## How to launch

1. Use a **fresh** Claude Code cloud session or a new local Claude Code session after pulling current `main` in `kodingcills/pinn_mvp`. PR #6 was already merged and the old feature branch was deleted at the time of writing.
2. To avoid the previous missing-attachment blocker, upload/extract these Markdown files into the working repo under `docs/eval_program/` (on a feature branch) OR attach them to the Claude session and confirm that its environment sees their actual contents. Do not assume a ChatGPT sandbox link transfers automatically into another agent's filesystem.
3. Invoke `/poteto-mode` if the vendored slash skill loads in the new session. Otherwise paste `CLAUDE_ORCHESTRATOR_LAUNCH.md` directly: it explicitly tells Claude which vendored pstack playbooks to read and how to adapt the Cursor-specific commands.
4. Instruct the hosted agent to commit evidence and status as it runs. Cloud sessions may stop at execution budgets; long-running agent orchestration is only continuous while the host actually supports it.
5. Review source diffs, runnable replays, test output, and independent verification receipts. Do not treat an agent summary or a successful ZIP import as completion.

## Scope

Build a CPU-evaluable, two-task, model-in-the-loop-compatible execution evaluator with historical 3D replay and independent evidence, reusing existing `haplous/`, `pegsim/`, `benchmarks/heb/`, `evaluation/`, `hes/`, and M0. No CUDA, paid cloud provisioning, Tier 3 sealed data, real robot execution, or automatic merging to main.
