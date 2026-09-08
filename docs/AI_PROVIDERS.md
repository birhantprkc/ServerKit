# AI provider connections

In **Settings → AI Assistant**, add a named connection, choose a provider, fill
in its configuration, and choose a default model. **Discover models** uses the
current form values. **Browse models** opens a searchable picker; manual model
entry remains available for gateways and providers without discovery. **Save connection** stores credentials encrypted. The
first connection becomes the default automatically; another connection can be
made default when saving it. The **Enable AI assistant** switch saves immediately
and refreshes the chat UI. Disabling blocks new messages in both chat endpoints
while retaining conversation history. Limits and protections have their own Save
action; saving a connection preserves unsaved changes to those settings.

New chats offer a task, model role, and optional connection/model override.
The selected connection and model are saved with the conversation. Changing
the panel default does not reroute existing chats. Configured fallback or budget
degradation may switch the saved binding before a turn produces output.
You can rotate a connection's credentials, but
changing a provider or endpoint used by existing chats requires a new connection.
Delete those chats and remove task/routing/fallback references before deleting
their connection, and select another default before deleting the current default.

## Native providers and gateways

The catalog comes from the installed Prompture chat-provider descriptors, with
aliases deduplicated. Anthropic/Claude, OpenAI, Google, Azure, Bedrock, Mistral,
and other registered chat drivers appear together. Providers needing optional
Python dependencies are labeled in the selector. The backend requirements include
the OpenAI and Anthropic SDKs; other providers may require their SDKs to be
installed in the same backend environment before they become available.

For **OmniRoute**, select **OpenAI-Compatible**, set the API base URL to your
backend-reachable OmniRoute address (for example `http://omniroute:20128/v1` on a
shared Docker network), and enter the gateway key if authentication is enabled.
Enter the exact model ID or routing alias from that instance. Slashes are
preserved; ServerKit does not add an upstream-provider prefix. The endpoint preset
selector also exposes Prompture's curated compatible-provider URLs. Prompture Hub
has its own friendly entry and uses the same connection mechanism.

`localhost` means the machine or container running the ServerKit backend, not
the browser. Endpoints accept HTTP or HTTPS but cannot embed credentials, query
strings, or fragments. When changing an endpoint, re-enter credentials; ServerKit
does not forward the previous endpoint's saved key to the new address.

## Testing and capabilities

For compatible endpoints and OpenAI, **Test connection** makes a small chat
completion request, which may consume tokens. For other native providers, it
verifies model discovery and checks that the requested model is returned. An
unsupported or failed discovery reports an error, never a successful connection
based only on constructing a driver. Discovery does not prove streaming or tool
support; these depend on the provider and model. Error responses omit raw SDK
errors and credentials.

ServerKit pins Prompture 1.11.0 and uses its native OpenAI-compatible driver for
custom gateways, Prompture Hub, and LM Studio. That driver handles text streaming,
streamed tool calls, and ordinary tool calls without the OpenAI SDK. Explicit
model IDs are preserved, and unauthenticated gateways omit the Authorization
header even if a process-level compatible API key is set. Native Anthropic's
installed driver does not accept an endpoint argument: ServerKit refuses a
conflicting process-level `ANTHROPIC_BASE_URL` override instead of silently using
it. Vertex Gemini requires an explicit API key; Vertex Claude requires a project,
location, and access token. Ambient cloud credentials are not a connection option.

Existing assistant RBAC, redaction, and write-action confirmations remain active.
The picker includes catalog prices per million input/output tokens, context and
output limits, and tools, vision, reasoning, and structured-output capabilities.
Capability filters show only confirmed support. Missing metadata stays unknown.
Gateway aliases are not assigned an upstream model's price or capabilities.
Discovering a saved connection caches its nonsecret catalog; changing its
configuration clears that cache. Manual IDs remain supported.

## Task models and execution

**Task models and behavior** assigns a saved connection and exact model ID to
Utility, Standard, and Advanced. Standard inherits the panel default; Utility
and Advanced inherit Standard when unset. The UI shows the inherited binding.
Prompture's `ModelResolver` resolves these slots before execution.

| Task | Default role | Runtime behavior |
| --- | --- | --- |
| Chat | Standard | Existing assistant tools and confirmations, or simple text mode |
| Summarize | Utility | Summarizes supplied text and attached context without tools |
| Extract structured facts | Utility | Prompture validates an object with `summary`, `facts`, and `warnings`; no tools |
| Read-only diagnosis | Advanced | Only tools marked read-only and authorized for the caller; bounded tool rounds |

