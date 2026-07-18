#!/usr/bin/env python3
"""Require README.md in every system-changing commit after policy activation."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POLICY = "Every system-changing pass must update `README.md` in the same commit"
SYSTEM_PREFIXES = (
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
SYSTEM_FILES = {
    ".env.example",
    "AGENTS.md",
    "ARCHITECTURE.md",
    "Dockerfile",
    "GO-LIVE.md",
    "docker-compose.yml",
    "koyeb.yaml",
    "pyproject.toml",
}


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def policy_is_active(base: str) -> bool:
    if not base or set(base) == {"0"}:
        return False
    try:
        return POLICY in git("show", f"{base}:AGENTS.md")
    except subprocess.CalledProcessError:
        return False


def changed_files(commit: str) -> set[str]:
    ancestry = git("rev-list", "--parents", "-n", "1", commit).split()
    if len(ancestry) == 1:
        output = git("diff-tree", "--root", "--no-commit-id", "--name-only", "-r", commit)
    else:
        output = git("diff", "--name-only", ancestry[1], commit)
    return {line for line in output.splitlines() if line}


def is_system_path(path: str) -> bool:
    return path in SYSTEM_FILES or path.startswith(SYSTEM_PREFIXES)


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: check_readme_updates.py BASE HEAD", file=sys.stderr)
        return 2

    base, head = sys.argv[1:]
    if not policy_is_active(base):
        print("README same-commit policy is not active on the base; bootstrap check skipped")
        return 0

    violations: list[tuple[str, list[str]]] = []
    commits = git("rev-list", "--reverse", f"{base}..{head}").splitlines()
    for commit in commits:
        paths = changed_files(commit)
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
