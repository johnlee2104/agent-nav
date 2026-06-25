from agent_nav import ledger


def test_save_load_roundtrip_sorted(tmp_repo):
    entries = [
        {"path": "z.py", "description": "z", "keywords": [], "sha": "1"},
        {"path": "a.py", "description": "a", "keywords": [], "sha": "2"},
    ]
    ledger.save(tmp_repo, entries)
    loaded = ledger.load(tmp_repo)
    assert [e["path"] for e in loaded] == ["a.py", "z.py"]  # sorted by path


def test_ledger_path_and_self_exclusion(tmp_repo):
    assert ledger.ledger_path(tmp_repo).as_posix().endswith(".agent-nav/ledger.jsonl")
    assert ledger._is_ledger_path(".agent-nav/ledger.jsonl") is True
    assert ledger._is_ledger_path("a.py") is False


# Task 2: Agent-supplied entry writes (ingest / set_entry / drop)
def test_ingest_upserts_indexable_only_and_sorts(tmp_repo):
    (tmp_repo / "b.py").write_text("x=1\n")
    import subprocess
    subprocess.run(["git", "add", "b.py"], cwd=tmp_repo, check=True)
    rc = ledger.ingest(tmp_repo, [
        {"path": "b.py", "description": "bee", "keywords": ["b"]},
        {"path": "a.py", "description": "ay", "keywords": ["a"]},
        {"path": "untracked.py", "description": "no", "keywords": []},  # skipped
    ])
    assert rc == 0
    paths = [e["path"] for e in ledger.load(tmp_repo)]
    assert paths == ["a.py", "b.py"]  # untracked skipped, sorted


def test_set_entry_writes_sha_and_status(tmp_repo):
    ledger.set_entry(tmp_repo, "a.py", "the a file", ["alpha"], status="superseded")
    e = {x["path"]: x for x in ledger.load(tmp_repo)}["a.py"]
    assert e["description"] == "the a file"
    assert e["status"] == "superseded"
    assert e["sha"] == ledger._sha(tmp_repo, "a.py")


def test_drop_removes_entry(tmp_repo):
    ledger.set_entry(tmp_repo, "a.py", "x", [])
    ledger.drop(tmp_repo, ["a.py"])
    assert ledger.load(tmp_repo) == []


# Task 3: Ranking (find)
def test_find_ranks_keyword_over_path_over_description(tmp_repo):
    import subprocess
    for name in ("auth.py", "login.py", "misc.py"):
        (tmp_repo / name).write_text("x=1\n")
        subprocess.run(["git", "add", name], cwd=tmp_repo, check=True)
    ledger.ingest(tmp_repo, [
        {"path": "auth.py", "description": "unrelated", "keywords": ["token"]},  # kw hit (3)
        {"path": "login.py", "description": "unrelated", "keywords": ["misc"]},   # no hit
        {"path": "misc.py", "description": "issues a token here", "keywords": []},# desc hit (1)
    ])
    ranked = ledger.find(tmp_repo, "token")
    assert ranked[0]["path"] == "auth.py"   # keyword hit ranks first
    assert {e["path"] for e in ranked} == {"auth.py", "misc.py"}  # login.py has no hit


def test_find_drops_stopwords_and_short_tokens(tmp_repo):
    assert ledger._terms("where is the on") == []         # all stop/short
    assert ledger._terms("validate auth tokens") == ["validate", "auth", "tokens"]


def test_find_respects_n_limit(tmp_repo):
    import subprocess
    for i in range(5):
        n = f"f{i}.py"
        (tmp_repo / n).write_text("x=1\n")
        subprocess.run(["git", "add", n], cwd=tmp_repo, check=True)
        ledger.set_entry(tmp_repo, n, "shared thing", ["shared"])
    assert len(ledger.find(tmp_repo, "shared", n=2)) == 2


# Task 4: Drift detection (check)
def test_check_reports_missing_and_stale_and_orphaned(tmp_repo):
    import subprocess
    # a.py committed in fixture; describe it so it's current
    ledger.set_entry(tmp_repo, "a.py", "the a file", ["a"])
    # new tracked text file, no entry -> missing
    (tmp_repo / "new.py").write_text("y=2\n")
    subprocess.run(["git", "add", "new.py"], cwd=tmp_repo, check=True)
    # mutate a.py after its description -> stale
    (tmp_repo / "a.py").write_text("print('a changed')\n")
    # orphaned: entry for a path that does not exist on disk/tracking
    ledger.save(tmp_repo, ledger.load(tmp_repo) + [
        {"path": "gone.py", "description": "x", "keywords": [], "sha": "deadbeef"}])

    drift = ledger.check(tmp_repo)
    assert "new.py" in drift["missing"]
    assert "a.py" in drift["stale"]
    assert "gone.py" in drift["orphaned"]


