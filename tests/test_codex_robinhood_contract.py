"""Codex-only Robinhood execution and decommission boundaries."""

from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def test_codex_project_mcp_is_official_and_credential_free():
    config = tomllib.loads((ROOT / ".codex/config.toml").read_text())
    server = config["mcp_servers"]["robinhood-trading"]

    assert server == {
        "url": "https://agent.robinhood.com/mcp/trading",
        "auth": "oauth",
        "enabled": True,
        "required": False,
        "default_tools_approval_mode": "writes",
    }
    lowered = (ROOT / ".codex/config.toml").read_text().lower()
    assert "token" not in lowered
    assert "password" not in lowered
    assert "http_headers" not in lowered


def test_active_robinhood_surfaces_have_no_retired_agent_identity():
    runtime_paths = [
        ".env.example",
        "config/autotrade.yaml",
        "docs/20-one-stop-terminal-gap-analysis.md",
        "docs/25-signal-terminal-redesign.md",
        "koyeb.yaml",
        "services/gateway/intellidhan_gateway/app.py",
        "services/gateway/intellidhan_gateway/autotrade.py",
        "tests/test_autotrade.py",
        "tests/test_autotrade_api.py",
        "web/index.html",
    ]
    for relative in runtime_paths:
        assert "claude" not in (ROOT / relative).read_text().lower()


def test_retired_contract_is_archived_not_active():
    assert not (ROOT / "CLAUDE.md").exists()
    archive = ROOT / "docs/decommissioned/claude-robinhood-agent-contract.md"
    assert archive.exists()
    text = archive.read_text()
    assert text.startswith("# DECOMMISSIONED")
    assert "not an active instruction source" in text


def test_agent_entrypoint_requires_codex_claim_and_safe_modes():
    text = (ROOT / "AGENTS.md").read_text()
    assert '{"agent":"codex"}' in text
    assert "`OFF` is the default" in text
    assert "`SHADOW` is the safe validation path" in text
    assert "dedicated Robinhood Agentic account" in text
    assert "Never guess tool names or fields" in text


def test_system_passes_require_same_commit_readme_maintenance():
    agents = (ROOT / "AGENTS.md").read_text()
    safety = (ROOT / "docs/28-platform-safety-and-data-integrity.md").read_text()
    readme = (ROOT / "README.md").read_text()

    required = "Every system-changing pass must update `README.md` in the same commit"
    assert required in agents
    assert "Every system-changing pass updates `README.md` in the same commit" in safety
    assert "## Repository change discipline" in readme
    assert "**Last system pass:**" in readme
