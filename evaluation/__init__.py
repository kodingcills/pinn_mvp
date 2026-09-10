"""Haplous Evaluation Engine: permanent claim/metric/taxonomy/statistics
infrastructure for large-scale evaluation campaigns.

Layering: evaluation/ depends on nothing inside haplous/ except public
machine data types when needed. Benchmark ownership of success
predicates is preserved (HEB protocol).
"""

from evaluation import stats, taxonomy
from evaluation.registry import load_claims, load_metrics

__all__ = ["stats", "taxonomy", "load_claims", "load_metrics"]
