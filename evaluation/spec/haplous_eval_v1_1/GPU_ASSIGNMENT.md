# Claude Assignment B — GPU: Campaign 0A stages 0–1

Execute on a machine with verified suitable GPU resources, in a **separate session and environment** from the CPU assignment. No Haplous mechanism or agentic Tier 3 approval is required to reproduce baseline physics.

1. Pin a specific commit of https://github.com/renshaojie233/LIBERO-Dynamic. Record the actual source SHA, Python/requirements hash, GPU model and VRAM, checkpoint identity/license/size and simulator versions. If no compatible GPU/checkpoint exists, output `GPU_BLOCKED` with exact resource shortfall; still run any feasible CPU-side smoke checks.
2. Read the pinned official README, preflight script, dynamic dummy-policy script and formal evaluation notes. Use its **native** Python environment; do not merge Python 3.10 dependencies into vla-eval 0.9 as part of this job.
3. Run official policy-free preflight and a documented smoke test. Independently verify that configured simulated inference latency advances simulated time, moving-carrier state and object response; distinguish simulated latency from wall-clock time. No aggregate task-success comparison until smoke and timing semantics pass.
4. Produce raw logs, per-step timing evidence, exact reproduction commands, stage-0/1 manifest with true digests, and `VALID` / `BLOCKED_WITH_REASON`. Do not report benchmark metrics copied from the source publication as observed results.
5. Report whether a next-stage π0.5 PyTorch baseline reproduction is computationally feasible, including a bounded cost estimate grounded in observed pilot throughput, and any benchmark-specific blockers.

**Acceptance:** reproducible smoke and valid timing behavior on the pinned source, with actual logs and provenance. Stages 2–3 require a new scoped task and a competent incumbent tuning protocol. No sealed holdout, new physics, control runtime or generalized VLA server is implemented here.
