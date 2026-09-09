# Ground-truth audit

Read-only archaeology against the repository at `81117a9`. Every status below is
sourced from a commit hash, a file line, a test name, or a committed result
value. No claim is carried over from conversational memory.

Audit date: 2026-09-08. Auditor performed no implementation, no physics
campaigns, and no sweeps. The full test suite was executed exactly once; a
second `pytest --collect-only` (collection, not execution) was used for the
per-file breakdown.

**Worktree note.** The worktree was byte-identical at the end of the audit
except for this one file, which is the ordered deliverable. `git status --short`
was empty before the audit and lists only `docs/AUDIT_GROUND_TRUTH.md` after it.
No tracked file was modified.

---

## A1 — Commit provenance

Current HEAD: `81117a9e334376d9bd78c02b150b83c8020ab38b`
Current branch: `main`
`git status --short`: **empty output** (clean, no untracked files).
Branches: `main` only. `git branch -a` also reports `remote-only (1): UI` — this
is a remote-tracking entry, not a local branch; nothing is checked out from it.
`git stash list`: **empty**.
`main` contains 48 commits (`git rev-list --count HEAD`).

### Verdict per claimed hash

| Claimed | Exists | Reachable from HEAD | Subject | Verdict |
|---|---|---|---|---|
| `312345f` | yes (object) | **NO** | Lock validated control configuration | **orphaned** — see below |
| `9d60fa4` | yes (object) | **NO** | Preserve canonical diagnostic drivers | **orphaned** — see below |
| `407d9ce` | yes | yes | Add settled-state trial telemetry | confirmed |
| `3ad18a0` | yes | yes | Add N2 radial probe measurement | confirmed |
| `7081111` | yes | yes | docs(n2): record probe characterization | confirmed |
| `36243b2` | yes | yes | feat(n4): add facet convergence driver | confirmed |
| `8422b72` | yes | yes | chore(n4): record facet sweep trials | confirmed |
| `e917fdc` | yes | yes | feat(n4): decompose estimator error | confirmed |
| `ef92b1c` | yes | yes | docs(n4): record convergence evidence | confirmed |
| `27129c5` | yes | yes | docs: correct canonical project context | confirmed |
| `f02d904` | yes | yes | fix(n4): clarify figure provenance | confirmed |
| `cf64dcb` | yes | yes | fix(n4): gate claims on complete sweeps | confirmed |
| `60c557c` | yes | yes | docs(n4): refresh guarded evidence | confirmed |
| `2c8ea3a` | yes | yes | docs: protect integrator configuration | confirmed |
| `c64dc55` | yes | yes | docs(n4): publish convergence report | confirmed |
| `ed4f40f` | yes | yes | feat(filter): add gated state estimator | confirmed |
| `8590893` | yes | yes | docs(filter): report F1a discrimination failure | confirmed |
| `7ddb18c` | yes | yes | feat(filter): add pair batching and abort | confirmed |
| `81117a9` | yes | yes | docs(filter): report F1a rev2 | confirmed (= HEAD) |

### The Chunk 1 range is wrong

The claimed Chunk 1 boundary `312345f -> 9d60fa4` names two commit objects that
exist in the object database but are **not reachable from any ref**:

```
git merge-base --is-ancestor 312345f HEAD   -> false
git branch -a --contains 312345f            -> (no output)
```

Both are pre-rewrite predecessors of the commits that are actually on `main`:

| Orphan | Tree | On-main equivalent | Tree |
|---|---|---|---|
| `312345f` | `f07e774…` | `8c1db45` "Lock validated control configuration" | `0de6ec3…` |
| `9d60fa4` | `6ec88e3…` | `6ed42e5` "Preserve canonical diagnostic drivers" | `6060581…` |

Trees differ, so these are not identical-content duplicates — they are earlier
versions that were amended or rebased away. **The canonical Chunk 1 range on
`main` is `8c1db45 -> 6ed42e5`.** Any future work order citing
`312345f -> 9d60fa4` is citing content that no branch carries.

One further orphan exists, unrelated to the claim: `git fsck` reports
`dangling commit dbeda701`, a predecessor of `af35ee7` "Document Chunk 1
engineering decisions". It contains no N5, filter, or safety work.

