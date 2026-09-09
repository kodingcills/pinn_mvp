"""Haplous v0 runtime: physical intent -> requirements -> machine capability
-> feasibility -> execution -> state/constraint monitoring ->
recover/degrade/refuse/abort/complete -> execution evidence.

The core package is machine-agnostic and simulator-free: it imports no
simulator and no research substrate (AST-enforced by
tests/test_haplous_structure.py). Simulators sit behind the Machine protocol
in the adapters subpackage; application knowledge lives in the behaviors
subpackage.
"""
