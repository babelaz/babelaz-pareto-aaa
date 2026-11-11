"""The run folder's git (brief sections 3, 4 and 6): a NEW folder, commits with hooks disabled, reset, keep-best, proof.

Plain git through subprocess; no Hermes. Every call pins hooks, fsmonitor and signing off and sets an identity, so
nothing a worker plants runs and no prompt can hang. guard() keeps .git this folder's own: a worker that turns .git into
a link or a pointer to another repository could otherwise make our commits and resets land in the user's own repos.
"""

import subprocess
from pathlib import Path

SAFE = ["-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "commit.gpgsign=false",
        "-c", "user.name=Pareto AAA", "-c", "user.email=aaa@localhost"]
NOT_LOG = ":(exclude).aaa"  # the check's own log folder is never committed
PINNED = ("config", "HEAD", "refs", "objects", "index", "info")


class GitError(Exception):
    """Fail closed: the caller marks the round UNREVIEWED."""


def git(root, *args) -> str:
    r = subprocess.run(["git", *SAFE, *args], cwd=str(root), capture_output=True, text=True,
                       stdin=subprocess.DEVNULL)
    if r.returncode:
        raise GitError(f"git {args[0]} failed: {r.stderr.strip()[:200]}")
    return r.stdout.strip()


def init_run(root) -> tuple:
    """Create the NEW run folder (it must not exist) with its own git and a first empty commit.
    Returns (base sha, the .git/config bytes held in memory)."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=False)
    git(root, "init", "-q")
    git(root, "commit", "-q", "--no-verify", "--allow-empty", "-m", "Pareto AAA: start")
    return git(root, "rev-parse", "HEAD"), (root / ".git" / "config").read_bytes()


def guard(root, config: bytes) -> str | None:
    """.git must be this folder's own real directory, with nothing linked out and no shared commondir. Its config is
    put back from memory if it changed (a planted fsmonitor, filter or worktree setting). Returns a log note or None."""
    g = Path(root) / ".git"
    if g.is_symlink() or not g.is_dir() or (g / "commondir").exists() or any((g / p).is_symlink() for p in PINNED):
        raise GitError(".git is not this run folder's own directory")
    if (g / "config").read_bytes() != config:
        (g / "config").write_bytes(config)
        return "restored .git/config from memory"
    return None


def commit(root, msg: str) -> str:
    git(root, "add", "-A", "--", ".", NOT_LOG)
    git(root, "commit", "-q", "--no-verify", "--allow-empty", "-m", msg)
    return git(root, "rev-parse", "HEAD")


def reset(root, sha: str) -> None:
    """Back to a remembered commit: tracked files reset, anything new removed (the log folder stays)."""
    git(root, "reset", "-q", "--hard", sha)
    git(root, "clean", "-q", "-fdx", "-e", ".aaa")


def keep_best(root, sha: str, msg: str) -> str:
    """The working tree becomes the best round's tree, as a new commit on top, so no round's history is lost."""
    tree = git(root, "rev-parse", f"{sha}^{{tree}}")
    new = git(root, "commit-tree", tree, "-p", "HEAD", "-m", msg)
    reset(root, new)
    return new


def write_proof(root, png: bytes) -> str:
    """aaa-proof.png (the best round's evidence grid, from memory) goes into the project and is committed."""
    path = Path(root) / "aaa-proof.png"
    path.unlink(missing_ok=True)  # never write through a planted link
    path.write_bytes(png)
    return commit(root, "Pareto AAA: aaa-proof.png")