### N5a

No commit is expected and **none exists**. Confirmed in A3 below.

---

## A2 — Test suite ground truth

Command: `.venv/bin/python -m pytest -q -rA`, run once.

```
140 passed, 1 xfailed in 35.24s
```

Wall time: `real 35.46` (via `/usr/bin/time -p`, includes interpreter startup).

**This matches the last claimed state exactly (140 passed, 1 xfailed).** No
difference to account for.

The single xfail:

```
XFAIL tests/test_filter.py::test_clearance_step_separates_from_constant_fixture_position
  - SUPERSEDED: Q asymmetry separates slow from fast, not step from step
```

Collected total is 141 (`pytest --collect-only -q`), consistent with 140 + 1:

| File | Collected |
|---|---|
| `tests/test_rollout.py` | 36 |
| `tests/test_scene.py` | 35 |
| `tests/test_filter.py` | 21 |
| `tests/test_n4_facet_convergence.py` | 16 |
| `tests/test_control.py` | 13 |
| `tests/test_search.py` | 12 |
| `tests/test_characterize.py` | 5 |
| `tests/test_n2_probe.py` | 3 |

`docs/F1_STATE_FILTER_REPORT.md:412` claims "Final full suite after report
self-review: **140 passed, 1 xfailed**" — **verified true**.

---

## 1. Status table (A3–A6)

