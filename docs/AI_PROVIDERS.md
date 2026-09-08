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

New chats offer a connection selector and an optional model override. Once a
chat starts, its connection and model stay fixed. Changing the panel default
does not reroute existing chats. You can rotate a connection's credentials, but
changing a provider or endpoint used by existing chats requires a new connection.
Delete those chats before deleting their connection, and select another default
before deleting the current default.

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
Gateway routing aliases may have unknown pricing; enforce authoritative spending
limits at the gateway. Automatic fallback to another connection is not enabled.

The per-conversation cost ceiling uses Prompture's `hard_stop` policy for new
and resumed conversations (default $0.50; zero disables it). It checks reported
cumulative usage before each model call, so a final call can overshoot the
remaining amount. Budget stops return a clear message, and usage is persisted
even if the turn stops before producing an answer. This is not an authoritative
billing limit when provider pricing is unavailable.

See [AI management review](AI_MANAGEMENT_REVIEW.md) for the Prompture and
AgentSite source review and the proposed task profiles, routing, and reporting.

## Upgrade behavior

Migration `098_ai_provider_connections` creates encrypted connection records,
copies the existing single-provider configuration into **Existing connection**,
and attaches matching legacy conversations to it. Older conversations from a
different provider cannot borrow the current connection's credentials: start a
new chat using an explicitly configured connection. Resumed chats reconstruct
their Prompture driver with their saved connection, and restore history and
usage without exporting credentials or using global provider configuration.