An explicit connection/model override wins, followed by an explicit role.
Without either, enabled automatic routing uses the approved pool; otherwise
the task's role is used. Existing conversations retain their task and binding.
Advanced models never grant additional permissions.

Routing uses Prompture's cost-optimized, balanced, quality-first, or fast
strategy, with discovery restricted to administrator-approved saved bindings.
Candidate capability tiers guide its heuristics; “fast” is not a measured latency
guarantee. The routing preview runs against the unsaved draft without making
a model call. Each run records the selection strategy and explanation.

Fallback is opt-in and tries the ordered list with each candidate's own endpoint
and credentials. It only runs before any provider output has been delivered.
The unused legacy `fallback_models` settings field is rejected; fallback
candidates must include a saved connection ID in `/ai/management`.
Once output or a tool response starts, a failure ends that turn; actions are
never replayed on another connection. The adapter avoids Prompture's ambient
credential fallback path. The actual selected model appears in run reporting.

## Generation and spending controls

Generation controls are applied to actual conversations: maximum output tokens
per response, lifetime conversation tokens, history messages, tool rounds, and
tool-result characters. Temperature is forwarded only with catalog-confirmed
support; reasoning effort is forwarded only to supported OpenAI reasoning models.
Unsupported settings retain provider defaults. Arbitrary driver JSON and detached
tool timeouts are not exposed.

The conversation cost ceiling defaults to $0.50; zero disables it. Select:

- **Stop at the limit:** check cumulative cost/tokens before every provider call.
- **Warn and continue:** retain usage and show the exceeded allowance in chat.
- **Use a cheaper model near the limit, then stop:** at 80% of the cost allowance,
  switch before a turn to the first configured fallback with lower known combined
  input/output rates, then enforce the hard ceiling. Unknown prices are not compared.

Monthly allowances can apply panel-wide, per user, and per workspace. Workspace
attribution is captured from the active workspace when a chat starts and access
is rechecked on resume. All-workspace chats still use panel/user allowances.
Reservations prevent concurrent runs from all consuming the same remaining
allowance in ServerKit's single worker. Each run stops further calls once its
reservation is spent. Unknown-cost attempts conservatively consume that
reservation. A restarted worker marks abandoned runs interrupted and retains
their conservative charge.

Limits use reported/estimated usage; a final call can overshoot. Unknown gateway
pricing cannot enforce a billing ceiling. Use provider/gateway limits for
authoritative spending enforcement.

## Usage and plugin integration

Each response shows its actual model, per-run tokens and cost source, duration,
provider calls/errors, routing reason, and remaining conversation allowance.
Reloading history retains those run details. **AI usage** provides administrator
reports by period, role, model, connection, user, and workspace, with daily
breakdowns, remaining monthly allowances, and a drawer for provider attempts.
Unknown/partial pricing is labeled separately from known cost.

Prompture `UsageSession` collects each actual driver call; its cost calculation
and `BudgetState` checks are reused. ServerKit stores one migration-backed
`AiRun` per turn, with idempotent completion. Cumulative Conversation snapshots
remain for lifetime limits and are never summed as per-run costs. Run records
survive transcript deletion. Reporting starts with this migration; older
transcripts are not backfilled with invented per-call costs.

The plugin SDK `ask()` and `ask_stream()` use the same enable state, role/routing
policy, guardrails, limits, driver adapter, and ledger without creating chat
transcripts. Both accept optional `profile` and `workflow` arguments:

```python
from app.plugins_sdk import ai

summary = ai.ask(text, workflow='summarize')
facts = ai.ask(text, workflow='extract', profile='utility')
```

The implementation was checked against local Prompture `d36872b` and AgentSite
`db323d4`. AgentSite informed the model-picker/reporting interactions. Prompture
1.11.0 already supplies the relevant resolver, router, structured output, budget,
and usage machinery. ServerKit supplies credential binding, authorization,
scope-filtered accounting, persistence, and UI. In this SDK version, router
preferences are not an allowlist and tracker budget scopes do not filter usage
by tenant; ServerKit explicitly enforces both boundaries. No upstream issue
or external message was sent.

## Upgrade behavior

Migration `098_ai_provider_connections` creates encrypted connection records,
copies the existing single-provider configuration into **Existing connection**,
and attaches matching legacy conversations to it. Older conversations from a
different provider cannot borrow the current connection's credentials: start a
new chat using an explicitly configured connection. Resumed chats reconstruct
their Prompture driver with their saved connection, and restore history and
usage without exporting credentials or using global provider configuration.

Migration `099_ai_management` adds cached model metadata, conversation task
snapshots, and the durable run ledger. Apply normal Alembic upgrades before
starting the updated backend. No SDK upgrade or manual schema creation is needed.
