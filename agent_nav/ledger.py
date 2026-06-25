#!/usr/bin/env python3
"""Lean codebase-navigation ledger — find / update / ingest / check.

The ledger is one JSON object per line in .agent-nav/ledger.jsonl, sorted by
path:  {"path": ..., "description": ..., "keywords": [...], "sha": ...}

By default the tool makes NO model call: the agent already reading your code
supplies the descriptions, so nothing is billed beyond the session it's already in.

  update <path> -d "purpose" -k "kw1,kw2"   set one entry (you write the text)
  update <paths...>                          drop entries for deleted/renamed files
  ingest [file]                              bulk-set from JSON [{path,description,
                                             keywords}, ...] (stdin if no file)
  find  "<query>"                            rank entries by purpose, print matches
  check  [--staged]                          report drift; exit non-zero if any. The
                                             pre-commit hook runs `check --staged`.

  --auto (on update/build)                   opt in to describing via the `claude`
                                             CLI instead of supplying text yourself.

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
MODEL = "claude-haiku-4-5"
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
    try:
        return hashlib.sha256((pathlib.Path(repo) / rel).read_bytes()).hexdigest()
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


def update(repo, paths, describe_fn=describe) -> int:
    """--auto path: (re)describe via describe_fn, dropping gone/ignored paths."""
    by_path = {e["path"]: e for e in load(repo)}
    tracked = set(tracked_files(repo))
    for rel in paths:
        if not _indexable(repo, rel, tracked):
            by_path.pop(rel, None)  # gone / ignored → drop the entry
            continue
        by_path[rel] = _entry(repo, rel, describe_fn)
    save(repo, list(by_path.values()))
    return 0


# --------------------------------------------------------------------------- #
# agent-supplied entries (the default — no model call)
# --------------------------------------------------------------------------- #
def _indexable(repo, rel, tracked) -> bool:
    return rel in tracked and not _is_ledger_path(rel) and _read_text(repo, rel) is not None


_STATUS = ("current", "superseded", "archived", "draft")


def _mk_entry(repo, rel, description, keywords, status=None) -> dict:
    # `status` is an optional lifecycle/authority axis, kept separate from
    # `description` so it never pollutes purpose-ranking in find(). Sparse:
    # omitted entirely for plain code; set only on docs/runbooks that need it.
    e = {"path": rel,
         "description": (description or "").strip()[:200],
         "keywords": [str(k).strip().lower() for k in (keywords or []) if str(k).strip()][:6]}
    s = (status or "").strip().lower()
    if s:
        e["status"] = s
    e["sha"] = _sha(repo, rel)
    return e


def set_entry(repo, rel, description, keywords, status=None) -> int:
    """Upsert one entry from a caller-supplied description (computes the sha)."""
    if not _indexable(repo, rel, set(tracked_files(repo))):
        print(f"agent-nav: {rel} is not an indexable tracked file — nothing set", file=sys.stderr)
        return 1
    by_path = {e["path"]: e for e in load(repo)}
    by_path[rel] = _mk_entry(repo, rel, description, keywords, status)
    save(repo, list(by_path.values()))
    return 0


def drop(repo, paths) -> int:
    """Remove entries for the given paths (deleted/renamed-away files)."""
    by_path = {e["path"]: e for e in load(repo)}
    for rel in paths:
        by_path.pop(rel, None)
    save(repo, list(by_path.values()))
    return 0


def ingest(repo, items) -> int:
    """Bulk-upsert caller-supplied entries: [{path, description, keywords, status?}, ...]."""
    tracked = set(tracked_files(repo))
    by_path = {e["path"]: e for e in load(repo)}
    n = 0
    for it in items:
        rel = it["path"]
        if not _indexable(repo, rel, tracked):
            continue  # skip untracked/ignored/binary paths
        by_path[rel] = _mk_entry(repo, rel, it.get("description", ""),
                                 it.get("keywords", []), it.get("status"))
        n += 1
    save(repo, list(by_path.values()))
    print(f"agent-nav: ingested {n} entries", file=sys.stderr)
    return 0


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


def check(repo, staged=False) -> dict:
    """Return drift buckets. staged=True scopes to the git index (for the hook)."""
    entries = {e["path"]: e for e in load(repo)}
    drift = {"stale": [], "missing": [], "orphaned": []}
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
    else:
        tracked = set(tracked_files(repo))
        for rel in sorted(tracked):
            _flag(repo, rel, entries, drift)
        for path in sorted(entries):
            if path not in tracked and not _is_ledger_path(path):
                drift["orphaned"].append(path)
    return drift


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _prog() -> str:
    """Best-effort command prefix for help text / the hook, valid from repo root."""
    a0 = sys.argv[0] or ""
    base = os.path.basename(a0)
    if base in ("agent-nav", "agent_nav"):
        return "agent-nav"
    if base == "__main__.py":
        return "python3 -m agent_nav"
    if a0:
        return f"python3 {os.path.abspath(a0)}"
    return "agent-nav"


def _hook_script(prog) -> str:
    return (
        "#!/bin/sh\n"
        "# agent-nav freshness hook — blocks commits that leave the index stale.\n"
        "# Reinstall with `agent-nav install-hook` (or the vendored equivalent).\n"
        f"exec {prog} check --staged\n"
    )


def install_hook(repo) -> int:
    hooks = subprocess.run(
        ["git", "rev-parse", "--git-path", "hooks"], cwd=str(repo),
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    dest = pathlib.Path(repo) / hooks / "pre-commit"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(_hook_script(_prog()))
    dest.chmod(0o755)
    print(f"agent-nav: installed pre-commit hook at {dest}")
    return 0


def _print_drift(drift) -> int:
    if sum(len(v) for v in drift.values()) == 0:
        print("agent-nav: up to date")
        return 0
    for rel in drift["stale"]:
        print(f"stale (content changed since its description was written): {rel}")
    for rel in drift["missing"]:
        print(f"missing (no ledger entry): {rel}")
    for rel in drift["orphaned"]:
        print(f"orphaned (file gone, entry stale): {rel}")
    print()
    print("Index is out of date. You write the descriptions (no model call needed):")
    prog = _prog()
    for rel in sorted(set(drift["stale"] + drift["missing"])):
        print(f'    {prog} update {shlex.quote(rel)} '
              '-d "<one-line purpose>" -k "kw1,kw2,kw3"')
    if drift["orphaned"]:
        gone = " ".join(shlex.quote(p) for p in sorted(set(drift["orphaned"])))
        print(f"    {prog} update {gone}      # drop gone files")
    print(f"    git add {LEDGER_REL}")
    print("(automated alternative: add --auto to describe via the `claude` CLI.)")
    return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="agent-nav", description=__doc__)
    ap.add_argument("--repo", default=".")
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("find"); f.add_argument("query"); f.add_argument("-n", type=int, default=8)
    c = sub.add_parser("check"); c.add_argument("--staged", action="store_true")
    ig = sub.add_parser("ingest")
    ig.add_argument("file", nargs="?", help="JSON [{path,description,keywords}]; default stdin")
    u = sub.add_parser("update")
    u.add_argument("paths", nargs="+")
    u.add_argument("-d", "--description", help="one-line purpose for a single file")
    u.add_argument("-k", "--keywords", default="", help="comma/space separated keywords")
    u.add_argument("-s", "--status", default=None,
                   help=f"optional lifecycle/authority tag {_STATUS} (docs/runbooks)")
    u.add_argument("--auto", action="store_true",
                   help="describe via the `claude` CLI instead of supplying -d "
                        "(uses your Claude Code login; may count toward API/SDK usage)")
    b = sub.add_parser("build")
    b.add_argument("--auto", action="store_true", help="describe every file via the `claude` CLI")
    sub.add_parser("install-hook")
    args = ap.parse_args(argv)

    if args.cmd == "find":
        for e in find(args.repo, args.query, n=args.n):
            st = e.get("status")
            tag = f"  [{st}]" if st and st != "current" else ""
            print(f"{e['path']}\t{e['description']}{tag}")
        return 0
    if args.cmd == "check":
        return _print_drift(check(args.repo, staged=args.staged))
    if args.cmd == "ingest":
        raw = pathlib.Path(args.file).read_text() if args.file else sys.stdin.read()
        return ingest(args.repo, json.loads(raw))
    if args.cmd == "install-hook":
        return install_hook(args.repo)
    if args.cmd == "build":
        if not args.auto:
            print("agent-nav: a full build needs a description per file. Either do an "
                  "agent-driven build (`check` to list files, then `ingest` your JSON), "
                  "or pass --auto to describe via the `claude` CLI.", file=sys.stderr)
            return 2
        return build(args.repo)
    if args.cmd == "update":
        kws = [k for k in re.split(r"[,\s]+", args.keywords) if k]
        if args.description is not None:
            if len(args.paths) != 1:
                print("agent-nav: -d/--description sets one file at a time", file=sys.stderr)
                return 2
            return set_entry(args.repo, args.paths[0], args.description, kws, args.status)
        if args.auto:
            return update(args.repo, args.paths, describe_fn=describe)
        # no -d, no --auto: drop gone files; require a description for files that remain
        tracked = set(tracked_files(args.repo))
        need = [p for p in args.paths if _indexable(args.repo, p, tracked)]
        gone = [p for p in args.paths if p not in need]
        if gone:
            drop(args.repo, gone)
        if need:
            print("agent-nav: these files need a description — re-run one at a time with "
                  '-d "<purpose>" -k "kw1,kw2" (or pass --auto):', file=sys.stderr)
            for p in need:
                print(f"  {p}", file=sys.stderr)
            return 1
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
