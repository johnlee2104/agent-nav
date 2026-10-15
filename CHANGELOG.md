# Changelog

## 0.2.0 — 2026-10-15
- README: install from GitHub (`pip install git+…@v0.2.0`). agent-nav is not on PyPI, so `pip install agent-nav` failed.
- CI: tests also run on macOS.

Refinements ported from a production fork of the ledger. Each one fixes a failure seen in real agent use:
- `update <paths...> --keep`: refresh only the sha when a file's purpose is unchanged.
- `show <paths...>` prints rows back. `check <paths...>` audits just those paths. `find` takes unquoted words.
- `-d` over 200 chars, `-k` over 6 keywords, or a blank description are refused (exit 2). They used to be silently truncated or blanked.
- A description-only refresh keeps the row's existing keywords and status. `ingest` follows the same rules as `-d`. It splits a `"a,b"` keywords string and names each skipped path with its reason (exit 1).
- Writers accept untracked, non-ignored files, so a row can be written before `git add`. Plain `update <paths>` drops only paths not in the git index and names each row it drops.
- Path arguments are normalised to the git toplevel. Absolute, `./` and `..` paths work. Paths outside the repo or inside a nested checkout are refused before any write.
- `update --auto` refuses to overwrite an existing description unless `--redescribe` is given. It prints `path: old → new` and keeps `status`. `--auto` uses the `sonnet` alias instead of a pinned model id.
- `check` flags **duplicate** rows left by a union merge. Every write replaces only the rows it sets or drops, so unrelated duplicates survive for `check` to report. The README recommends `merge=union` for the ledger.
- `check --staged` reads the staged ledger. It flags rows whose file is not in the commit, including `git add -N` files. Unstaged rows no longer block the commit.
- Symlinks are hashed by their link text, not their target.
- Drift hints offer `--keep` after `-d` and no longer advertise `--auto`.
- `install-hook` for a vendored copy writes a repo-relative, guarded path. Each worktree runs its own copy, and branches without the tool still commit.
- Tests: the hook test now runs the real ledger. It used to pass only because pytest itself failed inside the hook.

- Docs: publish the benchmark behind the README numbers (`docs/benchmark.md`) with probes, per-run CSVs, delta/variance tables and charts.
- README: the headline table reports means, not medians; the 10× pass covered 8 of the 10 probes.

## 0.1.0 — 2026-06-24
- Initial release: description-based codebase navigation index.
- Commands: `find`, `update`, `ingest`, `check`, `install-hook`, `build` (`--auto` optional).
- No model call on any default path; agent-supplied descriptions.
- stdlib-only core; `agent-nav` console script and `python3 -m agent_nav`.
