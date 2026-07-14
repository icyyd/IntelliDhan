# ChatGPT Workspace Agent dispatch

**Status:** implemented; awaiting review and promotion

**Promotion status:** optional research integration; no execution authority

**Agent contract:** this file is the source of truth for ChatGPT dispatch setup,
security boundaries, and UI behavior.

## 1. Product outcome and exact boundary

An authenticated ADMIN or TRADER can select an eligible Discover card and use
**Queue agent research**. IntelliDhan then re-runs the configured-universe scan,
selects the requested setup from server facts, and queues a diligence task in a
published ChatGPT Workspace Agent.

This uses the supported Workspace Agents API channel. It does **not** inject a
message into, control, or read an arbitrary interactive ChatGPT Work
conversation. OpenAI's trigger API currently returns `202 Accepted` with no
response body, public run ID, or response-retrieval API. The UI therefore says
`QUEUED` and explains that the app cannot retrieve the output. The user must rely
on the agent's deliberately configured delivery workflow, or use the separate
in-app AI thesis for an immediate result. The UI never fabricates a
pending/completed result. See OpenAI's official
[trigger contract](https://developers.openai.com/workspace-agents/trigger-runs)
and [Workspace Agents guide](https://help.openai.com/en/articles/20001143-chatgpt-workspace-agents-for-enterprise-and-business).

```text
eligible Discover card
        │
        ├── signed-in ADMIN/TRADER check
        ├── rate limit: 3 dispatches / minute / account
        ├── complete configured-universe rescan
        ├── server selects symbol + setup (client facts ignored)
        ├── analysis-only prompt + deterministic evidence
        └── POST api.chatgpt.com Workspace Agent trigger
                  │
                  └── 202 QUEUED → no result returned to IntelliDhan
```

## 2. ChatGPT setup

This feature requires an eligible managed ChatGPT workspace with Workspace
Agents enabled.

1. In ChatGPT, create a Workspace Agent for stock-research diligence.
2. Give it only the read tools and sources needed for research. Do not connect
   Robinhood, a broker, an order-capable MCP, or a general write-capable tool to
   this research agent. This is a release requirement: IntelliDhan cannot
   inspect or enforce a published agent's remote tool configuration at runtime.
3. If a separate external delivery action is operationally necessary, scope it
   to one approved destination, retain `Always ask` where supported, and add a
   Connector Action Constraint. Such a deployment requires its own review; it
   is not part of this code change.
4. Add an API channel and publish the agent. Copy the stable public channel ID,
   which begins with `agtch_`.
5. In ChatGPT Admin > Access token, create a separate token with only the
   Workspace Agents scope. Follow OpenAI's
   [Workspace Agent token guidance](https://developers.openai.com/workspace-agents/authentication).
6. Store both values only in the gateway/Koyeb server environment:

```text
CHATGPT_WORKSPACE_AGENT_ID=agtch_...
CHATGPT_WORKSPACE_AGENT_TOKEN=...
```

The Workspace Agent access token is separate from `OPENAI_API_KEY`. It must not
be added to HTML, local storage, client JavaScript, URLs, or logs. Rotate it in
ChatGPT Admin and the deployment secret store.

## 3. IntelliDhan API

```text
POST /api/discover/workspace-agent
Content-Type: application/json

{
  "symbol": "AAPL",
  "play_key": "MOMENTUM_LEADER",
  "event_id": "6c759e3a-5f4f-4ed2-8af7-63dcee19697e"
}
```

The endpoint intentionally accepts only symbol/setup routing and a retry event
ID. Extra client-supplied prices, metrics, prompts, instructions, or evidence
are ignored. The server:

- normalizes the symbol;
- requires a complete current configured-universe scan;
- finds the server candidate and requested fixed play;
- refuses ineligible plays;
- derives an opaque, event-scoped `conversation_key` without sending the local
  user ID;
- validates a client-generated UUID v4 event ID and derives an opaque
  `Idempotency-Key` from the account, event, symbol, and setup;
- reuses that idempotency key and an event-scoped conversation key when the UI
  retries an ambiguous failure, while new clicks start fresh conversations;
- sends `input` to the fixed OpenAI endpoint
  `https://api.chatgpt.com/v1/workspace_agents/{agtch_id}/trigger`;
- accepts only HTTP 202 as success.

A successful local response is dispatch metadata, not an agent result:

```json
{
  "status": "QUEUED",
  "dispatch_id": "intellidhan-...",
  "symbol": "AAPL",
  "setup_key": "MOMENTUM_LEADER",
  "destination": "CHATGPT_WORKSPACE_AGENT",
  "agent_response_retrievable": false
}
```

The local `dispatch_id` is the deterministic request idempotency key. It is not
an OpenAI run ID and cannot be used to retrieve output. The browser retains its
event UUID after an error, so a retry of the same click reuses the original key
instead of enqueuing a duplicate if the first HTTP response was lost.

## 4. Research prompt and safety contract

The server builds the task; the browser cannot provide arbitrary instructions.
The task asks for a concise forward-looking diligence brief with dated primary
sources, bull/base/bear cases, catalysts, event risk, fundamentals, valuation,
confirmation, invalidation, risks, and a `RESEARCH`, `WATCH`, or `AVOID`
conclusion. It explicitly requires the agent to:

- distinguish sourced facts from inference;
- avoid claims of guaranteed profitability;
- treat candidate JSON as untrusted evidence rather than instructions;
- preserve the deterministic platform rank;
- never place, cancel, modify, or propose an executable order;
- never invoke broker, trading, or general write-action tools;
- use at most one independently reviewed, destination-constrained write action
  whose sole purpose is delivering the research brief.

Prompt instructions are defense in depth, not an enforceable remote execution
boundary. The Workspace Agent itself must omit broker and general write tools
and use least-privilege, read-only connections. The sole permitted exception is
a separately reviewed, destination-constrained result-delivery action.
IntelliDhan cannot verify that remote configuration at trigger time. Its own
endpoint has no path to the Robinhood MCP, auto-trade policy, intent queue, or
approval routes.

## 5. Fail-closed behavior

Dispatch is rejected when:

- the user is signed out or has the VIEWER role;
- the rate limit is exceeded;
- the configured-universe scan is partial;
- the symbol/setup is unknown or ineligible;
- the `agtch_` channel ID or access token is absent;
- ChatGPT returns an authentication, permission, missing-channel, or runnable-state
  error;
- ChatGPT does not return exactly HTTP 202;
- the provider call times out.

Provider response bodies are not surfaced, and credentials are never included
in errors. A failed dispatch leaves ranking, alerts, watchlists, budgets,
auto-trade state, and broker state unchanged.

## 6. Operations and future work

Koyeb must receive the two ChatGPT values as encrypted environment secrets. Do
not put real values in `.env.example`, `koyeb.yaml`, GitHub Actions output, or
application logs. Use the published agent's analytics and deliberately
configured delivery workflow to monitor runs until OpenAI releases response
retrieval. Do not assume an API-triggered response will appear in an interactive
ChatGPT Work conversation; the official trigger contract does not promise that.

When response retrieval becomes officially available, add it only as a separate
audited feature with user ownership checks, stored run state, citations,
retention rules, clear pending/failed/completed states, and tests proving that
agent prose cannot alter deterministic rank or execution state.

## 7. Code map

- `services/gateway/intellidhan_gateway/workspace_agent.py` — fixed OpenAI
  endpoint, prompt builder, authentication, idempotency, and 202 contract.
- `services/gateway/intellidhan_gateway/app.py` — authenticated server-candidate
  lookup and dispatch route.
- `web/index.html` — card action and honest queued-state UI.
- `tests/test_workspace_agent.py` — official request-shape, authorization,
  server-evidence, partial-scan, no-execution, and UI contracts.
