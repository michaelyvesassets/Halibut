"""Real-world evidence layer: source catalogue, provenance, bitemporal store.

This package is independent of the simulation engine. The engine never
imports it; analyses connect the two only through explicit measurement models
and calibration targets. Industry-specific knowledge lives in catalogue and
event *data files*, not in code.
"""
