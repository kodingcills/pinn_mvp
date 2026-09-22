# HES v0 final report

**Scope.** Haplous Evaluation System v0, built and exercised on the committed
v3 hardening qualification package. This report states what HES answers, what
the v0 campaign found, which findings weaken Haplous's registered claims, what
HES cannot yet decide, and the next experiments HES itself recommends.

**Reproduce.**

```bash
.venv/bin/python scripts/run_hes_v0_campaign.py         # migrate + analyze + gate + report
.venv/bin/python -m pytest tests/test_hes_*.py -q        # HES tests
PYTHONPATH=. .venv/bin/python -m hes report results/hes/v3_hardening_qualification \
    --out docs/HES_V0_REPORT.md --check                  # drift check
```

Evidence: `results/hes/v3_hardening_qualification/` (copied from
`results/v3_hardening_qualification/` byte-for-byte), analysis in
`hes_analysis.json`, gates in `hes_gates.json`, canonical generated report in
`docs/HES_V0_REPORT.md`.

## 1. What HES v0 is

A composition layer over the existing evidence stack, not a parallel system:

- it **reuses** `evaluation/` (claims/metrics registries, statistics, the
  resumable experiment lifecycle and its Parquet row contract) and
  `benchmarks/heb/` (scenario truth, arms, grading, counterfactual forks);
- it **adds** an independent-axis outcome model, an attainment-relative
  metric vector, hierarchical attribution, failure-Pareto ranking with an
  explicit responsibility decomposition, integrity checks, hard gates,
  metamorphic relations, boundary search, regression gates, versioned
  populations, a claim ledger, a recommender, and a digest-checked canonical
  report (`hes/`, 15 modules, ~2.3k LOC);
- it **never** modifies Haplous execution semantics, the locked test designs,
  or the historical results.

## 2. v0 campaign results (1000 scenarios, 3000 episodes, 3 arms)

| arm | bounded success | coverage | violation \| acceptance | execution capture | false refusal |
|---|---|---|---|---|---|
| `direct_minimal` (baseline) | 0.131 | 1.000 | 0.805 | 0.609 | 0.000 |
| `competent_supervisor` | 0.132 | 1.000 | 0.332 | 0.614 | 0.000 |
| `haplous_mediated` | 0.182 | 0.573 | 0.490 | **0.847** | 0.149 |

Adjusting for exposure changes the headline. Against the unmediated baseline
the runtime **more than halves** conditional violation (0.805 → 0.490) and
**captures 84.7%** of demonstrated-attainable execution, versus 60.9% for the
baseline. But it does so at **42.7% less coverage**, and only 5 of 23
attributable intervention forks were beneficial.

**Hard gates: the runtime does not qualify in v0.** G3 (non-interference)
fails: preservation of baseline bounded success is **0.786** against a 0.95
floor. G1, G2, G4, G5 pass.

## 3. Findings that weaken the registered claims

1. **C1 NON_INTERFERENCE — WEAKENED.** On scenarios the baseline already
   completes within contract, the runtime preserves only 78.6% of that bounded
   success and refuses 20.6% of them (unnecessary-intervention rate 0.206).
   This is the mechanism behind the G3 gate failure. This directly contradicts
   the claim's support condition ("per-pair identical or near-identical
   outcomes on streams that succeed without intervention").
