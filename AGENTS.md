# AGENTS.md

Repository-level guidance for coding agents working on Haplous / pegsim.

## Project

Research substrate for peg-in-hole insertion plus the Haplous physical-execution
runtime. Physics-first controller + geometric state estimator; no RL is trained.
Canonical state summary lives in `CONTEXT.md` (verify it against code; on
conflict, the code wins). Deferred/hardware notes live in `PHASE2.md`.
Durable engineering evidence lives in `docs/` (index: `docs/README.md`).

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
.venv/bin/python -m pytest tests -q          # full suite (expect 396 passed, 1 xfailed)
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
- Behavior-freeze: do not alter capability behavior, refusal, hold, revalidation,
  resume, recovery, constraints, evidence semantics, or state transitions without
  a locked design and tests proving parity.
- Generated evidence belongs in `results/` (JSON/JSONL) with a driver in `scripts/`.
- Do not commit virtualenvs, caches, editor config, or agent-tool config.
  Exception: `.claude/skills/` and `.claude/agents/` vendor the pstack plugin
  (MIT) for cloud sessions; see `.claude/skills/PSTACK_VENDORED.md`.
