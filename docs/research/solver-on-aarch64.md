# Solver availability on amd64 and aarch64

## HiGHS via `highspy`

- PyPI `highspy` 1.15.1 (2026-07-02), MIT, Python ≥ 3.9, classifiers up to
  3.14.
- Wheels for every CPython 3.9–3.14: `manylinux_2_24/2_28` **x86_64 and
  aarch64**, `musllinux_1_2` x86_64/aarch64/i686, plus macOS and Windows.
  Alpine-based HA images use musl → the `musllinux` wheels matter and exist.
- Source distribution available (C++ build, not acceptable on a Pi at
  install time – images are prebuilt in CI anyway).
- LP and MILP in one library; supports warm start and time limits.

## SciPy fallback

- SciPy 1.18.1 (2026-08-21), Python ≥ 3.12, aarch64 manylinux wheels for
  3.12–3.15. `scipy.optimize.milp` / `linprog(method="highs")` bundle HiGHS,
  so the same model can run without `highspy` if needed. SciPy is heavy
  (tens of MB) – acceptable because the ML load forecaster will likely pull
  NumPy anyway; whether SciPy itself is needed is decided in Phase 4.
- No musllinux wheels checked for SciPy → if the image is Alpine-based,
  prefer `highspy` directly or use the Debian base image (ADR-0001).

## Alternatives considered

- OR-Tools: large wheels, Google-specific API, overkill.
- `python-mip` + CBC: CBC binaries bundled for x86_64 only in the past –
  not verified, not pursued.
- PuLP (what EMHASS uses): a modelling layer that still needs a solver
  binary; HiGHS through `highspy` is leaner.

## Performance expectation

The planner model is 96–192 intervals × (grid import, export, battery
charge, discharge, SoC, per-device power, a few binaries for min-run-time
and semi-continuous loads). Comparable EMHASS/Predbat problems solve in well
under a second on x86 and a few seconds on a Pi 4 with HiGHS. Benchmarking
on real aarch64 hardware with the maintainer's scenario is a Phase 4 task
with a hard budget (< 10 s, BRIEF §2) and the heuristic fallback behind it.

## Verification

Verified on 2026-10-12 against `pypi.org/project/highspy/` and
`pypi.org/project/scipy/` (file lists).

`UNVERIFIED`: actual solve times on a Raspberry Pi 4 / HA Green; SciPy
musllinux wheels.
