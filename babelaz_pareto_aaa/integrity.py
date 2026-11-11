"""Integrity: hashes, git scope diff, out-of-scope rules, external references. No I/O beyond the given folder, no Hermes.

Rules: docs/BRIEF.md section 6, step 0. Unit tests: tests/test_integrity.py.
"""

import hashlib
import os
import re
import subprocess
from pathlib import Path

IGNORED_PREFIXES = (".aaa/", ".git/")  # the check's own log folder and git metadata
TEXT_SUFFIXES = (".html", ".htm", ".js", ".mjs", ".css", ".json")
CODE_SUFFIXES = (".html", ".htm", ".js", ".mjs")  # pages and scripts: never new after the lock, not even in assets/
EXTERNAL_URL = re.compile(r"https?://(?!localhost|127\.0\.0\.1|www\.w3\.org)[^\s\"'<>)]+")
# Pinned engine files (brief section 2; Brief issue 23, DECIDED: option a). The unmodified npm files, by SHA-256: their
# URLs sit in comments and strings, so they are not new dependencies. A file with any other hash is counted as usual.
# Taken Nov 7 2026 from `npm pack` (npm checked each tarball against the registry's sha512).
ENGINE_FILES = {
    "104f56145899722094556490d787aae0617cc2fc3382f31df02d724ea1aee341": "three@0.187.0 build/three.module.js",
    "613e370f76638fdef83926642bf256dc9615684922de1ce1f63a8583e5d78578": "three@0.187.0 build/three.core.js",
    "eafead3154ec06093f3108bddceab360f048a6fcebd7c085d5aae0ab5404bd25": "three@0.187.0 build/three.webgpu.js",
    "ec7ebc386f04be2ed99728d38cf4510bf9234579f2393b97dd8afc2070ad191c": "three@0.187.0 build/three.webgpu.nodes.js",
    "d8f3cdb8b85b799a5631bd9a8d77e185d88ce1eb1bb051f48b332728a5423e3f": "three@0.187.0 build/three.tsl.js",
    "66257bc46776898bf8c54daaed402ed8f9ac109379c47b2dfa8aa048544d2953": "pixi.js@8.22.0 dist/pixi.min.mjs",
    "8c7c745f667a9b352574044d6ce8d8e76705a6932c79b6bcb53db34a5e22ea76": "pixi.js@8.22.0 dist/pixi.mjs",
    "06d9ef9823e743518793083c296d801e752db128cb1f519fbabe37e1259567ea": "pixi.js@8.22.0 dist/pixi.min.js",
    "27bcdad5c17f86a95832670a27242a250f98b093231538e3c6f66dc20ff5de49": "pixi.js@8.22.0 dist/pixi.js",
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def tree_files(root) -> list:
    """Every file under root, as sorted POSIX paths, minus the ignored prefixes."""
    root = Path(root)
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for name in filenames:
            rel = (Path(dirpath) / name).relative_to(root).as_posix()
            if not rel.startswith(IGNORED_PREFIXES):
                out.append(rel)
    return sorted(out)


def file_hashes(root, rels) -> dict:
    root = Path(root)
    return {r: sha256((root / r).read_bytes()) for r in rels if (root / r).is_file()}


def tree_hash(root) -> str:
    """Hash of the whole working tree. Keys the judge cache (an unchanged tree reuses the result)."""
    hashes = file_hashes(root, tree_files(root))
    return sha256("\n".join(f"{k} {v}" for k, v in sorted(hashes.items())).encode())


def plugin_hashes_match(expected: dict, actual: dict) -> bool:
    return expected == actual


def _git(root, *args) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(root), capture_output=True, text=True, check=False
    )


def commit_exists(root, sha: str) -> bool:
    return _git(root, "cat-file", "-e", f"{sha}^{{commit}}").returncode == 0


def changed_files(root, base_sha: str) -> list:
    """Working tree against the remembered commit, not HEAD, so a worker's own commit hides nothing.
    Includes untracked files, and ignored files too (no --exclude-standard)."""
    tracked = _git(root, "diff", "--name-only", base_sha, "--").stdout.split()
    untracked = _git(root, "ls-files", "--others").stdout.split()
    return sorted({p for p in tracked + untracked if not p.startswith(IGNORED_PREFIXES)})


def scope_violations(changed, locked) -> list:
    """After round 0 the file list is locked. Later rounds may edit locked files and add new files only under assets/,
    and never a new page or script there (brief section 6: no new modules; section 2: polish adds no library).
    locked=None (before round 0) allows everything."""
    if locked is None:
        return []
    return [p for p in changed if p not in locked and (not p.startswith("assets/") or p.lower().endswith(CODE_SUFFIXES))]


def restore(root, base_sha: str, rel: str) -> None:
    """Put an out-of-scope path back as it was at the remembered commit (or remove it if it is new)."""
    root = Path(root)
    if _git(root, "cat-file", "-e", f"{base_sha}:{rel}").returncode == 0:
        _git(root, "checkout", base_sha, "--", rel)
    elif (root / rel).is_symlink() or (root / rel).is_file():  # a link to a folder, or a broken one, too
        (root / rel).unlink()


def links(root) -> list:
    """Every symbolic link under root (to files or folders, broken or not), outside .git. A slice never needs one, and
    a link would let the check serve or read files from outside the run folder (review 1, fix 4)."""
    root, out = Path(root), []
    for dirpath, dirnames, filenames in os.walk(root):  # does not descend into linked folders
        dirnames[:] = [d for d in dirnames if d != ".git"]
        out += [(Path(dirpath) / n).relative_to(root).as_posix() for n in dirnames + filenames
                if (Path(dirpath) / n).is_symlink()]
    return sorted(out)


def engine_paths(root, rels) -> set:
    """The paths among rels that hold an unmodified pinned engine file. Recorded once, at the smoke lock (review 3, fix 5)."""
    return {r for r, h in file_hashes(root, rels).items() if h in ENGINE_FILES}


def external_refs(root, engines=None) -> list:
    """Any http(s) URL in the slice's text files counts as a new runtime dependency (brief gate: 0 new runtime dependencies).
    A pinned engine file is skipped only before the lock (engines=None: round 0 chooses it), or at a path that held a
    pinned engine file when the lock passed. A locked script later overwritten with an engine file is counted."""
    root = Path(root)
    found = []
    for rel in tree_files(root):
        if not rel.lower().endswith(TEXT_SUFFIXES):
            continue
        data = (root / rel).read_bytes()
        if sha256(data) in ENGINE_FILES and (engines is None or rel in engines):  # a pinned engine, chosen at round 0
            continue
        for url in EXTERNAL_URL.findall(data.decode("utf-8", errors="replace")):
            found.append(f"{rel}: {url}")
    if (root / "package.json").exists():
        found.append("package.json present")
    return found
