"""Unit tests for integrity logic: scope rules, plugin hashes, and changes hidden by a worker's own commit."""

import subprocess

from babelaz_pareto_aaa import integrity as ig


def _git(repo, *args):
    cmd = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "core.hooksPath=/dev/null", *args]
    return subprocess.run(cmd, cwd=str(repo), check=True, capture_output=True, text=True).stdout.strip()


def test_scope_allows_everything_before_round_0_lock():
    assert ig.scope_violations(["anything.js"], None) == []


def test_scope_allows_locked_files_and_new_files_under_assets():
    assert ig.scope_violations(["index.html", "assets/new.png"], {"index.html"}) == []


def test_scope_flags_new_files_outside_assets():
    assert ig.scope_violations(["index.html", "evil.test.js"], {"index.html"}) == ["evil.test.js"]


def test_plugin_hash_mismatch_is_detected():
    assert ig.plugin_hashes_match({"a": "1"}, {"a": "1"})
    assert not ig.plugin_hashes_match({"a": "1"}, {"a": "2"})


def test_tree_files_skip_git_and_log_folder(tmp_path):
    (tmp_path / "index.html").write_text("x")
    (tmp_path / ".aaa").mkdir()
    (tmp_path / ".aaa" / "log.json").write_text("{}")
    _git(tmp_path, "init", "-q")
    assert ig.tree_files(tmp_path) == ["index.html"]


def test_worker_commit_does_not_hide_changes_from_the_remembered_sha(tmp_path):
    (tmp_path / "index.html").write_text("a")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "--no-verify", "-m", "base")
    base = _git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "index.html").write_text("b")
    (tmp_path / "evil.test.js").write_text("x")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "--no-verify", "-m", "worker")
    changed = ig.changed_files(tmp_path, base)
    assert "index.html" in changed and "evil.test.js" in changed
    assert ig.scope_violations(changed, {"index.html"}) == ["evil.test.js"]


def test_restore_reverts_tracked_files_and_removes_new_ones(tmp_path):
    (tmp_path / "index.html").write_text("a")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "--no-verify", "-m", "base")
    base = _git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "index.html").write_text("changed")
    (tmp_path / "new.js").write_text("new")
    ig.restore(tmp_path, base, "index.html")
    ig.restore(tmp_path, base, "new.js")
    assert (tmp_path / "index.html").read_text() == "a"
    assert not (tmp_path / "new.js").exists()


def test_planted_link_on_the_locked_smoke_is_replaced_never_written_through(tmp_path, monkeypatch):
    from babelaz_pareto_aaa import check
    from babelaz_pareto_aaa import status as st

    run, outside = tmp_path / "run", tmp_path / "outside.txt"
    run.mkdir()
    outside.write_text("user's own file")
    (run / "aaa.smoke.js").write_text("smoke")
    _git(run, "init", "-q")
    _git(run, "add", "-A")
    _git(run, "commit", "-q", "--no-verify", "-m", "base")
    state = check.RunState(base_sha=_git(run, "rev-parse", "HEAD"), locked={"aaa.smoke.js"},
                           smoke_bytes=b"smoke", smoke_sha=ig.sha256(b"smoke"))
    (run / "aaa.smoke.js").unlink()
    (run / "aaa.smoke.js").symlink_to(outside)  # the worker's move after the lock

    def stop(*_a):
        raise check.CaptureFailed("stop after the restore")

    monkeypatch.setattr(check, "_measure", stop)
    check._round(run, tmp_path / "out", "brick", state, st.Round(n=1), None, None)
    assert outside.read_text() == "user's own file"
    assert not (run / "aaa.smoke.js").is_symlink() and (run / "aaa.smoke.js").read_bytes() == b"smoke"


def test_links_are_found_and_restore_removes_folder_and_broken_links(tmp_path):
    """Review 1, fix 4: a worker's links (to a file, a folder, or nothing) are listed, and restore() removes each one
    without touching what it points at."""
    outside = tmp_path / "outside"
    (outside / "dir").mkdir(parents=True)
    (outside / "s.env").write_text("SECRET")
    repo = tmp_path / "run"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "game.js").symlink_to(outside / "s.env")
    (repo / "lib").symlink_to(outside / "dir")
    (repo / "gone.js").symlink_to(outside / "missing")
    (repo / "real.js").write_text("ok")
    assert ig.links(repo) == ["game.js", "gone.js", "lib"]
    for rel in ("game.js", "gone.js", "lib"):
        ig.restore(repo, base, rel)
    assert ig.links(repo) == [] and (repo / "real.js").exists()
    assert (outside / "s.env").read_text() == "SECRET" and (outside / "dir").is_dir()


