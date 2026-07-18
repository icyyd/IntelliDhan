"""Commit-level README maintenance policy tests."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "check_readme_updates", ROOT / "scripts/check_readme_updates.py"
)
assert SPEC and SPEC.loader
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def write(repo: Path, relative: str, content: str) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def commit(repo: Path, message: str, files: dict[str, str]) -> str:
    for relative, content in files.items():
        write(repo, relative, content)
    git(repo, "add", *files)
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-b", "main")
    git(tmp_path, "config", "user.name", "README Policy Test")
    git(tmp_path, "config", "user.email", "readme-policy@example.invalid")
    return tmp_path


def activate(repo: Path) -> str:
    return commit(
        repo,
        "activate policy",
        {
            "AGENTS.md": checker.POLICY,
            "README.md": "# Project\n",
        },
    )


@pytest.mark.parametrize(
    "path",
    [
        ".claude/launch.json",
        ".codex/config.toml",
        ".dockerignore",
        ".gitignore",
        "config/runtime.yaml",
        "deploy/app.yaml",
        "docs/01-architecture.md",
        "services/gateway/app.py",
        "web/index.html",
    ],
)
def test_system_path_scope_includes_active_surfaces(path: str):
    assert checker.is_system_path(path)


@pytest.mark.parametrize(
    "path",
    [
        "AGENT_CONTEXT.md",
        "docs/archive/old.md",
        "docs/decommissioned/old.md",
        "tests/test_only.py",
    ],
)
def test_system_path_scope_excludes_inactive_or_test_surfaces(path: str):
    assert not checker.is_system_path(path)


def test_bootstrap_base_without_policy_skips(repo: Path, capsys: pytest.CaptureFixture[str]):
    base = commit(repo, "base", {"AGENTS.md": "legacy instructions\n"})
    head = commit(repo, "system change", {"services/app.py": "changed\n"})

    assert checker.main(["check", base, head], root=repo) == 0
    assert "bootstrap check skipped" in capsys.readouterr().out


def test_invalid_base_fails_closed(repo: Path, capsys: pytest.CaptureFixture[str]):
    head = commit(repo, "base", {"README.md": "# Project\n"})

    assert checker.main(["check", "does-not-exist", head], root=repo) == 2
    assert "commit is unavailable" in capsys.readouterr().err


def test_unrelated_history_fails_closed(repo: Path, capsys: pytest.CaptureFixture[str]):
    base = activate(repo)
    git(repo, "checkout", "--orphan", "replacement")
    git(repo, "rm", "-rf", ".")
    head = commit(repo, "replacement root", {"README.md": "# Replacement\n"})

    assert checker.main(["check", base, head], root=repo) == 2
    assert "base is not an ancestor" in capsys.readouterr().err


def test_regular_system_commit_requires_readme(
    repo: Path, capsys: pytest.CaptureFixture[str]
):
    base = activate(repo)
    head = commit(repo, "missing README", {"services/app.py": "changed\n"})

    assert checker.main(["check", base, head], root=repo) == 1
    error = capsys.readouterr().err
    assert head[:12] in error
    assert "services/app.py" in error


def test_regular_system_commit_with_readme_passes(repo: Path):
    base = activate(repo)
    head = commit(
        repo,
        "documented change",
        {"services/app.py": "changed\n", "README.md": "# Project\n\nUpdated.\n"},
    )

    assert checker.main(["check", base, head], root=repo) == 0


def test_system_file_rename_to_excluded_path_still_requires_readme(
    repo: Path, capsys: pytest.CaptureFixture[str]
):
    activate(repo)
    base = commit(
        repo,
        "documented service",
        {"services/app.py": "active\n", "README.md": "# Project\n\nService.\n"},
    )
    (repo / "notes").mkdir()
    git(repo, "mv", "services/app.py", "notes/app.py")
    git(repo, "commit", "-m", "move service out of system paths")
    head = git(repo, "rev-parse", "HEAD")

    assert checker.main(["check", base, head], root=repo) == 1
    error = capsys.readouterr().err
    assert "services/app.py" in error


def test_merge_history_is_checked(repo: Path):
    base = activate(repo)
    git(repo, "checkout", "-b", "feature")
    commit(
        repo,
        "documented feature",
        {"web/index.html": "changed\n", "README.md": "# Project\n\nFeature.\n"},
    )
    git(repo, "checkout", "main")
    commit(repo, "review note", {"AGENT_CONTEXT.md": "reviewed\n"})
    git(repo, "merge", "--no-ff", "feature", "-m", "merge feature")
    head = git(repo, "rev-parse", "HEAD")

    assert checker.main(["check", base, head], root=repo) == 0
