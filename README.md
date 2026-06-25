# agent-nav

**A self-maintaining, description-based codebase navigation index for AI agents (and humans).**

`agent-nav` is a **complement to grep, not a replacement.** grep finds exact strings; this finds files by *purpose* — and keeps itself fresh as the code changes, without bloating every task's context window.

## The problem

`grep` answers "where does this string appear?" But an AI agent usually needs to answer "which file handles auth retries?" or "where's the thing that paginates the API?" — questions about *purpose*, not literal tokens. grep can't paraphrase.

The usual fix is a loaded `index.md` of file descriptions pasted into context. It works, but it taxes **every** task's context window whether or not the task needs navigation, and it grows linearly with the repo. The bigger the codebase, the more expensive — and the more it drifts out of date.

`agent-nav` keeps the same description-based map, but the agent **queries** it (one short tool call) instead of carrying the whole thing in context, and a write-through nudge plus a pre-commit hook keep it honest.

## The data

Medians from a Sonnet agent harness over N=10 navigation probes (multi-file, paraphrased, cross-domain queries), replicated N=3 at 10× repo scale:

| method | F1 | recall | MRR | task-success | upfront context |
|---|---|---|---|---|---|
| **agent-nav (ledger)** | **0.81** | **0.87** | **0.97** | **1.00** | **17 tok** |
| loaded index.md | 0.81 | 0.87 | 0.93 | 0.95 | 990 tok → 9,442 at 10× |
| grep baseline | 0.62 | 0.72 | 0.70 | 1.00 | 0 tok |

- **vs grep:** +0.19 F1, +0.15 recall, +0.27 MRR. Description-based scaffolding beats blind grep on realistic queries — the ones where the word you'd grep for isn't in the file.
- **vs a loaded index:** *identical* retrieval quality, but roughly **60× less** upfront context, and the gap *widens* with repo size. In a benchmark, the loaded index grew from 990 to **9,442** tokens at 10× scale while agent-nav stayed flat at **17**. The index doesn't get answers wrong at scale — it gets expensive.
- **Robustness:** across **3 replicates** (48 runs) agent-nav was the **only** method that recovered a stale/moved pointer every single time — task-success **1.00 ± 0.00**, MRR **0.98 ± 0.01**.
- **Cautionary contrast:** a cheaper tag-based scheme *collapsed* at 10× scale (F1 **0.44**, task-success **0.75**). Cheap query methods are not interchangeable — ranking on free-text descriptions is what survives scale.

## How it complements grep

