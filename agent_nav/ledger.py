#!/usr/bin/env python3
"""Lean codebase-navigation ledger — find / show / update / ingest / check.

The ledger is one JSON object per line in .agent-nav/ledger.jsonl, sorted by
path:  {"path": ..., "description": ..., "keywords": [...], "sha": ...}

By default the tool makes NO model call: the agent already reading your code
supplies the descriptions, so nothing is billed beyond the session it's already in.

  update <path> -d "purpose" -k "kw1,kw2"   set one entry (you write the text). -d over
                                             200 chars or -k over 6 keywords → refused
                                             (exit 2), never truncated
  update <paths...> --keep                   purpose unchanged: refresh only the sha,
                                             keeping description/keywords/status
  update <paths...>                          drop entries for files not in the git index
                                             (deleted/renamed/`git rm --cached`); a
                                             tracked text file → asks for -d instead
  ingest [file]                              the BATCH path: set many entries from JSON
                                             [{path,description,keywords?,status?}, ...]
                                             (stdin if no file); same rules as -d
                                             (description required, caps refused), and
                                             no shell quoting for `$`, backticks, `<->`
  find  <query words...>                     rank entries by purpose, print matches
  show  <paths...>                           print each path's row (exit 1 if none)
  check  [--staged | paths...]               report drift; exit non-zero if any. The
                                             pre-commit hook runs `check --staged`.
  install-hook                               install the pre-commit backstop

Every command runs at the git toplevel. Paths may be repo-relative (to --repo) or
absolute; outside the repo, or inside a nested checkout (a `.git` below the
toplevel, e.g. a worktree kept inside the repo) → refused. Writers (-d, --keep,
--auto, ingest) accept an untracked file as long as git does not ignore it — no
`git add` needed first. Plain `update <paths>` (no flags) only DROPS.

  --auto (on update/build)                   opt in to describing via the `claude`
                                             CLI instead of supplying text yourself.
                                             `update --auto` REFUSES a path whose
                                             row already has a description — add
                                             --redescribe to overwrite.

`sha` is the SHA-256 of the file's bytes when its description was written. A file
is "stale" only when its current sha differs from the stored one — so re-setting
the entry (which rewrites the sha) clears the flag. `find` and `check` are pure
git/text and never call a model. The ledger data file is excluded from the index
so it never self-flags.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import shlex
import subprocess
import sys
import tempfile

MAX_BYTES = 100_000
MODEL = "sonnet"  # CLI alias = the latest Sonnet, so it never pins a retired model id
LEDGER_REL = ".agent-nav/ledger.jsonl"


# --------------------------------------------------------------------------- #
# store
# --------------------------------------------------------------------------- #
def ledger_path(repo) -> pathlib.Path:
    return pathlib.Path(repo) / LEDGER_REL


def load(repo) -> list:
    p = ledger_path(repo)
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]


def save(repo, entries) -> None:
    p = ledger_path(repo)
    p.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(entries, key=lambda e: e["path"])
    p.write_text("".join(json.dumps(e, separators=(",", ":")) + "\n" for e in ordered))


def _write(repo, rows, set_rows=None, drop_paths=()) -> None:
    """The one writer save: replace ONLY the paths this call sets or drops.

    Every untouched row — a union-merge duplicate included — is kept verbatim. A
    by-path dict round-trip would collapse any duplicate to its last row (possibly
    the stale side) on every unrelated write, before `check` could report it. A
    path this call sets is written once, which is the documented dedupe
    (`update <dup> -d …`).
    """
    set_rows = set_rows or {}
    touched = set(set_rows) | set(drop_paths)
    save(repo, [e for e in rows if e["path"] not in touched] + list(set_rows.values()))


def _is_ledger_path(rel) -> bool:
    # the ledger data file describes the repo, not itself — never index/flag it
    return rel == LEDGER_REL


# --------------------------------------------------------------------------- #
# git + content helpers
# --------------------------------------------------------------------------- #
def tracked_files(repo) -> list:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=str(repo),
        capture_output=True, text=True, check=True,
    ).stdout
    return [p for p in out.split("\0") if p]


def candidate_files(repo) -> set:
    """Tracked ∪ untracked-but-not-ignored: what a WRITER may index, and what a
    row's file must still be in to not be orphaned. Lets the natural order
    (write file → ledger update → git add) work."""
    out = subprocess.run(
        ["git", "ls-files", "-z", "--others", "--exclude-standard"], cwd=str(repo),
        capture_output=True, text=True, check=True,
    ).stdout
    return set(tracked_files(repo)) | {p for p in out.split("\0") if p}


def _toplevel(repo) -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=str(repo),
                              capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        raise ValueError(f"{repo} is not inside a git repo")


def _repo_rel(repo, paths) -> tuple:
    """(git toplevel, `paths` as posix toplevel-relative strings).

    Rows are keyed by git-relative path, so a raw absolute/`./`/`..` path never
    matches one — it would read as "not tracked" and `update` would silently drop
    nothing. A relative input is relative to `repo`. Normalisation is lexical (a
    tracked symlink must stay the link's own path — see _sha); only on a miss is
    the PARENT dir realpath'd (macOS /tmp → /private/tmp; git's toplevel is
    physical). Raises ValueError naming the first path outside the repo.
    """
    top = _toplevel(repo)
    out = []
    for p in paths:
        full = os.path.abspath(os.path.join(repo, p))  # join keeps an absolute p
        for cand in (full, os.path.join(os.path.realpath(os.path.dirname(full)),
                                        os.path.basename(full))):
            rel = os.path.relpath(cand, top)
            if rel != os.pardir and not rel.startswith(os.pardir + os.sep):
                _refuse_nested(p, top, rel)
                out.append(pathlib.PurePath(rel).as_posix())
                break
        else:
            raise ValueError(f"{p} is outside the repo ({top})")
    return top, out


def _refuse_nested(p, top, rel) -> None:
    """A path under a nested checkout (e.g. a git worktree kept inside the repo,
    seen from the main checkout) normalises to a key no row has. Git-free: any
    dir strictly below `top` holding a `.git` entry → ValueError."""
    d = os.path.dirname(rel)
    while d:
        if os.path.lexists(os.path.join(top, d, ".git")):
            other = os.path.join(top, d)
            raise ValueError(f"{p} is inside another checkout ({other}) — "
                             f"pass --repo {other}")
        d = os.path.dirname(d)


def staged_changes(repo) -> list:
    """Parse `git diff --cached --name-status -z` → list of (code, a, b).

    -z (NUL-delimited) means paths with spaces/newlines/unicode/'->' are safe.
    """
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-status", "-z"], cwd=str(repo),
        capture_output=True, text=True, check=True,
    ).stdout
    toks = out.split("\0")
    changes, i = [], 0
    while i < len(toks):
        status = toks[i]
        if not status:
            i += 1
            continue
        code = status[0]
        if code in ("R", "C"):  # rename/copy: status, old, new
            changes.append((code, toks[i + 1], toks[i + 2]))
            i += 3
        else:                   # A/M/D/T/...: status, path
            changes.append((code, toks[i + 1], None))
            i += 2
    return changes


def _sha(repo, rel) -> str | None:
    p = pathlib.Path(repo) / rel
    try:
        if p.is_symlink():  # hash the link itself, not the target — else the
            # entry goes stale whenever the target changes, invisibly to
            # `check --staged` (the unchanged link never appears in the diff)
            return hashlib.sha256(os.readlink(p).encode()).hexdigest()
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        return None


def _read_text(repo, rel) -> str | None:
    p = pathlib.Path(repo) / rel
    try:
        if p.stat().st_size > MAX_BYTES:
            return None
        return p.read_text()
    except (OSError, UnicodeDecodeError):
        return None  # binary, gone, or oversize → not indexable


# --------------------------------------------------------------------------- #
# describe (the only LLM call) — runs through the `claude` CLI by default, so it
# uses whatever your Claude Code session is logged in with (a subscription is
# fine; no ANTHROPIC_API_KEY needed).
# --------------------------------------------------------------------------- #
_PROMPT = (
    "Summarize this source file for a code-navigation index. Reply with ONLY a "
    "minified JSON object and nothing else (no prose, no code fences):\n"
    '{{"description":"<single primary purpose, <=120 chars>",'
    '"keywords":["3-6","lowercase","search","terms"]}}\n\n'
    "Path: {path}\n\n```\n{content}\n```"
)


def _extract_json(text) -> dict:
    """Pull the first {...} JSON object out of a model reply (tolerates fences/prose)."""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError(f"no JSON object in model reply: {text[:200]!r}")
    return json.loads(m.group(0))


def describe(path, content) -> dict:
    """Ask Claude (via the `claude` CLI) for a one-line purpose + keywords.

    Uses your Claude Code login — subscription or API key, whichever the CLI has.
    Returns {description, keywords}. Run from a temp cwd so the target repo's own
    CLAUDE.md/context isn't pulled into every describe call.
    """
    prompt = _PROMPT.format(path=path, content=(content or "")[:12000])
    try:
        proc = subprocess.run(
            ["claude", "-p", "--model", MODEL, "--output-format", "json"],
            input=prompt, capture_output=True, text=True,
            cwd=tempfile.gettempdir(),
        )
    except FileNotFoundError:
        raise RuntimeError(
            "the `claude` CLI was not found on PATH. Install Claude Code, or pass "
            "a custom describe_fn to build()/update()."
        )
    if proc.returncode != 0:
        raise RuntimeError(f"`claude` CLI failed (exit {proc.returncode}): {proc.stderr.strip()[:300]}")
    outer = json.loads(proc.stdout)            # the CLI's JSON envelope
    if outer.get("is_error"):
        raise RuntimeError(f"`claude` reported an error: {str(outer.get('result'))[:300]}")
    data = _extract_json(outer["result"])      # the model's JSON, our schema
    return {
        "description": data["description"].strip()[:200],
        "keywords": [k.strip().lower() for k in data.get("keywords", []) if k.strip()][:6],
    }


def _entry(repo, rel, describe_fn) -> dict:
    text = _read_text(repo, rel)
    return {"path": rel, **describe_fn(rel, text), "sha": _sha(repo, rel)}


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
def build(repo, describe_fn=describe) -> int:
    entries = []
    for rel in tracked_files(repo):
        if _is_ledger_path(rel) or _read_text(repo, rel) is None:
            continue
        entries.append(_entry(repo, rel, describe_fn))
    save(repo, entries)
    print(f"agent-nav: {len(entries)} entries", file=sys.stderr)
    return 0


def update(repo, paths, describe_fn=describe, redescribe=False) -> int:
    """--auto path: describe via describe_fn, dropping gone/ignored paths.

    A path whose row already carries a description is REFUSED: a generated line
    silently clobbering a curated one is invisible to `check`, which only compares
    shas. `redescribe=True` (`--redescribe`) opts in. Every description this writes
    is announced `path: old → new` on stderr first, so a review sees it.
    Undescribed paths in the same call still land — first-time describing stays
    one command — and the refusal only sets the exit code.
    """
    rows = load(repo)
    by_path = {e["path"]: e for e in rows}  # lookup only; _write saves
    cands = candidate_files(repo)
    refused, new, gone = [], {}, []
    for rel in paths:
        if not _indexable(repo, rel, cands):
            gone.append(rel)  # gone / ignored → drop the entry
            continue
        prev = by_path.get(rel, {})
        old = (prev.get("description") or "").strip()
        if old and not redescribe:
            refused.append(rel)
            continue
        e = _entry(repo, rel, describe_fn)
        # describe_fn never generates `status` (a lifecycle/authority tag a human
        # set), so overwriting the row would drop it. Keywords ARE regenerated:
        # that's --auto.
        if prev.get("status"):
            e["status"] = prev["status"]
        # cap here too: describe_fn is injectable, and the printed line must show
        # what is actually STORED (descriptions are truncated at 200 chars)
        e["description"] = (e.get("description") or "").strip()[:MAX_DESC]
        print(f"{rel}: {old or '(none)'} → {e['description']}", file=sys.stderr)
        new[rel] = e
    _write(repo, rows, new, gone)
    if refused:
        print("agent-nav: refusing to overwrite an existing description (write the "
              "new one yourself, or pass --redescribe):", file=sys.stderr)
        for rel in refused:
            print(f'    {_prog()} update {shlex.quote(rel)} '
                  '-d "<one-line purpose>" -k "kw1,kw2,kw3"', file=sys.stderr)
        return 1
    return 0


# --------------------------------------------------------------------------- #
# agent-supplied entries (the default — no model call)
# --------------------------------------------------------------------------- #
def _why_not_indexable(repo, rel, cands) -> str | None:
    """Which gate rejects `rel`, or None if it is indexable.

    Several unrelated gates share this predicate; callers that report an error to
    a human/agent must say which one fired, or the fix is a guess.
    """
    if _is_ledger_path(rel):
        return "it is the ledger data file (never self-indexed) — no entry needed"
    if rel not in cands:
        return "it is git-ignored or does not exist"
    try:
        if (pathlib.Path(repo) / rel).stat().st_size > MAX_BYTES:
            return f"it is over {MAX_BYTES} bytes — oversize files need no entry"
    except OSError:
        pass  # e.g. a dangling symlink: _read_text below reports it unreadable
    if _read_text(repo, rel) is None:
        return "it is not readable as UTF-8 text (binary?) — binaries need no entry"
    return None


def _indexable(repo, rel, cands) -> bool:
    return _why_not_indexable(repo, rel, cands) is None


_STATUS = ("current", "superseded", "archived", "draft")
MAX_DESC, MAX_KW = 200, 6


def _bad_text(description, keywords) -> str | None:
    """Why caller-written text must be refused before any write, or None.

    Silent truncation loses the tail of a description the agent meant to keep, so
    the CLI refuses instead. --auto output is generated and keeps the silent cap.
    A blank description would overwrite a curated one with "" — refused too.
    """
    n = len((description or "").strip())
    if n == 0:
        return "description is empty — write the one-line purpose"
    if n > MAX_DESC:
        return f"description is {n} chars, the limit is {MAX_DESC} — shorten it"
    k = len([x for x in (keywords or []) if str(x).strip()])
    if k > MAX_KW:
        return f"{k} keywords given, the limit is {MAX_KW} — drop some"
    return None


def _mk_entry(repo, rel, description, keywords, status=None) -> dict:
    # `status` is an optional lifecycle/authority axis, kept separate from
    # `description` so it never pollutes purpose-ranking in find(). Sparse:
    # omitted entirely for plain code; set only on docs/runbooks that need it.
    e = {"path": rel,
         "description": (description or "").strip()[:MAX_DESC],
         "keywords": [str(k).strip().lower() for k in (keywords or []) if str(k).strip()][:MAX_KW]}
    s = (status or "").strip().lower()
    if s:
        e["status"] = s
    e["sha"] = _sha(repo, rel)
    return e


def set_entry(repo, rel, description, keywords, status=None) -> int:
    """Upsert one entry from a caller-supplied description (computes the sha)."""
    return ingest(repo, [{"path": rel, "description": description,
                          "keywords": keywords, "status": status}])


def keep(repo, paths) -> int:
    """`update --keep`: purpose unchanged → rewrite ONLY the sha of each path.

    Refuses the WHOLE call (exit 1, nothing written) if any path has no described
    row, is not indexable, or has duplicate rows (last-wins would keep whichever
    union-merge side came last — possibly the stale one).
    """
    cands = candidate_files(repo)
    rows = load(repo)
    by_path = {e["path"]: e for e in rows}
    bad = []
    for rel in paths:
        why = _why_not_indexable(repo, rel, cands)
        if not why and not (by_path.get(rel, {}).get("description") or "").strip():
            why = "it has no described row to keep"
        if not why and sum(e["path"] == rel for e in rows) > 1:
            why = "it has several rows (union merge) — merge them into one"
        if why:
            bad.append((rel, why))
    if bad:
        print("agent-nav: --keep refused, nothing changed — describe these instead:",
              file=sys.stderr)
        for rel, why in bad:
            print(f"  {rel}: {why}\n    {_prog()} update "
                  f'{shlex.quote(rel)} -d "<one-line purpose>" -k "kw1,kw2,kw3"',
                  file=sys.stderr)
        return 1
    _write(repo, rows, {rel: {**by_path[rel], "sha": _sha(repo, rel)} for rel in paths})
    print(f"agent-nav: kept {len(set(paths))} row(s) (sha refreshed)", file=sys.stderr)
    return 0


def drop(repo, paths) -> int:
    """Remove entries for the given paths (deleted/renamed-away files); say what
    happened to each, so a no-op is never silent."""
    rows = load(repo)
    have = {e["path"] for e in rows}
    for rel in paths:
        if rel in have:
            print(f"agent-nav: dropped {rel}", file=sys.stderr)
        else:
            print(f"agent-nav: {rel} had no row — nothing to drop", file=sys.stderr)
    _write(repo, rows, drop_paths=paths)
    return 0


def ingest(repo, items) -> int:
    """Upsert caller-supplied entries: [{path, description, keywords?, status?}, ...].

    The one write path for `update -d` (via set_entry) and batches. A
    non-indexable item is reported with its reason and sets exit 1; the valid
    items in the same call still land.
    """
    cands = candidate_files(repo)
    rows = load(repo)
    by_path = {e["path"]: e for e in rows}  # lookup only; _write saves
    new, n, rc = {}, 0, 0
    for it in items:
        rel = it["path"]
        why = _why_not_indexable(repo, rel, cands)
        if why:
            print(f"agent-nav: {rel} not indexed — {why}", file=sys.stderr)
            rc = 1
            continue
        # A description-only refresh must not wipe fields it didn't mention:
        # omitted/empty keywords or status inherit the existing entry's. No caller
        # legitimately clears keywords to [] — to change them, pass -k.
        prev = new.get(rel) or by_path.get(rel, {})  # same path twice: last wins
        status = it.get("status")
        new[rel] = _mk_entry(repo, rel, it.get("description", ""),
                             it.get("keywords") or prev.get("keywords", []),
                             status if (status or "").strip() else prev.get("status"))
        n += 1
    if n:
        _write(repo, rows, new)
    if len(items) > 1:
        print(f"agent-nav: ingested {n} of {len(items)} entries", file=sys.stderr)
    elif n:
        print(f"agent-nav: set {items[0]['path']}", file=sys.stderr)
    return rc


# common words that carry no navigation signal — dropped from queries so they
# can't dominate ranking (e.g. "on" matching inside "connection")
_STOP = frozenset(
    "a an the of on in to for and or is are be do we i it by with at as from this "
    "that where how what which when who code file function class method".split()
)


def _terms(query) -> list:
    # keep distinct tokens of length >= 3 that aren't stopwords
    seen, out = set(), []
    for t in re.split(r"\W+", query.lower()):
        if len(t) >= 3 and t not in _STOP and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def find(repo, query, n=8) -> list:
    terms = _terms(query)
    scored = []
    for e in load(repo):
        kw = " ".join(e.get("keywords", [])).lower()
        path, desc = e["path"].lower(), e["description"].lower()
        # weight by where a term hits: keywords > path > description. Each term
        # counts once (presence, not frequency) so high-signal words can't be
        # drowned out by a stopword/substring that repeats across the haystack.
        score = sum(3 if t in kw else 2 if t in path else 1 if t in desc else 0
                    for t in terms)
        if score:
            scored.append((score, e))
    scored.sort(key=lambda x: (-x[0], x[1]["path"]))
    return [e for _, e in scored[:n]]


def _flag(repo, rel, entries, drift) -> None:
    """A file is stale if its sha drifted from the entry; missing if indexable with no entry."""
    if _is_ledger_path(rel):
        return
    e = entries.get(rel)
    if e is not None:
        if _sha(repo, rel) != e.get("sha"):
            drift["stale"].append(rel)
    elif _read_text(repo, rel) is not None:
        drift["missing"].append(rel)


def check(repo, staged=False, paths=None) -> dict:
    """Return drift buckets. staged=True scopes to the git index (for the hook);
    `paths` (repo-relative) scopes to those paths. Orphaned = a row whose file is
    neither tracked nor untracked-and-not-ignored (an un-added file is not gone);
    staged mode is stricter — any row of the STAGED ledger.jsonl (`git show
    :.agent-nav/ledger.jsonl`; none if it is not in the index) whose file is not
    in the tree being committed (`git write-tree`, so `git add -N` does not
    count), since that row would be committed without its file."""
    rows = load(repo)
    entries = {e["path"]: e for e in rows}
    drift = {"stale": [], "missing": [], "orphaned": [], "duplicate": []}
    # A union merge of ledger.jsonl keeps BOTH branches' rows for a path; the dict
    # above silently keeps the last, while `find` returns both. Flag it in every
    # mode — a duplicate is a ledger-file defect regardless of what's staged.
    seen = set()
    for e in rows:
        if e["path"] in seen and e["path"] not in drift["duplicate"]:
            drift["duplicate"].append(e["path"])
        seen.add(e["path"])
    if staged:
        for code, a, b in staged_changes(repo):
            if code in ("A", "M", "T"):
                _flag(repo, a, entries, drift)
            elif code == "D":
                if a in entries and not _is_ledger_path(a):
                    drift["orphaned"].append(a)
            elif code in ("R", "C"):
                if a in entries and not _is_ledger_path(a):
                    drift["orphaned"].append(a)   # old path
                if b is not None:
                    _flag(repo, b, entries, drift)  # new path
            # unmerged (U) / unknown codes: left alone — git blocks such commits itself
        # writers accept un-added files, so a row can reach a commit whose file
        # never does (`update scratch -d …` + `git add ledger.jsonl`): flag every
        # row OF THE STAGED jsonl whose file is not in the index. Read the index
        # copy, not the worktree: a worktree-only drop must not hide a staged
        # orphan, and someone else's unstaged row must not block this commit.
        # Membership = the tree being committed: `git add -N` (intent-to-add) is in
        # ls-files but commits no content. Unmerged index → write-tree fails → fall
        # back to ls-files.
        tree = subprocess.run(["git", "write-tree"], cwd=str(repo),
                              capture_output=True, text=True)
        tracked = (set(p for p in subprocess.run(
            ["git", "ls-tree", "-r", "-z", "--name-only", tree.stdout.strip()],
            cwd=str(repo), capture_output=True, text=True, check=True,
        ).stdout.split("\0") if p) if tree.returncode == 0 else set(tracked_files(repo)))
        shown = subprocess.run(["git", "show", f":{LEDGER_REL}"], cwd=str(repo),
                               capture_output=True, text=True)
        staged_rows = ({json.loads(ln)["path"] for ln in shown.stdout.splitlines()
                        if ln.strip()} if shown.returncode == 0 else set())
        for path in sorted(staged_rows):
            if (path not in tracked and not _is_ledger_path(path)
                    and path not in drift["orphaned"]):
                drift["orphaned"].append(path)
    elif paths:
        cands = candidate_files(repo)
        for rel in paths:
            if rel in cands:
                _flag(repo, rel, entries, drift)
            elif rel in entries and not _is_ledger_path(rel):
                drift["orphaned"].append(rel)
            elif not _is_ledger_path(rel):
                drift.setdefault("unknown", []).append(rel)  # typo / directory
    else:
        cands = candidate_files(repo)
        # missing/stale walk TRACKED files only — every scratch untracked file
        # would otherwise read as missing
        for rel in sorted(tracked_files(repo)):
            _flag(repo, rel, entries, drift)
        for path in sorted(entries):
            if path not in cands and not _is_ledger_path(path):
                drift["orphaned"].append(path)
    return drift


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _prog() -> str:
    """Best-effort command prefix for help text, valid from any cwd."""
    a0 = sys.argv[0] or ""
    base = os.path.basename(a0)
    if base in ("agent-nav", "agent_nav"):
        return "agent-nav"
    if base == "__main__.py":
        return "python3 -m agent_nav"
    if a0:
        return f"python3 {shlex.quote(os.path.abspath(a0))}"
    return "agent-nav"


def _hook_prog(top) -> tuple:
    """(command prefix, vendored script path or None) for the pre-commit hook.

    A vendored script inside the repo is referenced by its toplevel-relative
    path: git runs hooks at the worktree root, and the hooks dir is shared by
    every worktree, so each worktree must run ITS OWN copy rather than the
    absolute path of whichever checkout ran install-hook.
    """
    prog = _prog()
    a0 = sys.argv[0] or ""
    if not a0 or os.path.basename(a0) in ("agent-nav", "agent_nav", "__main__.py"):
        return prog, None  # installed console script / `python3 -m agent_nav`
    rel = os.path.relpath(os.path.realpath(a0), os.path.realpath(top))
    if rel == os.pardir or rel.startswith(os.pardir + os.sep):
        return prog, None  # vendored outside this repo: keep the absolute path
    rel = pathlib.PurePath(rel).as_posix()
    return f"python3 {shlex.quote(rel)}", rel


def _hook_script(prog, vendored=None) -> str:
    head = (
        "#!/bin/sh\n"
        "# agent-nav freshness hook — blocks commits that leave the index stale.\n"
        "# Reinstall with `agent-nav install-hook` (or the vendored equivalent).\n"
    )
    if vendored is None:
        return head + f"exec {prog} check --staged\n"
    # The hooks dir is shared by every worktree, so this also fires on a branch
    # cut before the tool was vendored in. Skip there instead of failing every
    # commit with "file not found"; the check activates once the tool is merged.
    q = shlex.quote(vendored)
    return head + f"if [ -f {q} ]; then\n    exec {prog} check --staged\nfi\n"


def install_hook(repo) -> int:
    top = _toplevel(repo)
    hooks = subprocess.run(
        ["git", "rev-parse", "--git-path", "hooks"], cwd=top,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    dest = pathlib.Path(top) / hooks
    dest.mkdir(parents=True, exist_ok=True)
    dest = dest / "pre-commit"
    dest.write_text(_hook_script(*_hook_prog(top)))
    dest.chmod(0o755)
    print(f"agent-nav: installed pre-commit hook at {dest}")
    return 0


def _print_drift(drift) -> int:
    unknown = drift.pop("unknown", [])
    for rel in unknown:
        print(f"agent-nav: {rel} is not a file in the repo and has no row", file=sys.stderr)
    if sum(len(v) for v in drift.values()) == 0:
        if unknown:
            return 1
        print("agent-nav: up to date")
        return 0
    for rel in drift["stale"]:
        print(f"stale (content changed since its description was written): {rel}")
    for rel in drift["missing"]:
        print(f"missing (no ledger entry): {rel}")
    for rel in drift["orphaned"]:
        print(f"orphaned (row's file is not in the git index — git add it, or drop "
              f"the row): {rel}")
    for rel in drift["duplicate"]:
        print(f"duplicate (several rows for one path — a union merge kept both sides; "
              f"merge the descriptions into ONE, then update): {rel}")
    print()
    print("Index is out of date. You write the descriptions (no model call needed):")
    prog = _prog()
    # duplicates too: a write replaces every row of the path it sets (_write), so
    # one `update <dup> -d` dedupes it
    for rel in sorted(set(drift["stale"] + drift["missing"] + drift["duplicate"])):
        print(f'    {prog} update {shlex.quote(rel)} '
              '-d "<one-line purpose>" -k "kw1,kw2,kw3"')
    if drift["stale"]:
        stale = " ".join(shlex.quote(p) for p in sorted(set(drift["stale"])))
        print(f"    {prog} update {stale} --keep"
              "      # instead of -d, ONLY if the purpose is unchanged")
    if drift["orphaned"]:
        gone = " ".join(shlex.quote(p) for p in sorted(set(drift["orphaned"])))
        print(f"    {prog} update {gone}      # drop gone files")
        print(f"    git add {gone}      # or, if the file should exist: track it")
    print(f"    git add {LEDGER_REL}")
    return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="agent-nav", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=".")
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("find"); f.add_argument("query", nargs="+")
    f.add_argument("-n", type=int, default=8)
    c = sub.add_parser("check"); c.add_argument("--staged", action="store_true")
    c.add_argument("paths", nargs="*", help="report drift for these paths only")
    sub.add_parser("show").add_argument("paths", nargs="+")
    ig = sub.add_parser("ingest")
    ig.add_argument("file", nargs="?", help="JSON [{path,description,keywords?,status?}]; "
                                            "default stdin")
    u = sub.add_parser("update")
    u.add_argument("paths", nargs="+")
    u.add_argument("-d", "--description",
                   help=f"one-line purpose for a single file (max {MAX_DESC} chars)")
    u.add_argument("-k", "--keywords", default="",
                   help=f"comma/space separated keywords (max {MAX_KW}); replaces the set")
    u.add_argument("-s", "--status", default=None,
                   help=f"optional lifecycle/authority tag {_STATUS} (docs/runbooks)")
    u.add_argument("--keep", action="store_true",
                   help="purpose unchanged: refresh only the sha, keeping "
                        "description/keywords/status")
    u.add_argument("--auto", action="store_true",
                   help="describe via the `claude` CLI instead of supplying -d "
                        "(uses your Claude Code login; may count toward API/SDK usage). "
                        "Refuses paths that already have a description")
    u.add_argument("--redescribe", action="store_true",
                   help="with --auto: allow overwriting an existing description "
                        "(prints `path: old -> new` for each)")
    b = sub.add_parser("build")
    b.add_argument("--auto", action="store_true", help="describe every file via the `claude` CLI")
    sub.add_parser("install-hook")
    args = ap.parse_args(argv)
    if args.cmd == "check" and args.staged and args.paths:
        c.error("--staged takes no paths (pass paths without --staged)")
    if args.cmd == "update" and args.keep and (
            args.description is not None or args.keywords or args.status or args.auto):
        u.error("--keep refreshes the sha only; it takes no -d/-k/-s/--auto")

    # every command runs at the git toplevel; path args are normalised against
    # --repo BEFORE any read/write, and one bad path refuses the whole call
    if args.cmd == "ingest":
        items = json.loads(pathlib.Path(args.file).read_text() if args.file
                           else sys.stdin.read())
        for it in items:  # a "a,b" string would iterate as characters
            if isinstance(it.get("keywords"), str):
                it["keywords"] = [k for k in re.split(r"[,\s]+", it["keywords"]) if k]
    try:
        repo, paths = _repo_rel(args.repo, [it["path"] for it in items]
                                if args.cmd == "ingest" else getattr(args, "paths", []))
    except ValueError as e:
        print(f"agent-nav: {e} — nothing changed", file=sys.stderr)
        return 2
    if args.cmd == "find":
        for e in find(repo, " ".join(args.query), n=args.n):
            st = e.get("status")
            tag = f"  [{st}]" if st and st != "current" else ""
            print(f"{e['path']}\t{e['description']}{tag}")
        return 0
    if args.cmd == "check":
        return _print_drift(check(repo, paths=paths) if paths
                            else check(repo, staged=args.staged))
    if args.cmd == "show":
        rows = load(repo)
        rc = 0
        for p in paths:
            hits = [e for e in rows if e["path"] == p]
            for e in hits:
                print(json.dumps(e, ensure_ascii=False))
            if not hits:
                print(f"agent-nav: {p} has no row", file=sys.stderr)
                rc = 1
        return rc
    if args.cmd == "ingest":
        for it, p in zip(items, paths):
            why = _bad_text(it.get("description"), it.get("keywords"))
            if why:
                print(f"agent-nav: {p}: {why} — nothing changed", file=sys.stderr)
                return 2
        return ingest(repo, [{**it, "path": p} for it, p in zip(items, paths)])
    if args.cmd == "install-hook":
        return install_hook(repo)
    if args.cmd == "build":
        if not args.auto:
            print("agent-nav: a full build needs a description per file. Either do an "
                  "agent-driven build (`check` to list files, then `ingest` your JSON), "
                  "or pass --auto to describe via the `claude` CLI.", file=sys.stderr)
            return 2
        return build(repo)
    if args.cmd == "update":
        kws = [k for k in re.split(r"[,\s]+", args.keywords) if k]
        if args.description is not None:
            if len(paths) != 1:
                print("agent-nav: -d/--description sets one file per call — for a "
                      "batch, `ingest` a JSON file", file=sys.stderr)
                return 2
            why = _bad_text(args.description, kws)
            if why:
                print(f"agent-nav: {why} — nothing changed", file=sys.stderr)
                return 2
            return set_entry(repo, paths[0], args.description, kws, args.status)
        if args.keep:
            return keep(repo, paths)
        if args.auto:
            return update(repo, paths, describe_fn=describe, redescribe=args.redescribe)
        # no -d = the DROP command: "gone" = not in the git index (plus binaries /
        # the ledger file). Every orphan remedy must really drop — check --staged's D
        # (incl. `git rm --cached`, still on disk) and full check's neither-set rule
        # are both subsets of not-tracked. So an un-added file passed here IS
        # dropped; drop() names each row it removes. Paths are normalised above.
        tracked = set(tracked_files(repo))
        need = [p for p in paths if _indexable(repo, p, tracked)]
        gone = [p for p in paths if p not in need]
        if gone:
            drop(repo, gone)
        if need:
            print("agent-nav: these files need a description — re-run with "
                  '-d "<purpose>" -k "kw1,kw2" (one path per call; batch with '
                  "`ingest <file.json>`):", file=sys.stderr)
            for p in need:
                print(f"  {p}", file=sys.stderr)
            rows = {e["path"]: e for e in load(repo)}
            kept = [p for p in need if (rows.get(p, {}).get("description") or "").strip()]
            if kept:
                print("  ONLY if the purpose is unchanged, refresh the sha instead:\n"
                      f"    {_prog()} update "
                      f"{' '.join(shlex.quote(p) for p in kept)} --keep", file=sys.stderr)
            return 1
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
