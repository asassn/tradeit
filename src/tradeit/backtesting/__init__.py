"""Backtesting, walk-forward validation and Monte Carlo.

``base`` holds the contracts; the rest implement them. ``engine`` advances a
clock and calls the live components, ``performance`` turns a curve and a trade
log into metrics, ``walkforward`` splits history and reports the gap between
in-sample and out-of-sample, and ``montecarlo`` turns one path into the
distribution it was drawn from.

The commitment that shapes all of it: **the backtester does not reimplement the
strategy.** It adds the loop, the simulated venue and the measurement, and
nothing else.
"""