| use | reach for |
|---|---|
| exact string / symbol / regex | **grep** (keep it — agent-nav doesn't replace it) |
| "which file does X?" / purpose / paraphrase | **agent-nav** `find` |
| a freshness-guaranteed living map of the repo | **agent-nav** ledger |

## Install / setup

Two modes. Pick one — the index data lives in the same place either way.

### Mode A — vendored (no install)

`ledger.py` is stdlib-only, so "vendoring" is just copying one file into your repo. Put it wherever you like; this README uses `src/maintenance/` as the worked example.

```bash
cp path/to/agent_nav/ledger.py src/maintenance/ledger.py
python3 src/maintenance/ledger.py --help
```

### Mode B — pip install

```bash
pip install agent-nav
agent-nav --help
```

This gives you the `agent-nav` console script.

### Where the index lives (both modes)

The **tool code** location differs, but the **index data** is always a committed file at your repo root — never in site-packages.

| mode | tool code lives in | index data lives in (committed) |
|---|---|---|
| vendored | `your-repo/src/maintenance/ledger.py` | `your-repo/.agent-nav/ledger.jsonl` |
| pip install | site-packages (outside your repo) | `your-repo/.agent-nav/ledger.jsonl` |

`.agent-nav/ledger.jsonl` is one JSON object per line — `{"path", "description", "keywords", "sha"}` — sorted by path. Commit it with your code.

## Bootstrap a repo

```bash
agent-nav check                       # lists files that have no ledger entry yet
# the agent reads those files and writes one-line purposes for each, as JSON:
#   [{"path": "src/api/pager.py", "description": "...", "keywords": ["api","pagination"]}, ...]
agent-nav ingest descriptions.json    # bulk-set entries (reads stdin if no file)
agent-nav install-hook                # pre-commit backstop
git add .agent-nav/ledger.jsonl && git commit -m "add agent-nav index"
```

No model call is billed: the agent already reading your code supplies the descriptions.

## Day-to-day (for the agent)

```bash
agent-nav find "where retries are configured"        # ranked purpose search
agent-nav update src/api/pager.py -d "cursor pagination for the public API" -k "api,pagination"
git add .agent-nav/ledger.jsonl                       # stage the updated index
agent-nav update src/api/old_pager.py                 # no -d => drops a deleted/renamed file
```

`update <path> -d "..." -k "..."` sets one entry (you write the text). `update <paths...>` with no `-d` drops entries for files that are gone.

## How freshness works

Two layers, neither calls a model:

1. **Write-through nudge.** The CLAUDE.md block below tells the agent to `update` the entry whenever it substantially changes a file. This keeps the common case fresh in the same session that touched the code.
2. **Pre-commit backstop.** `install-hook` adds a hook that runs `check --staged` and **blocks any commit that leaves the index stale**. It only compares hashes — no model call.

The `sha` field is the SHA-256 of the file's bytes when its description was written. A file is "stale" only when its current sha differs from the stored one, so re-running `update` (which rewrites the sha) clears the flag. The ledger data file itself is excluded from the index, so it never self-flags.

## Enable for your agent (CLAUDE.md / AGENTS.md / GEMINI.md)

Paste one of these blocks into your agent's instructions file. **Swap in your actual path** — the vendored variant assumes `src/maintenance/ledger.py`; change it to wherever you copied the file.

**Vendored variant:**

```markdown
## Codebase navigation
Find files by purpose: `python3 src/maintenance/ledger.py find "<query>"` (grep still
handles exact-string search). After adding or substantially changing a file, write a
one-line purpose for it and record it: `python3 src/maintenance/ledger.py update <path>
-d "<purpose>" -k "kw1,kw2"`, then stage `.agent-nav/ledger.jsonl`. For a deleted/renamed
file, just `update <path>` to drop its entry. Do NOT use `--auto` unless asked — you supply
the description (no extra model call). The pre-commit hook blocks any commit that leaves the
index stale.
```

**pip-install variant:**

```markdown
## Codebase navigation
Find files by purpose: `agent-nav find "<query>"` (grep still handles exact-string search).
After adding or substantially changing a file, write a one-line purpose for it and record it:
`agent-nav update <path> -d "<purpose>" -k "kw1,kw2"`, then stage `.agent-nav/ledger.jsonl`.
For a deleted/renamed file, just `agent-nav update <path>` to drop its entry. Do NOT use
`--auto` unless asked — you supply the description (no extra model call). The pre-commit hook
blocks any commit that leaves the index stale.
```

## Commands

| command | what it does | calls a model? |
|---|---|---|
| `find "<query>" [-n N]` | rank entries by purpose, print matches | no |
| `update <path> -d "..." -k "..." [-s status]` | set one entry (you write the text) | no |
| `update <paths...>` | drop entries for deleted/renamed files | no |
| `ingest [file]` | bulk-set from JSON (stdin if no file) | no |
| `check [--staged]` | report drift; exit non-zero if any | no |
| `install-hook` | install the pre-commit backstop | no |
| `build --auto` | (re)describe every tracked file via the `claude` CLI (full rebuild) | **yes** |

(Bare `build` without `--auto` does nothing but point you at the agent-driven flow above — `check` to list files, then `ingest`. The full index is built from agent-supplied descriptions, or via `build --auto`.)

`--auto` (on `update`/`build`) is the only path that calls a model; it uses your Claude Code login and may count toward API/SDK usage. `-s/--status` tags an entry's lifecycle (`current`/`superseded`/`archived`/`draft`) for docs and runbooks.

## Caveats (honest)

- **N=10 probes.** The benchmark is directional, not a large-scale eval.
- **Synthetic repo.** Scores come from a constructed benchmark repo, not a survey of real projects.
- **Simple lexical ranker.** `find` ranks on free-text descriptions with a lightweight lexical match — no embeddings, no BM25.
- **`forbid` condition is prompt-enforced.** The "no native search" arm relied on the agent obeying the prompt, not a hard sandbox.
- **Paraphrase-beats-grep is unconfirmed.** It's the most likely source of the headline advantage, but it hasn't been isolated and proven on its own.

## License

MIT — see [LICENSE](LICENSE).
