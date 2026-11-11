"""ship.py: the run folder's git stays its own, keep-best keeps history, reset removes worker files."""

import subprocess

import pytest

from babelaz_pareto_aaa import ship


def _run(tmp_path):
    root = tmp_path / "run"
    sha, cfg = ship.init_run(root)
    return root, sha, cfg


def test_init_refuses_an_existing_folder(tmp_path):
    (tmp_path / "run").mkdir()
    with pytest.raises(FileExistsError):
        ship.init_run(tmp_path / "run")


def test_guard_restores_a_planted_config(tmp_path):
    root, _sha, cfg = _run(tmp_path)
    with open(root / ".git" / "config", "a") as fh:
        fh.write("[core]\n\tfsmonitor = touch /tmp/aaa-pwned\n")
    assert ship.guard(root, cfg) == "restored .git/config from memory"
    assert (root / ".git" / "config").read_bytes() == cfg
    assert ship.guard(root, cfg) is None


def test_guard_refuses_a_git_pointer_or_link_to_another_repo(tmp_path):
    other = tmp_path / "users-repo"
    subprocess.run(["git", "init", "-q", str(other)], check=True)
    root, _sha, cfg = _run(tmp_path)
    (root / ".git" / "commondir").write_text(str(other / ".git"))
    with pytest.raises(ship.GitError):
        ship.guard(root, cfg)
    (root / ".git" / "commondir").unlink()
    refs = root / ".git" / "refs"
    refs.rename(root / ".git" / "refs-old")
    refs.symlink_to(other / ".git" / "refs")
    with pytest.raises(ship.GitError):
        ship.guard(root, cfg)


def test_keep_best_restores_the_best_tree_and_keeps_history(tmp_path):
    root, base, _cfg = _run(tmp_path)
    (root / "game.js").write_text("best")
    best = ship.commit(root, "round 1")
    (root / "game.js").write_text("worse")
    (root / "extra.js").write_text("added later")
    later = ship.commit(root, "round 2")
    new = ship.keep_best(root, best, "keep best")
    assert (root / "game.js").read_text() == "best" and not (root / "extra.js").exists()
    log = ship.git(root, "log", "--format=%H", new)
    assert later in log and best in log and base in log


def test_reset_removes_new_files_but_keeps_the_log_folder(tmp_path):
    root, base, _cfg = _run(tmp_path)
    (root / ".aaa").mkdir()
    (root / ".aaa" / "run.log").write_text("log")
    (root / "half-done.js").write_text("stalled work")
    ship.reset(root, base)
    assert not (root / "half-done.js").exists() and (root / ".aaa" / "run.log").exists()
