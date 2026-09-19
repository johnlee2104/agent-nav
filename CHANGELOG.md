# Changelog

## Unreleased
- Docs: publish the benchmark behind the README numbers (`docs/benchmark.md`) with probes, per-run CSVs, delta/variance tables and charts.
- README: the headline table reports means, not medians; the 10× pass covered 8 of the 10 probes.

## 0.1.0 — 2026-06-24
- Initial release: description-based codebase navigation index.
- Commands: `find`, `update`, `ingest`, `check`, `install-hook`, `build` (`--auto` optional).
- No model call on any default path; agent-supplied descriptions.
- stdlib-only core; `agent-nav` console script and `python3 -m agent_nav`.
