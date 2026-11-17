# AGENTS.md

Repository-level guidance for coding agents working on Haplous / pegsim.

## Project

Research substrate for peg-in-hole insertion plus the Haplous physical-execution
runtime. Physics-first controller + geometric state estimator; no RL is trained.
Canonical state summary lives in `CONTEXT.md` (verify it against code; on
conflict, the code wins). Deferred/hardware notes live in `PHASE2.md`.
Durable engineering evidence lives in `docs/` (index: `docs/README.md`).
Current mandate: `docs/engineering/HAPLOUS_AUTONOMOUS_PRODUCT_PROGRAM.md`. Live status and
next action: `docs/engineering/PROGRAM_STATUS.md`; the action backlog is GitHub Issues
(epics #9-#12). Work branches start from the latest `main`. `docs/eval_program/` is the
historical research record (M2-M6), kept unchanged.

## Layout

| Path | Contents |
|---|---|
| `pegsim/` | MuJoCo simulation substrate (scene, control, rollout, filter, ...) |
| `haplous/` | Execution runtime: capabilities, refusal, hold, revalidation, recovery, evidence |
| `benchmarks/` | HEB benchmark, governor candidate, external/real-data adapters |
| `evaluation/` | Metrics, taxonomy, statistics, locked test designs |
| `scripts/` | Reproducible measurement drivers |
| `tests/` | Full test suite |
| `results/` | Committed raw JSONL trials and derived evidence |
| `docs/` | Engineering reports and campaign evidence |
| `hes/` | Haplous Evaluation System: outcome axes, metric vector, attribution, gates, reports |

## Commands

```bash
.venv/bin/python -m pytest tests -q          # full suite (see docs/eval_program/KNOWN_ISSUES.md for the current baseline)
.venv/bin/python -m pytest tests/test_haplous_semantics.py -q   # runtime semantics
.venv/bin/python -c "import haplous, pegsim" # import smoke
ruff check .                                  # lint, if ruff is installed
```

Run the full practical suite before declaring work complete. Do not introduce
new failures.

## Boundaries

- `pegsim/filter.py` and `pegsim/error_injection.py` are deliberately MuJoCo-free;
  an AST test enforces this.
- Locked test designs (`evaluation/locked-tests/R05-v*.yaml`) are immutable;
  results produced under them must not be pooled across versions.
- Runtime-semantics changes (capability, refusal, hold, revalidation, resume,
  recovery, constraints, evidence, state transitions) are allowed under the product
  program only through this workflow: a short design in the GitHub issue (types,
  migration, parity checks), parity tests proving unchanged behaviour where
  behaviour should not change, a new scoring epoch plus impact note where scores
  can change, and an independent verifier PASS at the exact commit. Sealed
  historical evidence is never rewritten. (Supersedes the earlier behaviour-freeze
  rule, 2026-11-17.)
- Product runtime (`haplous/`) never imports HES, benchmark ground truth or MuJoCo;
  adapters may import their own SDKs.
- Generated evidence belongs in `results/` (JSON/JSONL) with a driver in `scripts/`.
- Do not commit virtualenvs, caches, editor config, or agent-tool config.
  Exception: `.claude/skills/` and `.claude/agents/` vendor the pstack plugin
  (MIT) for cloud sessions; see `.claude/skills/PSTACK_VENDORED.md`.