| # | Item | Status | Evidence | Prior belief vs. found |
|---|---|---|---|---|
| **A3** | | | | |
| 3.1 | N2 summary artifact | **DONE** | `results/n2_probe_summary.json`, 14,909 B, added at `7081111`; `n_trials=128`, `n_success=128` | **Filename in the work order is wrong.** No `results/n2_probe_primitive_summary.json` exists; the real name is `results/n2_probe_summary.json` |
| 3.2 | N2 numbers vs. docs | **DONE** | Recomputed `clearance_m − tip_radius_m.min` = 7.3868941313180985 µm vs. `CONTEXT.md:137` "7.3869 µm"; `estimator_error_norm_m` mean/p95/max = 0.02345 / 0.04121 / 0.04199 mm vs. `CONTEXT.md:139` — exact | matches |
| 3.3 | N4 summary artifact | **DONE** | `results/n4_facet_convergence_summary.json`, 47,118 B, added at `ef92b1c`, revised at `60c557c` | matches |
| 3.4 | N4 spot-check | **DONE** | `by_n["64"].penetration_m_from_pooled_minimum` = `2.9630093418717097e-06`; **byte-for-byte the claimed value**. Cross-checked against `docs/N4_FACET_CONVERGENCE_REPORT.md:127` ("2.963 µm") and `:282` | matches |
| 3.5 | **N5a preflight report** | **NOT STARTED** | No `docs/N5_*` file exists, on any branch, in any commit. `git log --all --diff-filter=A --name-only \| grep -i n5` → empty | **Believed: "N5a preflight ran, report exists." FALSE — even N5a produced no committed artifact** |
| 3.6 | **N5b (640-trial antipodal sweep)** | **NOT STARTED** | No `results/n5_*`; `find . -iname '*n5*'` (excluding `.git`/`.venv`) → empty; no commit touches `pegsim/scene.py` or `pegsim/rollout.py` after `407d9ce` | **Believed: possibly run. FALSE — never ran, anywhere** |
| 3.7 | **N5c (half-facet control)** | **NOT STARTED** | Same evidence as 3.6 | never ran |
| 3.8 | N3 (64-trial natural-search negative) | **NOT STARTED** | `CONTEXT.md:285` "N3 must still measure the 64-trial natural-search negative"; no `n3_*` artifact or script exists | not in scope of the work order, but it is an unmet precondition — see §6 |
| **A4** | | | | |
| 4.1 | `pegsim/filter.py` exists | **DONE** | 18,099 B, added `ed4f40f`, extended `7ddb18c` | matches |
| 4.2 | Pair batching, `H_pair` rank 3 | **DONE** | `pegsim/filter.py:207-216` `pair_observation_matrix` returns 4×3; `pegsim/filter.py:261` `update_pair`. Asserted by `tests/test_filter.py:448` `test_pair_observation_is_bearing_independent`: `assert np.linalg.matrix_rank(h_pair) == 3` and `assert np.linalg.cond(h_pair) == pytest.approx(1.0)` at 0/30/45/90° | matches |
| 4.3 | Per-probe fallback retained | **DONE** | `pegsim/filter.py:232` `update()` survives `7ddb18c`; docstring at `update_pair` (`filter.py:267-271`): "an incomplete pair must use :meth:`update`" | matches |
| 4.4 | Joseph-form covariance | **DONE** | `pegsim/filter.py:388-395`: `joseph_left = np.eye(3) - kalman_gain @ h_matrix` then `joseph_left @ p_pred @ joseph_left.T + K R Kᵀ`; symmetrized at `:396`; PD guard at `:403`. Test `test_joseph_covariance_stays_symmetric_and_positive_definite` (`tests/test_filter.py:376`) | matches |
| 4.5 | Innovation gate before update | **DONE** | `pegsim/filter.py:342` `if normalized_innovation_squared > self._gate_threshold:` returns before the Kalman gain is computed at `:384`. Threshold `CHI2_2DOF_999` = 13.816 (`results/f1a_filter_summary.json` `config.gate_threshold_chi_square_2dof_99_9_percent`) | matches |
| 4.6 | 3-consecutive-gate abort | **DONE** | `pegsim/filter.py:25` `DEFAULT_CONSECUTIVE_GATE_LIMIT: Final[int] = 3`; increment `:343`; abort test `:352` `if self._consecutive_gate_count >= self._consecutive_gate_limit`; counter reset on clean update `:406` and post-abort `:369` | matches — and see §3, the abort is **not** off-by-one |
| 4.7 | **`pegsim/error_injection.py` (F1b)** | **NOT STARTED** | File absent from worktree and from every commit on every ref (`git log --all --diff-filter=A --name-only \| grep -i error_inject` → empty). None of the four channels is implemented: `grep -ril 'encoder.quant\|tcp.bias\|part.variation\|ovality'` hits **only** `docs/F1_STATE_FILTER_REPORT.md`, i.e. prose, not code. Consequently nothing was run against T1/T7 | matches the belief that F1b is not started |
| 4.8 | **F1c wiring** | **NOT STARTED** | `StateFilter` is imported by exactly two files: `tests/test_filter.py:8` and `scripts/measure_f1a_state_filter.py:12`. That script imports no `mujoco` and never calls `run_episode` (`grep -n 'mujoco\|run_episode' scripts/measure_f1a_state_filter.py` → no hits). `tests/test_filter.py:93` `test_filter_module_has_no_mujoco_import` enforces the separation. **Nothing partial exists, so the `mj_resetData` state-leak concern is untouched** — the only reset site is `pegsim/rollout.py:452` inside episode construction | matches |
| **A5** | | | | |
| 5.1 | **Eigenvalue extraction from `mj_fullM`** | **NOT STARTED** | `mujoco.mj_fullM` is called at `pegsim/control.py:103` (`derived_damping`) and `:125` (`stability_cap`). **Both take only the diagonal**: `m_diag = full_m[dof_ids, dof_ids]`. No `eigvals`/`eigh` is applied to a mass matrix anywhere: the only `eigvalsh` calls in the repo are `pegsim/filter.py:76`, `tests/test_filter.py:392`, `scripts/measure_f1a_state_filter.py:580` — all on filter covariances | **Believed: "discussed but never executed." CONFIRMED never executed.** See §4 |
| 5.2 | Rotational-mode frequency (`omega_n`) result file | **ABSENT** | No results file reports a rotational-mode frequency. `omega_n` appears once, at `pegsim/control.py:118`, and only inside a docstring deriving the per-DOF cap `K ≤ (2/dt)²·M` | never measured; the ~135 Hz estimate remains unverified |
| 5.3 | **MJCF joint damping still zero** | **CONFIRMED ZERO** | `pegsim/scene.py:324-329`: all six joints emit `damping="0" frictionloss="0" armature="0"`. Locked by `tests/test_scene.py:213` `assert model.dof_damping[jid] == 0.0`. **The L3 Colgate-Brown premise (b = 0) holds unchanged.** Caution: `derived_damping` in `control.py` is rendered controller damping (B_rendered), a different quantity — do not read it as joint `b` | matches |
| **A6** | | | | |
| 6.1 | `pegsim/safety.py` / watchdog | **ABSENT** | File does not exist and never did. `CONTEXT.md:39-44` states it plainly: "`pegsim/safety.py` **does not exist.** Milestone 5 was specified and never built." Nearest thing is a single assertion, `pegsim/control.py:229` (10° tilt bound). No force limit, timeout, or abort guard in `rollout.py` (`grep -i 'max_force\|force_limit\|abort\|timeout\|emergency' pegsim/rollout.py` → no hits) | matches |
| 6.2 | Touch-off routine | **ABSENT** | `CONTEXT.md:263-265`: "`z_flat` is currently seeded from CAD nominal. Real hardware must establish that datum by contact… On the sim-to-real critical path." Code path is `seed_z_flat_from_cad: bool = True` (`scripts/measure_l1_attribution.py:23`); no contact-based alternative exists | matches |
| 6.3 | Probe cycle as runtime feature | **ABSENT** | No probe code in `pegsim/`. `grep -i probe pegsim/*.py` (excluding `filter.py`) yields one comment, `rollout.py:485`. Probing exists only in the two one-off drivers `scripts/measure_n2_probe_primitive.py` and `scripts/measure_n4_facet_convergence.py` | matches |
| 6.4 | Viewer / recording via `mujoco.Renderer` | **PARTIAL, non-functional** | `UI/SimPort.py:34` uses `mujoco.Renderer(self.model, height=height, width=width)` — correct API. Tracked on `main` at `cea3962`. **But it does not run:** `UI/SimPort.py:32` is `mujoco.MjModel.from_xml_path(pegsim)`, passing the *module object* as a path (guaranteed `TypeError`); it imports `PIL`, which is not in `pyproject.toml` dependencies; and it is a Tkinter widget, so not headless. `UI/UI.py:128` still passes the placeholder `"path/to/your_model.xml"`. No test covers `UI/` | **Believed: nothing exists. Partly false — a `Renderer` path exists but is broken.** `launch_passive` confirmed absent repo-wide, as `CONTEXT.md:270-274` requires |
| 6.5 | Search-only baseline config for paired demo | **PARTIAL** | Only one `ValidatedConfig` exists: `VALIDATED_LOOSE_V1` at `pegsim/rollout.py:838`. It *is* search-only with no estimator (nothing imports `StateFilter` into `rollout.py`), so it functions as the de facto baseline — but it is not labelled as a demo baseline, and **there is no metrology-enabled counterpart to pair it against**, so the comparison does not exist | matches in substance |
| 6.6 | Tight-clearance TaskSpec | **ABSENT** | `pegsim/rollout.py:838-850` defines exactly one config: `radial_clearance=0.0005` (500 µm). `grep -n 'ValidatedConfig(' pegsim/rollout.py` returns one line | matches |
| 6.7 | SPC chart / control limits | **ABSENT** | Only two hits repo-wide, both scoping it *out*: `docs/F1_STATE_FILTER_REPORT.md:9` and `:380`. No stub, no plotting code | matches |

