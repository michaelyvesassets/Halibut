"""Empirical validation layer: measurement models, calibration to observed
data, rolling-origin backtests and forecasting baselines.

Generic: every industry-specific choice (series, products, covariates,
parameters, origins) lives in a pilot spec file. Data is read only through
``AsOfView`` objects, so a forecast made at origin t can use only what was
publicly available at t.
"""
