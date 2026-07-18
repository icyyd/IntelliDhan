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
- The claim contract is v1.1 and requires the exact body
  `{"agent":"codex"}`. Other identities are rejected.
- Real orders are allowed only in the dedicated Robinhood Agentic account.
  Other connected accounts are read-only for this workflow.
- `OFF` is the default. `SHADOW` is the safe validation path. `SUPERVISED`
  requires explicit approval for every intent. Do not enable unattended
  `ARMED` execution without a new, explicit user instruction after reviewing
  the current account, MCP tools, policy, limits, and shadow results.
- Before each real order, use the connected MCP's pre-trade review tool and
  follow the fail-closed execution loop in doc 27 without omission.

## Decommission boundary

The former Claude execution contract is archived at
`docs/decommissioned/claude-robinhood-agent-contract.md` for historical reuse
only. It is not an active instruction source. Do not use its claim identity or
restore it without a separately reviewed migration, new credentials, shadow
validation, and explicit user authorization.

Read-only ingestion of old multi-brain daily-brief artifacts that happen to
carry a Claude label is not a broker integration and must remain isolated from
intent creation and execution.

## GitOps

The complete shared-repository workflow and deployment gates are mandatory in
doc 28. Every system-changing pass must update `README.md` in the same commit,
including its last-system-pass marker and any affected capability, setup,
deployment, safety, or document-index content. Context files and PR text do not
substitute for this update. Never merge, delete a branch, deploy, or change live
execution mode without explicit user authorization.