---

## 2. Corrections to prior belief

Ordered most-important first.

### 2.1 N5 never happened — not N5b, not N5c, and not even N5a

This is the correction that already propagated into a downstream work order once,
so it is stated with the full search that establishes it.

- No file whose name contains `n5` exists in the worktree:
  `find . -path ./.git -prune -o -path ./.venv -prune -o -iname '*n5*' -print` → **empty**.
- No such file was ever added, on any ref, in any commit:
  `git log --all --pretty=format: --name-only --diff-filter=A | sort -u` — the
  complete list of every file ever added to this repository contains no `n5`
  entry, no `antipodal` entry, and no N5-specific script.
- `docs/N5_ANTIPODAL_CANCELLATION_REPORT.md` **does not exist and never did**.
- No commit after `407d9ce` touches `pegsim/scene.py` or `pegsim/rollout.py` at
  all, so no N5 physics change was made even transiently.
- Nothing is hidden: `git stash list` is empty, there is one branch, and the
  only orphaned objects are `312345f`, `9d60fa4` (Chunk 1 predecessors) and
  `dbeda701` (a Chunk 1 docs predecessor) — none contains N5 work.
- Nothing is hidden in ignored paths either: the only gitignored non-tooling
  content is `data/contact_model_audit_force_profiles.png` and
  `data/step1_drop.npz`.