2. **C2/C3 — WEAKENED.** The bounded-violation improvement is real and large,
   but it is **not** achieved "at comparable useful execution coverage". The
   runtime does not weakly dominate the baseline on
   (BSR, coverage, violation): it trades coverage away. C3's explicit
   weakening condition ("improvement in bounded success explained entirely by
   additional refusals of feasible work") is exactly the observed shape:
   coverage 0.573 with a 14.9% false-refusal rate.
3. **C11 INCUMBENT_DIFFERENTIATION — WEAKENED.** Against the competent
   supervisor, the runtime has lower preservation (0.786 vs 0.870) and lower
   conditional safety margin at higher capture; no differentiation is
   demonstrated on the frontier axes that matter here.
4. **Attainability is the dominant uncertainty.** Only **215/1000 (21.5%)**
   scenarios are demonstrated attainable; **785 are `UNKNOWN`**. HES refuses
   to call these non-attainable. Consequence: *every* attainment-relative
   result — capture, false refusal, "correct refusal" — is currently bounded
   by that 21.5%, and the 44.4% of runtime failures that are refusals cannot
   yet be split into productivity losses versus correct refusals. The v0
   campaign cannot yet answer whether the runtime refuses mostly feasible or
   mostly infeasible work.
5. **Intervention posture is net-destructive in the forked sample.** 12
   `HARMFUL` vs 5 `BENEFICIAL` vs 6 `UNNECESSARY`; 0 bounded successes
   recovered, 12 destroyed. (n=23, Wilson CI on harmful 0.33–0.71 — small but
   not negligible.)
6. **Reported semantic defect found and fixed.** Section 18 of the historical
   v3 report inverted `BENEFICIAL`/`HARMFUL` in prose (attaching
   "would have breached" to `HARMFUL`). The classifier was always correct;
   the prose was wrong. Fixed in `scripts/render_v3_hardening_report.py`; the
   inversion is now impossible to reintroduce without failing
   `tests/test_hes_intervention_semantics.py`.
7. **8 of 12 registered claims are not executable by HES v0** and are labeled
   `NOT_EXECUTABLE_IN_HES_V0` with the missing channel named (recovery,
   controller tournament, learned policy, boundary sweeps, cross-adapter
   diffs, loop latency, reliability fuzz/soak, cross-engine pairing). The
   registry's `SUPPORTED_WITHIN_TESTED_SCOPE` statuses for C4 and C9/C10 are
   *not contested* by HES — they rest on evidence HES did not read.

## 4. What HES cannot decide yet (honest limits)

**Benchmark health (from the grader's side).** All four HES benchmark-health
checks pass: the grader never reports bounded success together with a hard
violation, population balance is 2 task families × 3 offset strata over 1000
scenarios, and the scenario set is not dead — arms disagree on 123/1000
scenarios. But **786 of 1000 scenarios are unsolved by every arm**, which is
the same population that carries `UNKNOWN` attainability. The benchmark is
internally consistent; it is simply not yet resolving whether that large
unsolved mass is infeasible or un-captured. That is an instrument-coverage
limit, not a grader defect.

- **Attainability** for 78.5% of the population — needs counterfactual forks.
- **Recovery value** — the canonical row schema carries no recovery channel.
- **Real-time viability** — no loop-latency rows in canonical evidence.
- **Simulation relevance / generality** — single-engine, single-adapter by
  construction here.
- **Physical consequence beyond force/tilt** — integrated constraint
  excursion, collision count, and collision severity are `NOT_AVAILABLE` on
  the internal substrate and are reported as such.
- **Hardware** — no hardware leg exists; nothing in this report is
  hardware evidence.

## 5. HES-recommended next experiments (ranked)

1. **`counterfactual_sweep_on_unknown_attainability`** (priority 1.285) —
   fork a stratified sample of the 785 `UNKNOWN` scenarios. This is the one
   experiment that unlocks the largest amount of decision-relevant
   uncertainty: it converts capture/false-refusal from bounded-by-21.5% into
   population-level statements, and it can flip finding 4 either way.
2. **`harmful_intervention_root_cause`** (0.922) — replay the harmful forks
   with attribution. If the 12 destroyed successes are a fixable mediation
   heuristic, C1 and G3 recover; if they are structural, the runtime's
   mandate narrows.
3. **`refusal_cause_decomposition`** (0.614) — classify each refused
   scenario once, which is the direct input to whether C3's coverage trade is
   justified.

## 6. State of the evaluation system itself

- Full practical suite: **396 passed, 1 xfailed** (364 pre-existing + 32 HES:
  19 smoke/mechanism, 4 intervention-semantics inversion guards, 9
  campaign-integration). Note: `AGENTS.md` states 278; the code baseline is
  364 and `AGENTS.md` is stale.
- `ruff` clean on `hes/`, `scripts/`, `tests/`.
- HES does not change any Haplous behavior: `hes/` only reads committed
  evidence and the claims/metrics YAML; the one change to existing code is a
  prose correction in a report renderer.
- Determinism: the analysis is a pure function of the committed rows; the
  canonical report digest is stable across reruns.

## 7. Verdict

HES v0 is operational: it can reproduce the campaign, gate a candidate,
attribute failures, expose gaming, refuse to fabricate, and — the point of the
exercise — **find Haplous wrong**. On this evidence it does: the runtime's
bounded-violation advantage is real, but it is bought with coverage,
non-interference fails its own gate, and its intervention posture in the
forked sample is net-destructive. The registered non-interference and
net-value claims should be downgraded accordingly, and the single highest-value
next action is to remove the attainability uncertainty that currently bounds
every attainment-relative conclusion.