def test_check_removes_links_before_anything_is_served(tmp_path):
    """Review 1, fix 4: the check deletes every link in the run folder, in any round, before it reads or serves a file.
    No smoke file here, so the round stops at G1 before the browser starts."""
    from babelaz_pareto_aaa import check
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "s.env").write_text("SECRET")
    repo = tmp_path / "run"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "base")
    (repo / "index.html").symlink_to(outside / "s.env")
    (repo / "assets").symlink_to(outside)
    res = check.run_round(repo, tmp_path / "out", "brick", check.RunState(base_sha=_git(repo, "rev-parse", "HEAD")),
                          None, None)
    assert ig.links(repo) == [] and (outside / "s.env").read_text() == "SECRET"
    assert [f["id"] for f in res["round"].gate_fails] == ["G1"]


def test_pinned_engine_file_is_not_a_dependency_but_a_changed_copy_is(tmp_path, monkeypatch):
    """Brief issue 23 (DECIDED, option a): a slice file is skipped only when its SHA-256 is on the shipped list. One
    changed byte makes it an ordinary file again, so its URLs count. (The real npm files: evidence/M3/engine_pins_check.txt.)"""
    engine = b"/* @license see https://threejs.org/license */ export const REVISION = '187';\n"
    monkeypatch.setitem(ig.ENGINE_FILES, ig.sha256(engine), "test engine")
    (tmp_path / "three.module.js").write_bytes(engine)
    assert ig.external_refs(tmp_path) == []
    (tmp_path / "three.module.js").write_bytes(engine.replace(b"187", b"188"))
    assert ig.external_refs(tmp_path) == ["three.module.js: https://threejs.org/license"]


def test_the_shipped_engine_list_pins_one_version_of_each_engine():
    names = set(ig.ENGINE_FILES.values())
    assert {n.split(" ")[0] for n in names} == {"three@0.187.0", "pixi.js@8.22.0"}
    assert "three@0.187.0 build/three.module.js" in names and "three@0.187.0 build/three.core.js" in names


def test_after_the_lock_no_new_page_or_script_even_in_assets_and_no_new_engine(tmp_path, monkeypatch):
    """Review 2, fix 3a: a polish round can add assets, but not a new page or script there, and the engine-hash skip
    covers only files on the locked list."""
    locked = {"index.html", "game.js"}
    assert ig.scope_violations(["assets/hit.wav", "assets/three.module.js", "assets/x.mjs", "assets/menu.html"],
                               locked) == ["assets/three.module.js", "assets/x.mjs", "assets/menu.html"]
    engine = b"/* see https://threejs.org/license */\n"
    monkeypatch.setitem(ig.ENGINE_FILES, ig.sha256(engine), "test engine")
    (tmp_path / "three.module.js").write_bytes(engine)
    assert ig.external_refs(tmp_path, None) == []  # before the lock: round 0 may choose the engine
    assert ig.external_refs(tmp_path, {"three.module.js"}) == []  # chosen at round 0: on the locked list
    assert ig.external_refs(tmp_path, locked) == ["three.module.js: https://threejs.org/license"]  # added later


def test_review3_upper_case_suffix_and_engine_over_a_locked_script(tmp_path, monkeypatch):
    """Review 3, fixes 1 and 5: a new assets/lib.JS is out of scope, and a locked script later overwritten with a pinned
    engine file counts as a dependency (only paths that held an engine file at the lock are skipped)."""
    assert ig.scope_violations(["assets/lib.JS", "assets/Menu.HTML", "assets/hit.WAV"], {"index.html"}) == [
        "assets/lib.JS", "assets/Menu.HTML"]
    engine = b"/* see https://threejs.org/license */\n"
    monkeypatch.setitem(ig.ENGINE_FILES, ig.sha256(engine), "test engine")
    (tmp_path / "game.js").write_bytes(b"let x = 1;\n")
    (tmp_path / "three.module.js").write_bytes(engine)
    engines = ig.engine_paths(tmp_path, ["game.js", "three.module.js"])
    assert engines == {"three.module.js"}
    (tmp_path / "game.js").write_bytes(engine)  # a later round swaps a locked script for the engine file
    (tmp_path / "LIB.JS").write_bytes(b"fetch('https://cdn.example.com/x')\n")
    assert ig.external_refs(tmp_path, engines) == ["LIB.JS: https://cdn.example.com/x",
                                                   "game.js: https://threejs.org/license"]