The string "N5" survives in exactly one place in the tree —
`docs/N4_FACET_CONVERGENCE_REPORT.md`, as forward-looking recommendation prose.
"antipodal" appears in `pegsim/filter.py`, `docs/N4_FACET_CONVERGENCE_REPORT.md`,
and `docs/F1_STATE_FILTER_REPORT.md`, always as design discussion — the antipodal
*pair batching* in the filter is F1a-rev2 work operating on synthetic
measurements, and must not be mistaken for the N5 physics sweep.

**Verdict: N5a's preflight report is not "the only N5 artifact that exists."
There is no N5 artifact of any kind. Prediction C (pair-mean error flat across
dc) is untested.**

### 2.2 The claimed Chunk 1 commit range points at unreachable objects

`312345f -> 9d60fa4` are orphans. The equivalent commits on `main` are
`8c1db45 -> 6ed42e5`, and their trees differ from the orphans', so the orphans
are superseded content rather than aliases. Every prior citation of the
`312345f -> 9d60fa4` range was citing history no branch carries. Corrected range
for future work orders: **`8c1db45 -> 6ed42e5`**.

### 2.3 The N2 summary filename in the work order does not exist

Claimed: `results/n2_probe_primitive_summary.json`. Actual:
**`results/n2_probe_summary.json`** (added at `7081111`, 14,909 B). The *script*
is `measure_n2_probe_primitive.py`; the *result* drops "primitive".

### 2.4 A `mujoco.Renderer` path already exists, and it is broken

Prior belief treated the viewer as wholly absent. `UI/SimPort.py` is tracked on
`main` (`cea3962`) and does use the correct `mujoco.Renderer` API. It cannot
run as written — three independent defects listed in row 6.4. This is a
correction in both directions: something exists, and it does not work. The
positive half of the belief (`launch_passive` is absent) is confirmed.

### 2.5 CONTEXT.md is four commits stale and omits the entire F-series

Detailed in §5. It is stale by **omission**, not by false assertion — which is
the more dangerous failure mode here, because a next agent reading it will not
learn that `pegsim/filter.py` exists at all.

### 2.6 Everything else claimed about N2, N4, and F1a-rev2 verified true

Stated for completeness, since an audit that only reports errors is misleading:
17 of the 19 claimed hashes are correct and on `main`; the N4 spot-check value
matches to the last digit; the N2 numbers in `CONTEXT.md` reproduce exactly from
the committed JSON; the test count matches exactly; and every one of the five
claimed F1a-rev2 filter features is present and test-covered.

---

## 3. T10 resolution

**Verdict: spec-correct. There is no off-by-one.** The abort fires on the
**third consecutive gate**, which happens to be the **fourth pair** because the
first post-ramp pair updates cleanly and therefore is not a gate at all.

The test does not assert a pair index. Its binding assertions are on the gate
count (`tests/test_filter.py:511-516`):

```python
    assert result.outcome is FilterOutcome.ABORT
    assert post_ramp_pair < 5
    assert result.consecutive_gate_count == state_filter.consecutive_gate_limit
    assert len(result.gated_innovation_history_m) == (
        state_filter.consecutive_gate_limit
    )
```

`post_ramp_pair < 5` is a loose liveness bound (the loop runs `range(5)`); it
pins nothing about *which* pair. The two assertions that carry meaning both
compare against `consecutive_gate_limit`, which is 3
(`pegsim/filter.py:25`, `DEFAULT_CONSECUTIVE_GATE_LIMIT: Final[int] = 3`).

The filter satisfies them by construction. `pegsim/filter.py:343` increments the
counter on a gate, and `:352` aborts the moment it reaches the limit:

```python
            self._consecutive_gate_count += 1
            ...
            if self._consecutive_gate_count >= self._consecutive_gate_limit:
```

So the record emitted with `outcome=ABORT` carries `consecutive_gate_count == 3`
exactly — the third gate, not a fourth. A clean update resets the counter to 0
(`:406`), so a clean pair cannot contribute to the sequence.

The committed measurement closes it arithmetically.
`results/f1a_filter_summary.json`, key `t10_repeated_gate_abort`:

```
abort_pair_count             = 4
abort_consecutive_gate_count = 3
```

Four pairs consumed, three of them gated ⟹ exactly one clean update, and it was
the first. That is precisely the "pair 1 clean update, gates on pairs 2/3/4"
reading — and that reading **is** the three-consecutive-gates spec, not a
violation of it.

`docs/F1_STATE_FILTER_REPORT.md:218-219` describes the same thing correctly:
"T10's 2 µm/pair fixture ramp emitted `ABORT` on pair 4. Its history contained
exactly three innovations". The report's own summary table at `:330-331` lists
"`ABORT` | fourth pair after ramp start" alongside "Consecutive gates at abort |
3". The prose is accurate; the ambiguity was conversational, not in the artifact.

**One documentation nit, not a defect:** `docs/F1_STATE_FILTER_REPORT.md:354`
compresses this to "`ABORT` on fourth pair", which invites exactly the
off-by-one misreading this audit was asked to settle. Worth rewording to "on the
third consecutive gate (the fourth pair after ramp start)".

---

## 4. `mj_fullM` status

**Verdict: never executed. The chat record was right — no eigenvalue extraction,
no measured `omega_n`, no comparison against the ~135 Hz estimate.**

`mujoco.mj_fullM` *is* called, twice, which is the trap:

- `pegsim/control.py:103`, inside `derived_damping`
- `pegsim/control.py:125`, inside `stability_cap`

Both immediately discard everything but the diagonal:

```python
    m_diag = full_m[dof_ids, dof_ids]
```

`derived_damping` returns `2·sqrt(K·M_ii)`; `stability_cap` returns
`(2/dt)²·M_ii`. Neither performs a decomposition, and no rotational-mode
frequency is computed or recorded anywhere. The only `eigvalsh` calls in the
repository operate on 3×3 filter covariances
(`pegsim/filter.py:76`, `tests/test_filter.py:392`,
`scripts/measure_f1a_state_filter.py:580`) and have nothing to do with the mass
matrix.

The code itself already flags the gap it leaves. `pegsim/control.py:143-150`:

> "NECESSARY, NOT SUFFICIENT: this treats each DOF as decoupled, but x/ry and
> y/rx are coupled through `mj_fullM`'s off-diagonal terms… only empirical
> sweeping down to `K_rx=K_ry=1` gave a version that actually held."

That off-diagonal coupling is exactly what an eigen-analysis of `mj_fullM` would
quantify, and exactly what has not been done. No results file reports a
rotational-mode frequency; `omega_n` appears once in the whole repository, in a
`stability_cap` docstring at `control.py:118`, as symbolic derivation rather than
measurement.

The premise underneath the L3 diagnosis is intact and re-verified:
`pegsim/scene.py:324-329` sets `damping="0"` on all six joints, and
`tests/test_scene.py:213` locks it (`assert model.dof_damping[jid] == 0.0`).
**b = 0 unchanged**, so the Colgate-Brown condition `b > K·T/2 + B_rendered` at
500 Hz is still violated by construction.

---

## 5. CONTEXT.md / PHASE2.md discrepancy list

Neither document contains a section headed "current status". Both are structured
as standing reference plus open-items lists. **Critically, neither document
falsely claims that N5 or F1b is done or in progress — the word "N5" does not
appear in either file, and neither mentions F1b.** The failure mode is omission,
which is worse for the stated purpose: a next agent opening `CONTEXT.md` will not
discover that the F-series exists.

### CONTEXT.md — last touched `2c8ea3a` (2026-08-09), four commits behind HEAD

Every F-series commit (`ed4f40f`, `8590893`, `7ddb18c`, `81117a9`, all
2026-08-16) landed after `CONTEXT.md` was last edited.

