from collections import Counter

from benchmarks.stale_resume_population import CELLS, SEED, N, e19_population
from benchmarks.stress_population import stress_population
from hes.populations import POPULATIONS


def _windows(s):
    return s.stale_windows_s + s.command_rejection_windows_s + s.dropout_windows_s


def test_deterministic():
    assert e19_population() == e19_population()
    assert e19_population(10) == e19_population()[:10]


def test_disjoint_from_m4_stress():
    m4 = POPULATIONS["m4_stress"]
    assert SEED not in {p.seed for p in POPULATIONS.values()}
    frozen = m4.generate()
    pop = e19_population()
    assert not {s.id for s in pop} & {s.id for s in frozen}
    frozen_windows = {w for s in frozen for w in _windows(s)}
    assert not {w for s in pop for w in _windows(s)} & frozen_windows
    assert stress_population(m4.n, m4.seed) == frozen  # the frozen population is untouched


def test_full_factorial_insertion_only():
    pop = e19_population()
    assert len({s.id for s in pop}) == N
    tags = Counter(tuple(t for t in s.tags if t.split(":")[0] in {"fault", "phase", "duration_ms"}) for s in pop)
    assert len(tags) == len(CELLS) and set(tags.values()) == {N // len(CELLS)}
    for s in pop:
        assert s.task_family == "peg_insertion" and s.max_force_n == 25.0 and s.state_max_age_s == 0.02
        assert len(_windows(s)) == 1
