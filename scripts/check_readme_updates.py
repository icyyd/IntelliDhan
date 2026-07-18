#!/usr/bin/env python3
"""Require README.md in every system-changing commit after policy activation."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POLICY = "Every system-changing pass must update `README.md` in the same commit"
SYSTEM_PREFIXES = (
    ".claude/",
    ".codex/",
    ".github/",
    "config/",
    "deploy/",
    "docs/",
    "scripts/",
    "services/",
    "shared-schemas/",
    "web/",
)
EXCLUDED_PREFIXES = ("docs/archive/", "docs/decommissioned/")
SYSTEM_FILES = {
    ".dockerignore",
    ".env.example",
    ".gitignore",
    "AGENTS.md",
    "ARCHITECTURE.md",
    "Dockerfile",
    "GO-LIVE.md",
    "docker-compose.yml",
    "koyeb.yaml",
    "pyproject.toml",
}


def git(*args: str, root: Path = ROOT) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def require_commit(ref: str, *, root: Path) -> None:
    if not ref or set(ref) == {"0"}:
        raise ValueError(f"commit is unavailable: {ref or '<empty>'}")
    try:
        git("cat-file", "-e", f"{ref}^{{commit}}", root=root)
    except subprocess.CalledProcessError as exc:
        raise ValueError(f"commit is unavailable: {ref}") from exc


def policy_is_active(base: str, *, root: Path) -> bool:
    require_commit(base, root=root)
    try:
        git("cat-file", "-e", f"{base}:AGENTS.md", root=root)
    except subprocess.CalledProcessError:
        return False
    return POLICY in git("show", f"{base}:AGENTS.md", root=root)


def require_comparable_range(base: str, head: str, *, root: Path) -> None:
    try:
        git("merge-base", "--is-ancestor", base, head, root=root)
    except subprocess.CalledProcessError as exc:
        raise ValueError(f"base is not an ancestor of head: {base}..{head}") from exc


def changed_files(commit: str, *, root: Path) -> set[str]:
    ancestry = git("rev-list", "--parents", "-n", "1", commit, root=root).split()
    if len(ancestry) == 1:
        output = git(
            "diff-tree",
            "--root",
            "--no-commit-id",
            "--name-only",
            "-r",
            commit,
            root=root,
        )
    else:
        output = git("diff", "--name-only", ancestry[1], commit, root=root)
    return {line for line in output.splitlines() if line}


def is_system_path(path: str) -> bool:
    if path.startswith(EXCLUDED_PREFIXES):
        return False
    return path in SYSTEM_FILES or path.startswith(SYSTEM_PREFIXES)


def main(argv: list[str] | None = None, *, root: Path = ROOT) -> int:
    if argv is None:
        argv = sys.argv
    if len(argv) != 3:
        print("usage: check_readme_updates.py BASE HEAD", file=sys.stderr)
        return 2

    base, head = argv[1:]
    try:
        require_commit(head, root=root)
        active = policy_is_active(base, root=root)
        require_comparable_range(base, head, root=root)
    except ValueError as exc:
        print(f"README policy check cannot inspect history: {exc}", file=sys.stderr)
        return 2

    if not active:
        print("README same-commit policy is not active on the base; bootstrap check skipped")
        return 0

    violations: list[tuple[str, list[str]]] = []
    commits = git("rev-list", "--reverse", f"{base}..{head}", root=root).splitlines()
    for commit in commits:
        paths = changed_files(commit, root=root)
        system_paths = sorted(path for path in paths if is_system_path(path))
        if system_paths and "README.md" not in paths:
            violations.append((commit, system_paths))

    if not violations:
        print("README same-commit policy passed")
        return 0

    print("README.md is required in every system-changing commit:", file=sys.stderr)
    for commit, paths in violations:
        print(f"- {commit[:12]} changes {', '.join(paths)}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