| Line | Quoted text | Why it is now false or misleading |
|---|---|---|
| 22-33 | The `## Repo structure` table lists `pegsim/scene.py`, `control.py`, `search.py`, `rollout.py`, `characterize.py` — **and stops** | **`pegsim/filter.py` is missing from the table.** It is an 18 KB production module added at `ed4f40f` and extended at `7ddb18c`, with 21 tests. The canonical structure table denies its existence |
| — | No mention anywhere of F1a, F1a-rev2, `StateFilter`, the Kalman filter, gating, or the `ABORT` outcome (`grep -i 'F1a\|StateFilter\|kalman' CONTEXT.md` → no hits) | The single largest body of work in the last four commits is invisible in the canonical context file |
| 120-144 | `## Measured numbers` table ends at the N4 rows | Omits every F1a-rev2 measurement, including the T10 abort behaviour and the NEES consistency result, all of which are committed in `results/f1a_filter_summary.json` |
| 285 | "N3 must still measure the 64-trial natural-search negative before deliberate probing is declared mandatory" | **Still true and still unmet** — flagged here because it is an unsatisfied precondition that later work orders have skipped past |
| 221 | Section heading "Open and unresolved — do not chase unless told" | The list is accurate for what it contains but has no N5 entry, no F1b entry, and no F1c entry, so the reader cannot tell these were ever planned |
| 39-44 | "`pegsim/safety.py` **does not exist.** Milestone 5 was specified and never built." | **Verified correct.** Noted so it is not "corrected" by mistake |
| 137, 139 | N2 penetration 7.3869 µm; estimator error mean 0.02345 / p95 0.04121 / max 0.04199 mm | **Verified correct** — reproduced exactly from `results/n2_probe_summary.json` |
| 270-274 | "Use `mujoco.Renderer(m, h, w)` … not `launch_passive`" | Guidance is correct and `launch_passive` is absent repo-wide — but the doc does not record that `UI/SimPort.py` already attempts this and is broken (row 6.4) |

### PHASE2.md — last touched `f9edc10` (2026-08-08), one commit before the N2 docs

| Line | Quoted text | Why it is now false or misleading |
|---|---|---|
| — | `grep -i 'F1a\|F1b\|F1c\|filter\|kalman\|estimator\|N2\|N4\|N5' PHASE2.md` → **no hits** | The file predates the entire N-series and F-series. It documents only Chunk 1 / Milestone 4 physics findings |
| 156-158 | "Fixed: `pegsim.rollout.DELTA_Z_TRIGGER` (0.2 mm) + a live-tracked `z_flat` … independent of `f_search`/`k_z`" | `pegsim/rollout.py:47` now defines `DEPTH_COMMIT = 0.0005` (0.5 mm) and `:65` `Z_FLAT_BAND = 0.5 * DEPTH_COMMIT`. **No symbol named `DELTA_Z_TRIGGER` exists in `pegsim/rollout.py`.** This is precisely the stale-constant failure mode `CONTEXT.md:148` calls the project's #1 bug source |
| 3, 34, 43, 53 | Section headings "RESOLVED for nominal operation", "re-measure once Stage B's config lands", "still deferred", "a live finding, not settled" | Statuses are pinned to Milestone-4-era config. None has been revisited since `f9edc10`; treat every status word in this file as un-refreshed for a month |

### docs/README.md — current, and the one doc that is right

`docs/README.md` was updated at `8590893` and states:

> "F1b/F1c are stopped pending a process-model decision."

**Accurate.** Minor gap only: it was not re-touched at `81117a9`, so it names the
F1a report without mentioning the rev-2 revision. It is the most trustworthy
status surface in the repository right now — more so than `CONTEXT.md`.

---

## 6. Next viable work order

### Preconditions satisfied — startable now

