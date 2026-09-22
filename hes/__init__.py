"""Haplous Evaluation System (HES) v0.

HES is the evaluation operating system around Haplous: it answers what the
runtime currently claims, which experiment tests each claim, what evidence
supports or weakens it, and where the runtime helps, hurts, refuses, or
merely fails to capture attainable execution.

HES deliberately composes the existing evidence stack instead of
duplicating it:

- ``haplous/``      execution semantics (never modified by HES)
- ``benchmarks/heb/``  scenario truth, simulation, grading, counterfactuals
- ``evaluation/``   claims/metrics registries, statistics, taxonomy, and the
                    resumable experiment lifecycle
- ``hes/``          outcome model, runtime metrics, attribution, integrity,
                    regression, boundary search, reporting, and the CLI

The privileged-information boundary is unchanged: HES reads benchmark-owned
ground truth for grading and analysis; Haplous never does.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]