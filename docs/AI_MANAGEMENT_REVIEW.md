# ServerKit AI management: source review and integration proposal

Reviewed 2026-09-08. Primary references are the local Prompture checkout
(`d36872b`) and AgentSite checkout (`db323d4`). CachiBot is a secondary reference
for its model-slot UI. This is a source review, not a live provider benchmark.
Neither reference repository was modified.

## Main finding

ServerKit should expose more of Prompture's existing capabilities through a
small, coherent settings UI. It does not need another model-routing or token
accounting engine. However, Prompture does not supply ServerKit's user/workspace
authorization, saved-connection policy, or reporting views automatically.

ServerKit pins and has installed Prompture **1.11.0**. Normalized source-text
comparison found these local Prompture files identical to the installed package:
`agents/conversation.py`, `agents/async_agent.py`, `pipeline/resolver.py`,
`pipeline/routing.py`, and `infra/{budget,session,tracker,ledger,capabilities}.py`.
The checkout's `VERSION` file says 1.10.0; it is not evidence that the installed
runtime lacks these features. No SDK upgrade is needed for the capabilities
reviewed below. AgentSite declares `prompture[all]>=1.9.1`, not an exact pin.

## What Prompture already provides

| Capability | Existing implementation | ServerKit integration |
| --- | --- | --- |
| Model roles and inheritance | `ModelResolver`, ordered configuration layers, utility-to-default fallback | Store explicit connection/model choices for Utility, Standard, and Advanced roles; resolve request override → task role → default. |
| Routing | `ModelRouter`, cost-optimized, balanced, quality-first, fast strategies; routing explanations | Select only from administrator-approved connection/model candidates before a job starts. Strategies are heuristics, not a reliability or operational-risk classifier. |
| Catalog and capabilities | Provider descriptors, discovery, model rates, context limits, tool/vision/structured-output flags | Enrich the existing model picker with verified metadata, filters, and input/output prices per 1M tokens. Keep manual gateway aliases. Unknown capability/pricing stays unknown. |
| Generation controls | Conversation options, history cap, tool timeout/round/result limits; Agent iteration limits, guardrails, structured output | Add a validated Advanced section for supported controls. Distinguish lifetime token budget from per-response output-token limit. Do not expose arbitrary driver JSON or imply all models accept temperature/reasoning settings. |
| Budget policies | `hard_stop`, `warn_and_continue`, `degrade`, token/cost ceilings, remaining budget | Use the existing hard-stop mechanism now. Add degradation only after connection-aware fallback is implemented and tested. |
| Per-run observability | `UsageSession`, driver callbacks, tokens/cache tokens, costs, errors, latency, per-model summaries | Attach usage collection to each actual run; preserve ServerKit context and show the result in chat and reports. |
| Persistent observability | `UsageTracker` events, filters, tags/metadata, daily/monthly budget records; `ModelUsageLedger` aggregates | Reuse event collection/calculation, with a ServerKit-owned data location and access-controlled reporting. Global SDK aggregates are not per-user accounting. |
| Larger workflows | Agent/DeepAgent abstractions, guardrails, structured outputs, delegation and context hooks | Consider separate read-only diagnostic workflows later. Keep existing plugin tools, RBAC checks, and write confirmations in control of server operations. |

Sources: Prompture [resolver](../../prompture/prompture/pipeline/resolver.py),
[routing](../../prompture/prompture/pipeline/routing.py),
[Conversation](../../prompture/prompture/agents/conversation.py),
[AsyncAgent](../../prompture/prompture/agents/async_agent.py),
[capabilities](../../prompture/prompture/infra/capabilities.py),
[budget](../../prompture/prompture/infra/budget.py),
[session](../../prompture/prompture/infra/session.py),
[tracker](../../prompture/prompture/infra/tracker.py), and
[ledger](../../prompture/prompture/infra/ledger.py).

## What to borrow from AgentSite

Its [model picker](../../AgentSite/frontend/src/components/shared/ModelSelect.jsx)
and [model endpoint](../../AgentSite/agentsite/api/routes/models.py) demonstrate
searchable provider groups, context/output sizes, tools, vision, reasoning, and
pricing. The useful idea is showing enough information to choose a model,
without requiring users to memorize IDs. Port the interaction into ServerKit's
shared SCSS controls, not AgentSite's Tailwind markup.

Its [configuration](../../AgentSite/agentsite/config.py) assigns cheaper strategies
to triage and some specialist reviewers, and quality-first to the final judge.
That is a better model for ServerKit than treating every task as equally costly.
AgentSite also has [analytics endpoints](../../AgentSite/agentsite/api/routes/agents.py)
and a [routing-cost view](../../AgentSite/frontend/src/components/analytics/CostByRoutingChart.jsx).
Borrow the questions they answer: which model did the work, how much did it cost,
how often did it fail, and which task/strategy caused the spend?

There are important limits to copying its implementation:

- `route_for()` selects the first syntactically valid model in a configured
  strategy pool. It does not attempt runtime failover. Searches found calls only
  in tests; the production orchestrator calls `resolve_agent_model()` instead.
  Strategy configuration alone is therefore not proof of routed generation in
  this checkout. See [resolver](../../AgentSite/agentsite/engine/model_resolver.py)
  and [orchestrator](../../AgentSite/agentsite/agents/orchestrator.py).
- AgentSite's utility resolver takes the first budget fallback model. In
  ServerKit, an inexpensive task model and an outage fallback should be separate
  settings; they solve different problems.