def test_check_staged_scopes_to_index(tmp_repo):
    import subprocess
    (tmp_repo / "s.py").write_text("z=3\n")
    subprocess.run(["git", "add", "s.py"], cwd=tmp_repo, check=True)
    drift = ledger.check(tmp_repo, staged=True)
    assert "s.py" in drift["missing"]      # staged add with no entry


# Task 5: install-hook + program-aware drift printer
import subprocess, os


def test_install_hook_writes_executable_hook(tmp_repo):
    rc = ledger.install_hook(tmp_repo)
    assert rc == 0
    hook = tmp_repo / ".git" / "hooks" / "pre-commit"
    assert hook.exists()
    assert os.access(hook, os.X_OK)
    body = hook.read_text()
    assert "check --staged" in body


def test_hook_blocks_commit_when_stale(tmp_repo):
    # describe a.py current, install hook, then change a.py and try to commit
    ledger.set_entry(tmp_repo, "a.py", "the a file", ["a"])
    subprocess.run(["git", "add", ".agent-nav/ledger.jsonl"], cwd=tmp_repo, check=True)
    subprocess.run(["git", "commit", "-qm", "ledger"], cwd=tmp_repo, check=True)
    ledger.install_hook(tmp_repo)
    (tmp_repo / "a.py").write_text("print('changed')\n")
    subprocess.run(["git", "add", "a.py"], cwd=tmp_repo, check=True)
    r = subprocess.run(["git", "commit", "-qm", "should block"],
                       cwd=tmp_repo, capture_output=True, text=True)
    assert r.returncode != 0   # hook blocked the commit


def test_print_drift_returns_zero_when_clean(capsys):
    rc = ledger._print_drift({"stale": [], "missing": [], "orphaned": []})
    assert rc == 0
    assert "up to date" in capsys.readouterr().out


# Task 6: CLI wiring + --auto + binary/oversize skip
def test_cli_update_then_find(tmp_repo, capsys, monkeypatch):
    monkeypatch.chdir(tmp_repo)
    assert ledger.main(["update", "a.py", "-d", "the a file", "-k", "alpha,beta"]) == 0
    capsys.readouterr()
    assert ledger.main(["find", "alpha"]) == 0
    out = capsys.readouterr().out
    assert "a.py" in out and "the a file" in out


def test_cli_check_exit_codes(tmp_repo, monkeypatch):
    monkeypatch.chdir(tmp_repo)
    # a.py has no entry yet -> drift -> exit 1
    assert ledger.main(["check"]) == 1
    ledger.main(["update", "a.py", "-d", "x", "-k", "a"])
    assert ledger.main(["check"]) == 0


def test_cli_install_hook(tmp_repo, monkeypatch):
    monkeypatch.chdir(tmp_repo)
    assert ledger.main(["install-hook"]) == 0
    assert (tmp_repo / ".git" / "hooks" / "pre-commit").exists()


def test_update_auto_uses_injected_describe_fn(tmp_repo):
    calls = {}
    def fake_describe(path, content):
        calls[path] = True
        return {"description": "auto desc", "keywords": ["auto"]}
    rc = ledger.update(tmp_repo, ["a.py"], describe_fn=fake_describe)
    assert rc == 0 and calls == {"a.py": True}
    e = {x["path"]: x for x in ledger.load(tmp_repo)}["a.py"]
    assert e["description"] == "auto desc"


def test_binary_and_oversize_files_are_not_indexable(tmp_repo):
    import subprocess
    (tmp_repo / "img.bin").write_bytes(b"\x00\x01\x02\xff")
    big = "x" * (ledger.MAX_BYTES + 1)
    (tmp_repo / "big.py").write_text(big)
    for n in ("img.bin", "big.py"):
        subprocess.run(["git", "add", n], cwd=tmp_repo, check=True)
    tracked = set(ledger.tracked_files(tmp_repo))
    assert ledger._indexable(tmp_repo, "img.bin", tracked) is False
    assert ledger._indexable(tmp_repo, "big.py", tracked) is False
