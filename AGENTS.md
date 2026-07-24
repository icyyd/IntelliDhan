# IntelliDhan Codex + Robinhood Agent Contract

OpenAI Codex is the only active execution agent. Read
`docs/27-codex-robinhood-execution.md` and
`docs/28-platform-safety-and-data-integrity.md` in full before changing or
operating automation, intent, risk, broker, research-ranking, authentication,
data-integrity, or receipt code.

## Robinhood Trading MCP

Use only Robinhood's official Trading MCP over Streamable HTTP:

```text
https://agent.robinhood.com/mcp/trading
```

The trusted project config in `.codex/config.toml` declares the server without
credentials. Authenticate the local Codex host separately:

```bash
codex mcp login robinhood-trading
```

- Inspect the connected server's advertised tools and schemas at runtime.
  Never guess tool names or fields or use an unofficial Robinhood client.
- Robinhood credentials, cookies, tokens, account numbers, passwords, and MFA
  secrets never enter IntelliDhan, its prompts, logs, or version control.
- The claim contract is v2.0 and accepts only the `codex` agent identity plus a
  positive fresh underlying price and timezone-aware observation timestamp.
  The identity fragment remains exactly `{"agent":"codex"}`; the complete
  allowlisted body adds only `underlying_price` and `observed_at`. Other fields
  and identities are rejected.
- Real orders are allowed only in the dedicated Robinhood Agentic account.
  Other connected accounts are read-only for this workflow.
- There are only two operator modes: `SIMULATION` is the default and can only
  observe real quotes plus record hypothetical entries/exits; `LIVE` is
  time-limited and may stage real long-option orders only after reviewing the
  current account, MCP tools, policy, limits, and simulation results.
- Before each real order, use the connected MCP's pre-trade review tool and
  present the preview for explicit confirmation before placement. Follow the
  fail-closed execution loop in doc 27 without omission.

## Decommission boundary

The former Claude execution contract is archived at
`docs/decommissioned/claude-robinhood-agent-contract.md` for historical reuse
only. It is not an active instruction source. Do not use its claim identity or
restore it without a separately reviewed migration, new credentials, shadow
validation, and explicit user authorization.

Read-only ingestion of old multi-brain daily-brief artifacts that happen to
carry a Claude label is not a broker integration and must remain isolated from
intent creation and execution.

The Claude API is permitted only for server-side, research-only multi-brain
review under doc 15. `ANTHROPIC_API_KEY` never enters the browser, Claude Code,
Codex prompts, Robinhood MCP, execution intents, or receipts. Claude receives a
bounded public-research packet with no tools or MCP servers; its structured
review cannot alter deterministic specialists, posture, rank, sizing, policy,
or execution. A Claude API key is never a Robinhood execution credential.

## GitOps

The complete shared-repository workflow and deployment gates are mandatory in
doc 28. Every system-changing pass must update `README.md` in the same commit,
including its last-system-pass marker and any affected capability, setup,
deployment, safety, or document-index content. Context files and PR text do not
substitute for this update. Never merge, delete a branch, deploy, or change live
execution mode without explicit user authorization.