- Model-cost endpoints collapse ledger rows into a dictionary keyed only by model,
  although the ledger key is model plus API-key hash. Multiple credential rows
  can overwrite one another. The default ledger is also process-user-wide,
  while the configured tracker uses AgentSite's data directory. ServerKit should
  aggregate its own scoped run events instead of copying those endpoints.

## Recommended settings structure

Keep five sections within AI Assistant, using the shared card, field, switch,
model-picker, and footer patterns in [SETTINGS_UI.md](SETTINGS_UI.md):

1. **Connections:** credentials, endpoint, connection status, model discovery.
2. **Task models:** three labeled roles, each showing its saved connection and
   model. Show inherited defaults explicitly; no empty dropdown with hidden behavior.
3. **Limits and behavior:** spend/token limits and supported generation controls.
   Keep technical controls in an Advanced disclosure with sensible defaults.
4. **Routing and fallback:** opt-in strategy, approved candidates, fallback order,
   and a preview explaining the selected model. Publish this only with working
   consumers and connection-bound driver creation.
5. **Usage:** conversation/run totals and administrator reports by period, task
   role, model, connection, user, and workspace.

| Task role | Intended work | Selection rule |
| --- | --- | --- |
| Utility / economical | Short summaries, classification, structured extraction | Prefer an explicitly configured inexpensive model with required capabilities; inherit Standard if unset. Do not add model calls for tasks already handled deterministically, such as current chat titles. |
| Standard | Everyday questions and routine tool-assisted diagnosis | Default assistant connection/model. |
| Advanced | Complex incident analysis and multi-step diagnosis | Explicit user selection or a defined task type; inherit Standard if unset. Higher capability never changes permissions. |

Persist each choice as `{connection_id, model}`. The model portion is an opaque
provider/gateway ID, including embedded slashes. Resolve before the run, record
the decision and its reason, and preserve the selected connection for resumed
chats. A selected strategy must not switch credentials midway through a tool loop.

## Integration issues to account for

**Budget enforcement was disabled in ServerKit.** `build_conversation()` supplied
`max_cost` with `budget_policy=None`, which makes Prompture's check a no-op.
This change enables `hard_stop` for a nonzero ceiling on both fresh and resumed
conversations, validates saved limits, translates budget stops, and preserves
usage even when a turn ends without an answer. Zero disables the ceiling. The
default is $0.50, matching the settings endpoint. These checks use reported
usage: the last call can overshoot and unpriced gateway aliases need a provider
spending limit.

**Routing options are not all enforcement mechanisms.** In the reviewed
`routing.py`, `max_cost_per_call` is declared/documented but is not used by the
selection logic. Preferred providers are ordering preferences, not an allowlist.
Discovery uses the global environment. ServerKit must validate candidates against
saved connections and enforce spending through an actual budget check.

**Automatic fallback needs an adapter.** Conversation's `_switch_model()` calls
`get_driver_for_model(new_model)` without forwarding the saved driver/environment.
Do not enable it across ServerKit connections as-is. An explicit driver factory
must retain connection identity, endpoint, and credentials. Failed write tools
must not be silently replayed after fallback. ServerKit's legacy `fallback_models`
setting is currently stored but not applied by its conversation factory.

**CachiBot's router argument is not portable.** Its agent construction can pass
`model_router`; the reviewed Prompture `AsyncAgent` constructor accepts no such
argument. Use the public routing API as a separate selection step, rather than
copying that constructor call.

**Usage needs run attribution.** ServerKit persists Conversation usage snapshots,
which are cumulative; its chat context currently drops that usage when loading
messages and handling completion. Summing those snapshots double-counts spend.
Capture per-call/run usage with Prompture callbacks, or explicitly calculate
turn deltas, while retaining the cumulative value for conversation limits.

**SDK tracker scopes are not tenant isolation.** The reviewed tracker
`check_budget(scope)` selects a budget record by scope but sums usage events over
the chosen period without filtering events by that scope. Do not advertise it as
a per-user/workspace quota unchanged. Scope accounting, permissions, and any
concurrent spend reservations require ServerKit integration.

## Delivery sequence and acceptance criteria

1. **Current repair:** consistent settings surfaces and controls, model discovery
   picker, persistent enable switch, clear notification footer, enforced
   conversation ceiling. Offline tests cover both chat modes and budget resume.
2. **Profiles and visible usage:** implement the three roles and their actual task
   consumers together. Add a migration-backed run record with unique run ID,
   conversation/user/workspace, requested role, actual connection/model, routing
   reason, tokens, cost source, duration, outcome, and fallback attempts. Reuse
   Prompture counters; keep accounting idempotent. Test inheritance, permissions,
   exact gateway IDs, unknown pricing, and no double counting after resume.
3. **Routing and reports:** add approved model pools and a routing preview; show
   daily/monthly totals, model/task breakdowns, latency/failure rates, and remaining
   limits. Separate estimated cost from provider-reported cost. Missing prices
   must read “Unknown,” not “Free.” Test that disallowed connections cannot be
   selected and that report totals reconcile with recorded runs.
4. **Fallback and richer workflows:** add credential-aware failover/degradation,
   bounded diagnostic workflows, and supported context/history controls after
   the accounting and permission tests pass. Avoid shared response caching for
   live server state; any future cache needs user/workspace/connection scoping
   and an explicit freshness policy.

The task-role/routing/reporting work in steps 2–4 is a proposal, not implemented
UI. This review is an internal FYI; no upstream issue or external message was sent.
Useful upstream follow-ups are a connection-aware fallback factory, enforcement
or clarification of `max_cost_per_call`, and scoped tracker budget aggregation.