| Item | Why unblocked | Note |
|---|---|---|
| **F1b — error injection** | Needs nothing. `pegsim/filter.py` is complete and test-covered; T1 and T7 exist and pass (`tests/test_filter.py:107`, `:399`). The four channels operate on synthetic measurements, so no physics campaign is required | Highest value-per-risk item on the board. Create `pegsim/error_injection.py`; the spec prose already lives in `docs/F1_STATE_FILTER_REPORT.md` |
| **L3 — `mj_fullM` eigen-analysis** | Needs nothing. `mj_fullM` is already retrieved at `pegsim/control.py:103` and `:125`; the work is to keep the full matrix instead of slicing the diagonal, and take `eigvalsh`. `b = 0` is confirmed unchanged, so the diagnosis premise stands | Cheap: no rollout, one model build at a representative configuration. Settles the ~135 Hz estimate that has been asserted but never measured |
| **CONTEXT.md refresh** | Needs nothing but this audit | Should land **before** the next feature work order, since every work order opens by reading it. Minimum: add `pegsim/filter.py` to the structure table, add an F-series section, add F1a-rev2 measurements, and record that N5 has never run |
| **PHASE2.md `DELTA_Z_TRIGGER` correction** | Needs nothing | One-line stale-constant fix at `PHASE2.md:156` |
| **N5b / N5c** | Needs nothing — this is the correction that matters. It was believed done; it has never started. `pegsim/scene.py` and `pegsim/rollout.py` are untouched since `407d9ce`, and N4's infrastructure (`scripts/measure_n4_facet_convergence.py`) is a working template for a facet-phase sweep | This is a real physics campaign (640 trials) and should be scheduled deliberately, not assumed complete |
| **6.6 tight-clearance TaskSpec** | Needs nothing. `TaskSpec` (`pegsim/scene.py:17`) is already parameterized on `radial_clearance`; a second `ValidatedConfig` beside `pegsim/rollout.py:838` is a config addition | But re-validation trials would be required before the config could be called "validated" |
| **6.4 viewer repair** | Needs nothing. Three concrete defects in `UI/SimPort.py` (module-as-path at `:32`, undeclared `PIL` dependency, Tkinter/headless) | Small, and unblocks the recorded-demo deliverable |

### Blocked

| Item | Blocked on | Why |
|---|---|---|
| **F1c — filter/physics wiring** | **N5b** | F1c conditions the filter on real `run_episode` output. N5b is the ground-truth check that the antipodal pair-mean error is actually flat across `dc` (prediction C). Without it, F1c would wire a filter whose core observability assumption is unverified against physics. `docs/F1_STATE_FILTER_REPORT.md:9` independently calls F1b/F1c "stopped pending a process-model decision" |
| F1c `mj_resetData` state-leak handling | F1c | Nothing partial exists to inspect. The only reset site is `pegsim/rollout.py:452`; the cross-episode leak question cannot be answered until something actually spans an episode boundary |
| **N3** (64-trial natural-search negative) | nothing technically — but it gates the *claim* | `CONTEXT.md:285` makes N3 the precondition for declaring deliberate probing mandatory rather than merely validated. Any work order asserting that probing is required is blocked on N3 |
| **6.3 probe cycle as runtime feature** | N3, and arguably N5b | Promoting probing from one-off script to production feature presupposes the N3 negative result that justifies it |
| **6.5 paired demo comparison** | F1c | The baseline half exists (`VALIDATED_LOOSE_V1`); the metrology-enabled half is exactly what F1c would build. No pairing is possible until then |
| **6.7 SPC chart** | F1b + F1c | An SPC chart needs a stream of deviation measurements from real cycles. Neither the injected-error source nor the physics-wired filter exists |
| **6.1 safety watchdog / 6.2 touch-off** | nothing in this repo — deferred by decision | `CONTEXT.md:259-265` places both on the hardware path. Not blocked, but deliberately out of scope for simulation-only work; do not treat as available quick wins |

### Recommended order

1. Refresh `CONTEXT.md` (and the one-line `PHASE2.md` fix) — cheapest, and it
   stops the stale-premise propagation this audit exists to catch.
2. **L3 `mj_fullM` eigen-analysis** — no campaign, settles a month-old open
   question, and directly informs the control-rate remediation blocking sim-to-real.
3. **F1b error injection** — no campaign, no blockers, and it is the last F-series
   step that does not require N5b.
4. **N5b / N5c** — the real physics campaign, and the gate for F1c.
5. F1c, then the demo layer (6.5, 6.7).
