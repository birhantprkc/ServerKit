# Changelog

All notable changes to ServerKit are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project aims to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> **Scope:** This changelog tracks the **control panel** (Flask backend + React
> frontend). The cross-platform **agent** lives in its own repository,
> [`jhd3197/serverkit-agent`](https://github.com/jhd3197/serverkit-agent), ships
> on its own cadence, and is tagged `vX.Y.Z` there (it was `agent-vX.Y.Z` while
> it lived in this repo under `agent/`, which no longer exists).
>
> Commit-level history lives in `git log`; this file curates the user-facing
> changes by theme.

## [Unreleased]

The `dev` branch is well ahead of the last `main` release. The headline work
awaiting a stable release:

### Changed

- **The security suite is now five installable extensions.** ClamAV/YARA
  malware scanning and quarantine (`serverkit-clamav`), Fail2ban management
  (`serverkit-fail2ban`), Lynis host audits (`serverkit-lynis`), automatic
  security updates (`serverkit-auto-updates`) and container image CVE/SBOM
  scanning (`serverkit-image-scan`) left the core panel for standalone
  extensions installed from the Marketplace. Installed, they mount the same
  `/api/v1/security/...` routes and the same Security-page tabs as before —
  nothing about the surface changes. Not installed, a fresh panel's Security
  page is a lean baseline (overview, firewall, SSH keys, IP lists, file
  integrity, audit, events, settings) with no install nags. Panels upgrading
  with the host tool already present get the matching extension installed
  automatically once its registry entry is live. The setup wizard now asks
  how much security tooling to install (minimal / recommended / hardened).
  The panel core keeps fail2ban status probing and the per-site jail
  write-half, so Setup Health, the security audit and per-site brute-force
  jails work with or without the extensions.

- **Email Server and Cloudflare Zone Ops frontends moved into their
  extensions** (with their own API clients), the final in-tree step before
  those extensions move to standalone repos. Core keeps the DNS-provider
  connections and the Settings → Connections relay tile.

### Fixed

- **Fleet alert thresholds are actually evaluated.** `check_fleet_thresholds`
  was written, was correct, and had no caller anywhere in the backend — so a
  threshold configured on the Fleet page was never checked against anything and
  a server could sit at 99% CPU for a week in silence. It now runs every minute
  as a built-in scheduled job like every other recurring task, and each alert it
  opens or resolves is sent to admins through the Notification Bus. A breach
  nobody is told about was most of the way to no monitoring at all.

- **The agent heartbeat stops throwing away metrics.** It built its own
  `ServerMetrics` row and filled five of the model's thirteen columns, silently
  dropping `memory_used`, `disk_used`, the network totals, both network *rates*
  and `extra`. The two rates are exactly what the fleet threshold checker reads,
  so a network threshold could never fire for an agent-reported server: the data
  needed to evaluate it was discarded on the way in. Heartbeats now go through
  `ServerMetricsService`, the same writer everything else uses.

- **Metric history says how far back it can really go.** The history and
  aggregate endpoints offered 7-day and 30-day windows while cleanup deleted
  every sample after seven days, and two constants described hourly and daily
  aggregate retention that nothing produced. A 30-day chart came back holding at
  most seven days and said nothing about the difference. Both endpoints now
  return a `coverage` block — the retention in force, the earliest point they
  can answer for, and whether the window asked for was truncated — and the two
  constants that promised retention nobody implemented are gone.

- **Aggregated metrics work on SQLite.** `get_aggregated_metrics` grouped by
  `date_trunc`, which is PostgreSQL's alone, so the endpoint raised
  `no such function: date_trunc` on every SQLite deployment — one of the two
  supported databases. Hour and day buckets are now built with the right
  function for the database in use.

- **Adding a server to the fleet works again on Docker installs.** The panel
  serves the agent installer at `/api/v1/servers/install.sh` by reading it off
  its own filesystem, but the image never copied `scripts/` — so the enrollment
  one-liner the panel prints in its own Add Server dialog returned 404 on every
  container deployment, and no server could join a containerised panel. Bare
  metal was unaffected (the installer clones the whole repo). The image now
  ships both installers, and the release pipeline boots the built image and
  requests them before publishing.

- **The agent installer downloads from the right place.** When the Go agent
  moved to its own repository, the installer and the panel's release lookup kept
  asking `jhd3197/ServerKit` for `agent-v*` tags, which exist in no repository —
  so `/api/v1/servers/agent/version` returned 503 to every agent polling for an
  update, and the download URL was a 404. Both now target
  `jhd3197/serverkit-agent` and its `vX.Y.Z` releases.

- **Failed installs say so.** The enrollment command was
  `curl -fsSL … | sudo bash`, where the shell reports bash's exit status, not
  curl's — a failed download exited 0 and installed nothing silently. It now
  downloads to a file first and only runs on success.

- **API errors are JSON and get logged.** A missing installer answered `404`
  ("wrong URL") for what was a server-side packaging fault; it now answers 503
  and logs the path it searched. Framework-generated errors under `/api/`
  (405, 413, `abort()`) returned Werkzeug's HTML page to clients parsing JSON,
  unhandled exceptions were never logged with their request path, and nothing
  in the app configured logging at all — so `logging.getLogger(__name__)` calls
  throughout the services emitted unformatted records and dropped INFO entirely.

- **Offline panels see the full extension catalog again.** When the registry is
  unreachable the Marketplace falls back to a copy bundled with the panel, and
  that copy had fallen behind the published registry — so a panel without
  internet, or one with the registry disabled, was missing LocalKit Bridge while
  an online panel offered it. The bundled copy is back in step, and a check now
  fails the build if a bundled entry ever names a version the panel does not
  actually ship.

### Added

- **Deploy Activity is now a live feed, not a table.** Runs group into Queued /
  Today / Earlier, each row showing the service, what was deployed (commit,
  image, or template) and why (push, webhook, manual), a per-step pipeline bar,
  target server, duration, and — on failures — the error inline. Status filters
  carry live counts, and a search box in the top bar narrows by service, source,
  or server.

- **Deploy Console pipeline strip.** The vertical step checklist became a
  horizontal strip above the log, with each step's bar sized to its share of the
  run — so where the time went is visible at a glance, and the log pane gets the
  full page width. A run-identity line (job id, trigger, source, target, start
  time) sits between them.

- **Templates: import shortcuts and a deploy drawer.** The catalog leads with
  the two routes no template covers — import from GitHub, or drop in a ZIP — and
  gained a Featured / A–Z sort. Deploying a template now opens a drawer that
  shows the subdomain the service will be published at (when a base domain is
  configured), and a server picker listing each target's CPU and memory, so
  placing a service on a second box is a visible choice.

- **Services: card view.** The services list can switch between the dense table
  and a card layout; the choice is remembered per browser.

### Changed

- **Section top bars lead with their tabs.** Pages in a tab group (Services,
  Domains, Files, Monitoring, Security, …) no longer repeat the section name as
  a heading — the tab strip runs flush left and the active tab names the page,
  as the lit sidebar item already did. Entity pages (a single service, site, or
  zone) keep their title, since there the title says *which* entity you are on.

- **Test deployments (dev tool).** In development mode, Deploy Activity gains a
  "Test deployment" action that streams a scripted deploy — success, failing
  build, very long output, ANSI noise, or slow — through the real Deploy
  Console pipeline, so console changes can be exercised with no Docker or
  server. Off in production by default (`SERVERKIT_DEMO_DEPLOYS` to override).

- **Deployment Activity layout fixes.** The jobs table now uses the full page
  width — it previously sat in a half-width column beside empty space reserved
  for a removed detail pane, which also clipped the Started column. Job kinds
  read as plain labels ("App deploy"), loading shows skeleton rows, and the
  Deploy Console log pane now fills the viewport height.

- **Themes — pick a color theme, or build and share your own.** Settings →
  Appearance now has a Theme Gallery: swap the whole panel's palette instantly
  with one click — 17 built-in themes, from Paper, Nord Deep, Gruvbox, Phosphor
  and High Contrast to a collection named after Venezuelan nature: Turpial,
  Araguaney, Orquídea, Apamate, Ceiba, Azulejo, Guacamaya, Cacao, Papelón,
  Onoto and Tepuy — previewed live as you hover. Your choice is personal, and the
  dark/light toggle still works on top of it. A theme is just data — a small map
  of color tokens, no code, no build step — so anyone can make one: a built-in
  Theme Studio lets you edit colors over the live panel and export a shareable
  `theme.json`, and a public themes registry means community themes install with
  one click. See [`docs/THEMING.md`](docs/THEMING.md).
- **Extensions render on a production panel without a rebuild.** An extension that
  ships a prebuilt ESM bundle (`frontend_entry: "dist/index.mjs"`) now lights up its
  UI the moment it's installed — the panel fetches the bundle through the authed
  assets route, verifies its integrity against the hash pinned at install, and loads
  it live. Previously an installed extension's backend worked but its pages stayed
  invisible until the whole panel was rebuilt. A panel-wide kill switch
  (`extensions.runtime_frontend`, on by default) can turn the mechanism off.
- **The Deploy Console — publish anything, watch it live, debug it from the UI.**
  Installing a template, deploying a repo, redeploying a service, or uploading a
  build now takes you to one full-page live console at `/deployments/<id>`: a
  terminal-style log that streams the real pull/build/start output line-by-line
  (not 1.5-second snapshots), a step rail that checks off with per-step timings, a
  live elapsed timer, and follow/wrap/timestamps/level-filter/search/copy/download
  controls. When a deploy fails, the page pins an error card with the real failure
  tail (the actual build output, not a stripped one-liner), a plain-language hint
  for common cases (port in use, image pull denied, OOM, missing env var, npm/pip
  failures…), and a one-click **Retry** — so you can read the failure and fix your
  env var / port / Dockerfile and retry entirely from the browser, no SSH. Every
  install and deploy flow — templates, repo services, manual redeploys, the Build
  tab — now rides the same job + log + console machinery, and the `/deployments`
  index deep-links every run to its console. Updates push over websockets with a
  transparent 2-second polling fallback, so the console keeps working (and says so)
  when live updates are unavailable. See [docs/DEPLOY_CONSOLE.md](docs/DEPLOY_CONSOLE.md).

- **Web Analytics — a native, privacy-first analytics extension
  (`serverkit-analytics`).** Self-hosted, first-party website analytics for the
  sites this panel manages, built on the extension platform. A cookieless
  JavaScript tracker under 4 KB (`navigator.sendBeacon`, no cookies, no
  localStorage, honors Do Not Track) and optional apache/nginx access-log
  ingestion feed a persistent time series stored on your own server — nothing
  leaves the box. Visitor identity is a daily-rotating salted hash of IP + user
  agent (raw IPs are never stored). A dashboard at `/analytics` shows visitors,
  pageviews, top pages, referrers, devices, and a live realtime counter, with
  per-site tracking snippets you can one-click inject into managed WordPress
  sites (a mu-plugin) or nginx-proxied apps (a validated `sub_filter`). The
  public collector is protected by a per-site key, a token-bucket rate limit, an
  8 KB body cap, per-site CORS, and bot filtering. Off by default; install it
  from the Marketplace. See [docs/ANALYTICS.md](docs/ANALYTICS.md). The existing
  Umami/Plausible/PostHog deploy templates remain for users who want a full
  third-party stack.

- **The setup wizard now installs what it recommends (lean by default).** The
  onboarding "Recommended for you" step is no longer decorative — it renders
  real extensions matched to the use cases you pick (e.g. WordPress → WordPress
  flagship; DevOps → Kubernetes, Automations, Git) as checkboxes that install on
  Finish. Uncheck everything for a lean install; failed installs never block
  finishing setup and can be retried from Extensions. Fresh installs no longer
  force-install WordPress — it's offered in the wizard when the WordPress use
  case is selected. Existing installs keep WordPress untouched.

- **FTP, Cloud Provisioning, Remote Access, and Status Pages are now opt-in
  extensions** — their backends moved out of core into
  `serverkit-ftp`, `serverkit-cloud-provision`, `serverkit-remote-access`, and
  `serverkit-status`, so a fresh panel that never uses them loads none of their
  code or API surface. **Upgraders lose nothing:** a one-shot boot migration
  re-acquires each extension's backend on panels that had installed it, and the
  underlying data models stay in core. Uninstalling an extension now removes its
  API surface too, not just its page.

- **Automations (tramo) replaces the Workflow Builder** — the drag-and-drop
  React-Flow Workflow Builder is retired in favour of **Automations**
  (`/automations`), an opt-in builtin extension that embeds
  [tramo](https://github.com/jhd3197/tramo): a node-based automation editor
  (triggers, actions, branches, loops, approval gates) with 21 brand
  integration packs (Telegram, GitHub, Gmail, Discord, Notion, Stripe,
  Cloudflare, Postgres, Twilio and more). Enabled workflows deploy to a managed
  `@tramo/server` container on the panel host that runs them headless — webhooks,
  cron, run history, and approvals, all proxied through the panel. Panel events
  reach workflows through an opt-in events bridge (a managed webhook
  subscription), and a scoped API key lets workflows act back on the panel
  (start/stop/deploy apps, run backups, send notifications). The old `/workflow`
  route now redirects to `/automations`. Existing Workflow Builder data is kept
  (read-only export for manual rebuild); its four event triggers
  (`app.stopped`, `health.check_failed`, `git.push`, `monitor.high_cpu` /
  `monitor.high_memory`) are ported to the panel event bus.
- **Command palette is now an everything-search (F1 / Ctrl+Shift+P / Ctrl+K)** —
  the palette went from a page-jumper to omnisearch. Type to search across pages,
  individual **settings** (the actual card, e.g. "Require two-factor for all
  members" or "SMTP", not just the tab), runnable **actions** (`>` prefix — New
  Service, Add Server, Toggle theme, Sign out…), live **entities** (services,
  servers, domains, databases, WordPress sites, cron jobs, extensions, vaults —
  via a new authz-aware `GET /api/v1/search`), and **docs** (`?` prefix). Results
  rank by match quality plus your own usage (per-user frecency), an empty query
  shows Recently used, and pressing Enter on a settings result lands you on the
  exact card — scrolled into view and briefly highlighted. VS Code's F1 and
  Ctrl/Cmd+Shift+P now open it alongside the original Ctrl/Cmd+K. Results are
  filtered by the same admin + workspace-nav rules as the sidebar, so a member's
  palette never surfaces admin-only pages, settings, or actions.
- **One-click GitHub setup (GitHub App manifest flow)** — connecting GitHub no
  longer means hand-registering an OAuth app and pasting a client id + secret.
  An admin clicks **Set up in one click** in Settings → Connections; ServerKit
  hands GitHub an app *manifest*, the admin confirms once on github.com, and
  GitHub returns freshly-minted credentials that are stored **locally on the
  server** (client id/secret in settings, private key encrypted) — nothing
  secret ships in the open-source build. Setup then chains straight into the
  app's install screen, which grants repo access and authorizes in one hop. The
  connect flow routes through the app install URL, and repositories are listed
  per-installation (GitHub Apps don't use `/user/repos`). Bringing your own
  OAuth app still works, tucked under an "Advanced" disclosure. New endpoints:
  `GET/POST /api/v1/source-connections/admin/github/app-manifest[/complete]`.
- **New Service is a real three-step wizard, and Templates is one catalog** —
  `/services/new` was a form-wall that rendered everything at once (a "Ready to
  Import" rail of placeholder rows, a decorative Connect→Detect→Deploy strip,
  two boilerplate note boxes). It is now a slim stepper — **Source → Connect →
  Review** — in one centered column; nothing renders until it has real data, and
  the `?template=`/`?source=` deep links land preloaded on Connect. Deploy
  templates are no longer a hardcoded frontend constant: they live in the backend
  catalog as `kind: repo` YAML entries (starting with AgentSite) that blend into
  the one Templates grid alongside the 105 one-click templates, each badged
  **Git repo** vs **One-click**. The Templates page adopts the Marketplace
  treatment — topbar search + a filter drawer (category, type, sort) with a row
  of quick category chips — replacing the stacked "Deploy templates" section and
  the "More +95" chip wall. A new `GET /api/v1/templates/<id>/manifest` inspects
  a repo template's **public** repository (no clone, no OAuth) for deploy
  manifests and falls back to the template's declared hints, clearly labeled.
  The old `#deploy-templates` anchor redirects to the repo-filtered grid.
- **`serverkit.yaml` — build services from a Dockerfile (`dockerfilePath`)** —
  a manifest service can now declare `dockerfilePath: services/api/Dockerfile`
  as a third image source next to buildpacks and BYO `image:`. This is the
  monorepo path: one repository, several services, each built from its own
  Dockerfile with the repo root as the shared build context. Apply clones the
  project's repository (from the stored manifest's provenance, or a sibling
  app's git deployment) and writes the same git-deployment + build config the
  import wizard does, so the existing deploy pipeline — push webhook and
  per-service `autoDeploy` included — takes over unchanged. Relative paths only
  (`..`/absolute are validation errors); mutually exclusive with `image` and
  `containers`; a project with no repository on record is a plan-time blocker
  (`dockerfile_no_source`), and remote targets refuse like the other appliance
  features. Scaffold round-trips it, and the import wizard seeds
  `build_method: dockerfile` from the manifest.
- **Appliance tier for `serverkit.yaml` — typed L4 ports + a plan-time blockers
  rail** — a service can now declare raw `ports[]` (`{port, containerPort,
  protocol: tcp|udp, expose: public|local}`) as an escape hatch for
  infrastructure that speaks more than HTTP (media/UDP, VoIP, brokers). `public`
  ports render a `0.0.0.0` publish and open a firewall rule (recorded so an app
  delete closes it); `local` ports bind `127.0.0.1`. The scalar `port` keeps its
  HTTP/nginx behavior untouched. Apply grows **blockers** distinct from advisory
  issues — it refuses (nothing executed, no force flag) on a port conflict, a
  remote/observed target, or an undetectable firewall, with a message that says
  what the target can't provide and how to fix it.
- **Appliance tier — finished volumes + one-shot first-boot bootstrap** — a
  manifest disk's `size` is now recorded on the volume, its declared mounts are
  emitted into the generated compose, and its `backup:` block resolves the
  docker volume's live host mountpoint so backups capture the real data. A new
  `bootstrap: { command, timeoutSeconds }` runs once (via `docker compose run
  --rm`) after volumes exist — for appliances that generate a config tree on
  first boot — and stamps the app so it never re-runs; re-arm it with
  `POST /manifests/bootstrap/reset` (type the app name to confirm).
- **Appliance tier — multi-container units** — a service can now declare a
  `containers:` map and become one Application rendered as one compose project
  with a private network and health-gated `dependsOn`. Each container takes the
  full vocabulary (`image`, `ports`, `disks`, `envVars`, `bootstrap`,
  `hostRequirements`, `healthCheck`, `dependsOn`); `healthCheck.cmd` becomes a
  `CMD-SHELL` probe and `healthCheck.httpPath` a `wget` probe; dependsOn is
  validated against sibling containers and rejected on cycles. Per-container
  named volumes (`{unit}-{container}-{disk}`) let two containers mount the same
  path without colliding. `dependsOn` cycles and buildpack-key mixing are
  plan-time errors.
- **Appliance tier — BYO image + host requirements** — a service (or unit
  container) can declare a ready-made `image:` with an optional private
  `registry:` (an unknown/uncredentialed registry is a plan-time blocker; ECR is
  accepted via its key-pair exchange), and `hostRequirements:`
  (`privileged`/`capAdd`/`sysctls`/`devices`/`kernelModules`). Host requirements
  are listed in plain words in the plan and written to a
  `manifest.host_requirements` audit line on apply — never applied silently;
  `kernelModules` are an advisory `/proc/modules` check.
- **Appliance tier — network identity** — an env var can bind the service's own
  advertised address with `fromServer: { property: publicIp|hostname }` (the
  WebRTC/NAT need; a missing IP is a `fromserver_no_ip` blocker), and templates
  gain a `${SERVER_PUBLIC_IP}` magic variable. Manifest-generated app projects
  now attach to a shared external `serverkit` docker network (created
  idempotently), so a `fromService` host reference resolves cross-app by
  container name at runtime. Static egress IPs remain out of scope (documented).
- **Appliance tier — reference appliance + adoption hardening** — a runnable
  4-container real-time-media reference manifest ships at
  `docs/examples/reference-appliance-media.yaml` and applies end-to-end.
  `serverkit.yaml` scaffolding now round-trips typed `ports`, a BYO `image`, and
  disk `size` from a live app, and drift detection tracks raw ports and image
  alongside the existing surface.
- **New Service wizard clarity** — each source card now shows a short explainer
  strip when selected (what it does, what to have ready, the next steps), and a
  "Docs" link to the matching serverkit.ai guide (hidden under White Label). The
  demo deploy template moved out of the wizard into a shared module: the
  Templates tab gains a **Deploy templates** section with a "Use template"
  action that deep-links into the wizard with the template preselected
  (`/services/new?template=<id>`), so there is one list with two entry points.
- **Install extensions straight from GitHub, with a preview + consent step** —
  the manual-install URL flow is now two-step: paste a repo (`owner/repo`,
  `owner/repo@tag`, a release URL, or a direct `.zip`), **Preview** resolves and
  reads the extension, then a consent card shows its version, declared
  permissions, panel-version compatibility, and warnings (no release found,
  slug already installed, version-gate mismatch) before you install. The install
  is pinned to the exact previewed bytes via sha256. A new
  `POST /api/v1/plugins/preview` endpoint powers it. Set the optional
  `SERVERKIT_GITHUB_TOKEN` to lift GitHub's anonymous rate limit and install
  from private repos (the token is only ever sent to GitHub, never logged).
- **Public extension index (registry v2)** — the extension registry schema gains
  optional `logo`, `repo`, and `bundled` fields (additive; v1 entries stay
  valid). The Marketplace now surfaces extension logos (shown first in the art
  fallback chain) and a **Source repo** link on the detail modal. Builtin
  extensions are published to the public index as `bundled` catalog listings
  (generated via `scripts/export-registry-entries.mjs`) so the index is the full
  catalog of every extension; bundled entries stay out of the Browse merge to
  avoid duplicating builtin cards (`GET /api/v1/marketplace/registry?include_bundled=true`
  returns the complete set).

### Changed

- **Skeleton loading overhaul — one primitive, overlay-on-content, no layout
  shift.** Loading placeholders no longer swap in a disconnected tree that jumps
  when data arrives. A new `SkeletonBoundary` keeps the real content in flow and,
  while loading, hides it and paints the skeleton *over* its exact box — so the
  placeholder inherits the real dimensions at every breakpoint (zero guessed
  widths, zero cumulative layout shift on refresh). The two competing skeleton
  primitives were consolidated to the single SCSS-based `Skeleton` (the Tailwind
  `ui/skeleton` shim and its raw sizing classes are gone), and the worst
  hand-guessed loaders — SSL certificates, the WordPress list and detail tabs,
  and the WordPress activity feed — now render through the boundary. All skeleton
  shimmer honors `prefers-reduced-motion`, and loading regions expose `aria-busy`
  for assistive tech. A dev-only tool (`npm run capture:skeletons`) can measure a
  logged-in page's real layout into baked bone assets for pixel-accurate
  placeholders, replayed via the boundary's optional `bones` prop — no browser or
  extra dependency ships in the product.

- **`update.sh` now defaults to pre-built releases (Node-free updates).** The
  frontend is compiled once in CI and shipped, so a normal `update.sh` no longer
  rebuilds the SPA on the server — it fetches the pre-built release tarball,
  meaning the server needs no Node/npm at all. This keeps updates working
  uniformly on old distros, ARM/Raspberry Pi and tiny boxes. Opt into an on-box
  source rebuild with `--source`, `--branch <name>`, or `BUILD_FROM_SOURCE=1`
  (that path now requires Node 20.19+/22.12+ for the vite 8 toolchain; the
  installer provisions Node 22 LTS and both scripts fail with a clear "upgrade
  Node" message instead of a cryptic bundler error).

- **UI consistency round (Jobs, Queue Bus, Email, Marketplace)** — brought four
  drifted pages back onto the shared host idiom. The **Jobs** page was rebuilt
  to the /servers–/domains table-with-search pattern (SegControl status filter,
  kind select, debounced search, DataTable rows, clickable KPIs) and now pages
  server-side against the job store; a new `builtin.job_retention` scheduled job
  prunes old terminal jobs (`jobs.retention_days`, default 14; failed kept 3×)
  so the total count stops growing without bound. **Queue Bus** counts render in
  a compact notation (107,814 → "107.8K", exact value on hover) via a new
  `formatCompact` util and an opt-in `compact` prop on the KPI tile, so six-digit
  totals no longer burst the rail. The **Email Server** page was reskinned to the
  `sk-email` design system (its rebuilt classes had been wearing dead selectors),
  with a designed not-installed state and DataTable lists. The **Marketplace** got
  a density pass — Installed promoted to a top-bar tab, one toolbar row, compact
  cards — plus per-extension cover art (deterministic per-slug gradients with
  brand marks, optional registry `logo`).

### Fixed

- **July‑5–7 recovery rebuild** — a scattered subset of the July‑5–7 work
  (plans 22, 28–34) was lost when a backup was corrupted, leaving the backend
  unable to boot. Reconstructed the dropped service layer (agent‑fleet survey +
  fleet doctor/repair, backup restore‑drills + verify, reachable DNS cutover,
  setup‑health + reconcile, cron‑run history, notification digests + chat
  webhooks, the 4‑tier authorization model), the `FleetDoctorResult` /
  `DnsCutoverSnapshot` models, the missing migration chain (056–074), five lost
  frontend components, and the per‑app route authorization gating — recovered
  with high fidelity from surviving compiled bytecode, the surviving tests, and
  the plan docs. Backend boots clean, `flask db upgrade` reaches head on a fresh
  DB, and the full test suite is green again.
- **Recovery parity audit** — a follow-up audit found the recovery rebuild had
  silently dropped more than the above: because code and its proving tests died
  *together*, the suite stayed green while whole slices vanished. Restored the
  Cloudflare Round 2 engine (DNSSEC status, Origin CA issue/install/revoke,
  redirect & transform rules, the per-zone operations activity ledger, and the
  per-product token-scope probe), the email-bounce webhook + auto-mute slice,
  the DNS-cutover explicit-records staging path (a `NO_PROVIDER` now names the
  provider), the Server **Survey** tab and its (previously 404-ing) API routes,
  the cron run-history styling, and 18 lost backend test suites. Where a restored
  test proved its feature still hollow, it is skipped with a reason naming the
  exact missing symbol so the gap stays visible.
- **Survey API routes returned 404** — `app/api/survey.py` was defined but never
  registered after the rebuild; the server-detail Survey tab and management-mode
  endpoints are reachable again.
- **Test-count ratchet** — Backend CI now fails if the collected-test count drops
  below a checked-in floor (`backend/tests/BASELINE_COUNT`). Lowering the floor
  requires editing that file in the same commit, so a silent test loss (the exact
  failure mode that hid the recovery gaps above) surfaces as a red X instead of a
  still-green suite.

### Removed

- **Legacy marketplace catalog** — the DB-seeded `Extension`/`ExtensionInstall`
  catalog (a third, always-empty lane in Marketplace Browse) was retired. Browse
  now has exactly the real sources: bundled built-ins, the remote registry, and
  installed-plugin state. The orphaned `extensions`/`extension_installs` tables
  are dropped by migration 046 (they never held real data — nothing seeded them).

### Added

- **Mail Server extension (`serverkit-mail`)** — an opt-in built-in that runs a
  self-hosted mail server (Stalwart, in a managed Docker container) driven
  through its HTTP admin API. Manages mail domains, mailboxes, forwarders,
  autoresponders and catch-all; generates DKIM keys and deploys DKIM/SPF/DMARC/MX/A
  records through the existing DNS-provider integrations; requests a Let's Encrypt
  cert for the mail hostname; and shows the outbound queue. A **deliverability
  preflight** (reverse-DNS/PTR match, port-25 egress, RBL listing, listening
  ports) runs on a daily schedule and **blocks outbound sending until it passes**
  (an explicit force override is audit-logged), because self-hosted mail on a VPS
  is a real commitment — many providers block port 25 and a fresh IP can be
  pre-burned. Ships brute-force auth jails and registers the mail store for
  scheduled backups. Not installed by default; enable it from Marketplace. It runs
  alongside the older Postfix/Dovecot `serverkit-email` extension with entirely
  separate identifiers.
- **Configuration drift detection + repair ("doctor")** — a daily read-only
  sweep re-renders the expected nginx vhost and compose override for every
  managed resource from panel state and diffs it against disk; drift raises an
  admin notification, and a Doctor tab on Monitoring runs the full diagnosis
  (drift, core services, cert expiry, disk headroom, database) with
  diff-confirmed per-item repair and batch repair. Nothing is ever changed
  automatically.
- **Operator CLI** — the `serverkit` CLI gains API-backed verbs for when the
  browser isn't an option: `status`, `services list/restart`, `apps list`,
  `doctor [--repair]`, `repair <type> <id>`, `update`, `support-bundle`, and
  `login-url` (mints a one-time login link). Auth is a break-glass 10-minute
  token minted in-process — root on the box already implies full control —
  and every mint is audit-logged.
- **Diagnostic support bundle** — one call (API or CLI) exports a sanitized
  zip (versions, service states, setting shapes without values, recent job
  failures, scrubbed log tail) to attach to a bug report; secrets are
  scrubbed by pattern and by the settings secret-key list.
- **Web-shell/YARA scanning + job-backed malware scans** — malware scans now
  run as jobs, can target an app's docroot directly, and a curated web-shell
  rules pass (eval/base64 droppers, c99/r57/WSO markers, PHP-in-image,
  auto_prepend hijacks…) runs alongside ClamAV — with a pure-Python fallback
  when yara isn't installed, custom rule upload, and one-click quarantine
  with restore.
- **File-integrity monitoring** — baseline-and-diff over the paths ServerKit
  manages (nginx config, ServerKit systemd units, app docroots on opt-in)
  every six hours, feeding the Notifications Bus; the Security → Integrity
  tab gains baseline/check/accept controls and a what-changed view.
- **CrowdSec extension** — a new `serverkit-crowdsec` marketplace extension
  surfaces CrowdSec decisions and alerts, lets you ban/unban IPs and manage
  allowlists via cscli, with graceful degradation when CrowdSec isn't
  installed.
- **Authoritative DNS server extension** — a new `serverkit-dns-server`
  marketplace extension runs PowerDNS in a managed container so a ServerKit
  box can be the nameserver for its domains: zones, records, DNSSEC with DS
  records for the registrar, and a delegation check. For homelab and
  air-gapped setups; complements (never replaces) the provider integrations.
- **Per-domain bandwidth accounting** — daily nginx access-log aggregation
  into per-site transfer stats, with 30-day sparklines on the Services list
  and a monthly figure on the service Overview.
- **Per-site micro-cache** — an opt-in nginx micro-cache (10s TTL) per site
  with safe bypasses for logged-in/admin/cart cookies and paths, an
  `X-SK-Cache` header for verification, and a manual purge button. Big cheap
  win for WordPress/PHP sites.
- **`.htaccess` → nginx converter** — a paste-in tool (on the Import wizard)
  translating common rewrite/redirect/auth/access rules to nginx directives,
  flagging anything it can't translate with the reason and line number.
- **Import a site (migration pipeline)** — a 5-step wizard at `/imports`
  (entry points on Services and WordPress) restores cPanel/WHM, DirectAdmin
  and Hestia/Vesta backup archives onto ServerKit: the archive is analysed
  into a report (domains, databases, DB users, crontab, warnings), then a
  resumable job maps it to an app, managed databases (MySQL password hashes
  preserved where the engine allows) and cron entries, with per-step logs and
  retry-from-failed-step. Uploads and fetch-by-URL both supported, with SSRF
  and archive-traversal guards throughout.
- **WordPress: import over SSH** — point at any live WordPress site with SSH
  credentials (`/wordpress/ssh-import`): probe shows the pinned host key and
  site facts, then a job pulls the docroot, dumps the database through the
  tunnel and rebuilds it as a managed site with the URL search-replaced.
- **Database tools** — the Database Explorer gains a live processlist with
  kill/terminate per server or container; a curated config tuner (RAM-aware
  suggested values for vetted MySQL/PostgreSQL settings, applied with backups
  and clean rollback, never auto-applied); managed database users tracked as
  first-class rows; and one-click "Open in Adminer" SSO via a single-use,
  five-minute shadow credential scoped to one database.
- **Per-app resource limits** — CPU and memory limits are first-class fields
  on an app (Settings → Resource Limits) showing live usage vs limit, applied
  to the container without touching the user's own compose file.
- **One-time login links** — admins can mint single-use, short-TTL,
  optionally IP-bound login URLs from Settings → Users, for "log in and take
  a look" support situations; links are hashed at rest and reaped hourly.
- **Demo mode** — an opt-in flag that blocks every mutating API call with
  `403 demo_mode` and offers seeded read-only credentials on the login page,
  for running a public demo of a real panel.
- **Cloud-metadata egress guard** — a default-on (opt-out) firewall rule
  blocking app containers from 169.254.169.254, closing the SSRF-to-cloud-IAM
  credential-theft class on cloud VPSes (Security → Firewall).
- **Server speed test** — an on-demand download/upload/latency test on the
  Monitoring page, using the Ookla/speedtest CLI when present with a
  pure-Python fallback.
- **Extensions platform (Phase 7 — settings slot + manifest linting)** — extensions
  can now contribute sections to the Settings page (a `settings.section` widget
  slot rendered below the active tab), and `plugin.json` manifests are shape-checked
  at install time: malformed entry points, socket/model references, jobs, schedules,
  or contribution entries now fail the install with a message naming each problem
  instead of being silently dropped at runtime. Authors get the same rules locally
  via `node scripts/new-extension.mjs --validate <path>`.
- **Extensions platform (Phase 7 — scheduled update checks)** — the panel now
  checks the extension registry for updates once a day (a regular scheduled job,
  visible under Jobs) and notifies admins through the Notifications Bus when new
  versions are available — once per release set, not once per day. The
  Marketplace "Update available" badge remains the always-current surface.
- **Extensions platform (Phase 7 — per-extension configuration)** — an extension
  that declares a `config_schema` in its manifest now gets a real **Configure**
  form on the Marketplace Installed tab (text/number/boolean/enum/secret fields);
  values persist on the panel and the extension's backend reads them with the new
  `plugins_sdk.config(slug)` accessor. Config values may hold secrets, so they are
  served only by an admin-gated endpoint and never appear in plugin listings.
- **Extensions platform (Phase 7 — installed extensions survive panel updates)** —
  previously a panel update deployed a fresh source tree preserving only `.env`
  and the database, silently wiping any URL/registry/upload-installed extension's
  files (the install row then flipped to `error` on the next boot). Two-layer fix:
  the updater now carries user-installed plugin directories forward into the new
  tree (before the frontend build, so their UI recompiles), and the backend gained
  a boot-time repair pass that restores builtin installs from `builtin-extensions/`
  and re-downloads URL installs from their recorded source — upload-only installs
  it can't restore get a clear "re-upload" error instead of a cryptic import failure.
- **Extensions platform (Phase 7 — extensions can now contribute tabs to core
  tab groups)** — a new `tabs` contribution kind lets an installed extension add
  a real tab to a core-owned tab group (Files, Servers, Observability): the tab
  joins the shared top-bar strip, its routes render inside the group's layout so
  the chrome stays, and the group's sidebar item stays lit on the extension's
  routes. Four pages moved out of core onto it: **FTP Server → `serverkit-ftp`**
  (Files group), **Cloud Provisioning → `serverkit-cloud-provision`** and
  **Remote Access → `serverkit-remote-access`** (Servers group, keeping their
  original tab positions; the per-server Remote Access tab on the server detail
  page stays core), and **Status Pages → `serverkit-status`** (Observability
  group; the public `/status/<slug>` page stays core). Each tab + page + palette
  entry disappears together when its extension is uninstalled. Existing panels
  auto-install all four once on upgrade so nothing disappears; fresh installs
  find them in the Marketplace.
- **Extensions platform (Phase 5 — Cloudflare zone-ops is now a bundled extension)** —
  the Cloudflare per-zone control panel (zone settings, cache purge, WAF, Workers,
  Tunnels, and R2/KV/D1 storage, reached from the "Open in Cloudflare" button on a
  Cloudflare-managed domain) moved out of core into the **`serverkit-cloudflare-ops`**
  extension. It ships installed by default (a flagship — zero nav footprint, and the
  core Domains button depends on the route) and is uninstallable. Crucially, **DNS
  records and the Cloudflare connection stay core** (they back `/domains`): the
  extension borrows the single core Cloudflare API client rather than vendoring its
  own, so there's exactly one client and no credential duplication. API paths are
  unchanged (`/api/v1/cloudflare`, D9), and the `CloudflareWorker`/`CloudflareTunnel`
  models stay core (they key off the shared DNS-provider connection).
- **Extensions platform (Phase 5 — WordPress is now a bundled extension)** — the
  entire WordPress backend (site provisioning, plugin library, environments/
  pipelines, updates, security, vulnerability scanning, analytics & reports, and
  the `/api/v1/wordpress` API family) has moved out of core into the
  **`serverkit-wordpress`** extension. Because WordPress is a flagship, it ships
  **installed by default on every panel** and can be uninstalled to slim the core —
  it never becomes a Marketplace hunt (decision D4). API paths are unchanged
  (`/api/v1/wordpress`, `/projects`, and the `/pipelines` alias all survive, D9),
  and every WordPress model stays core so backups, Fail2ban, status pages, and
  environment activity keep their foreign keys. The old WordPress "module toggle"
  is retired — the extension's install/enable state is the gate. Core code reaches
  the extension's services through an importlib bridge, so a panel with WordPress
  uninstalled no longer loads any of the ~6k lines of WordPress service code. The
  **WordPress UI is contributed by the extension too** — a single `wordpress/*`
  route self-renders the whole WordPress sub-router (site list + plugin library +
  pipelines tab group, plus the full-bleed site/pipeline detail pages), and the
  sidebar item, command-palette entries, and page titles all come from the
  extension manifest. Uninstalling WordPress now cleanly removes its nav, routes,
  and API in one go.
- **Extensions platform (Phase 4 — Email is now an extension)** — the mail-server
  stack (Postfix/Dovecot, domains, mailboxes, DKIM/SpamAssassin, Roundcube webmail,
  and the `/api/v1/email` API) has moved out of core into the bundled
  **`serverkit-email`** extension. Panels that never run mail no longer load any of
  it — a real "smaller core" win. Existing panels that actually used mail
  auto-install the extension once on upgrade (detected by existing mail domains/
  accounts); everyone else finds it in the Marketplace. Outbound notification SMTP
  is unaffected — it never depended on the mail server. (The Email "module toggle"
  is retired in favor of installing/disabling the extension.)
- **Extensions platform (Phase 3 — platform primitives)** — the machinery that
  makes extensions first-class and safe. Extensions can now own **data models**
  (manifest `models` → `ext_<slug>_*` tables, created on install, dropped on
  purge), **background jobs & schedules** (wired into the Jobs system, and paused
  automatically when the extension is disabled), and a **real-time Socket.IO
  namespace** (`/ext/<slug>`, status-guarded). Declared **permissions** are now a
  consent step and enforced by an SDK capability gate (`require_permission`).
  **Panel-version compatibility** (`min_panel_version`/`max_panel_version`) is
  enforced at install and update. Uninstall offers **keep-data vs purge**. New
  generic **contribution slots** (`dashboard.top`, `service.detail.tab`,
  `domain.drawer.panel`) let extensions enrich core surfaces, not just add pages.
  The frontend-delivery decision is recorded in
  [`docs/adr/0001-extension-frontend-delivery.md`](docs/adr/0001-extension-frontend-delivery.md).
- **Extensions platform (Phase 2 — remote registry & updates)** — the Marketplace
  Browse tab can now show extensions from a curated remote **registry** (a single
  `index.json`), merged in and labeled "Registry", with no per-panel seeding. The
  fetch is offline-tolerant (last-good cache → a bundled fallback index) and
  cached. Installing from the registry is **checksum-verified** — the downloaded
  zip's sha256 must match the index before extraction, or the install hard-fails.
  Panel-version gates (`min_panel_version`/`max_panel_version`) block installs a
  panel is too old to run. Installed extensions listed in the registry now get an
  "Update available" badge and a one-click **Update**. Format + publishing guide in
  [`docs/EXTENSIONS_REGISTRY.md`](docs/EXTENSIONS_REGISTRY.md).
- **Extensions platform (Phase 1 — seed the marketplace)** — the Marketplace is
  now genuinely populated. **GPU Monitor** and **Workflow Builder** became bundled
  builtin extensions (`serverkit-gpu`, `serverkit-workflows`) — same route, but
  their nav/route/title/command-palette entries now come from the extension
  manifest. An upgraded panel auto-installs a converted builtin once so nothing
  disappears; fresh installs simply see it in the Marketplace. New **Module
  toggles** (Settings → Modules) let an admin hide the Email and WordPress
  verticals — nav, routes, and the module's API (`/api/v1/email`,
  `/api/v1/wordpress`) all switch off — for a smaller panel without uninstalling
  anything. The Marketplace gained a "by ServerKit" first-party badge, real
  category chips, and an extension detail view with icon + screenshots.
- **Extensions platform (Phase 0 — hygiene)** — groundwork for the small-core +
  marketplace direction. A single **extension author guide**
  ([`docs/EXTENSIONS.md`](docs/EXTENSIONS.md)) documents the manifest schema,
  contribution envelope, lifecycle hooks, backend SDK, install sources, and the
  production frontend-delivery constraint. Builtin-extension frontends are now
  mechanically kept in sync with their source
  (`scripts/sync-builtin-frontends.mjs` + an `Extensions CI` drift gate) instead
  of hand-duplicated. The Marketplace labels bundled entries honestly ("Built-in"
  rather than "Local mapping/Entries"). First automated coverage for the plugin
  install pipeline (builtin install, contributions envelope, disable→503 guard,
  reinstall metadata refresh, zip-slip rejection).

- **Managed databases** — the databases ServerKit provisions are now tracked as
  first-class resources (beside the existing live explorer): durable rows for
  backups and connection strings. A managed database backs a `BackupPolicy` by a
  real foreign key (not an untethered descriptor), one-click "Protect" creates
  that policy, and a real connection URI can be revealed/copied (audited, secret
  Fernet-encrypted at rest). Adopt an existing database to start tracking it. API
  under `/api/v1/databases/managed`. Not a DBaaS — no pooling/replicas/scaling.
- **Per-app managed volumes** — first-class, tracked persistent storage for a
  service. Attach a named Docker volume at a chosen container path under
  Settings → Storage; it survives redeploys and is visible with live
  present/size state, instead of a fragile relative bind mount
  (`./mysql-data:/var/lib/mysql`). Detaching keeps the data by default; wiping is
  blocked while the app runs. API under `/api/v1/apps/<id>/volumes`.
- **Private container registries** — store credentials once under Settings →
  Connections (GHCR, Docker Hub, GitLab, ECR, or any generic registry) and
  ServerKit runs `docker login` before pulling a private image, then logs out.
  Secrets are Fernet-encrypted at rest and piped via stdin (never on argv);
  attach a registry to a service under Container Ops. Anonymous pulls are
  unchanged. API under `/api/v1/connections/registries`.
- **Container status aggregator** — collapses an app's per-container Docker
  states into one deterministic status (`running:healthy` … `degraded` …
  `unknown`) at `/api/v1/status/app/<id>` and `/api/v1/status/apps`, with
  change-only pushes over the `container_status` Socket.IO channel.
- **API token scopes** — fine-grained, additive scopes for API keys (enforced
  only for `X-API-Key` requests; JWT/session callers stay RBAC-governed), a
  `require_scope` decorator, and a scope catalog at `/api/v1/api-keys/scopes`.
- **Server onboarding state machine** — a linear lifecycle (validating →
  installing prerequisites → installing Docker → pairing agent → ready/failed)
  driven on the job bus, with start/retry/status at
  `/api/v1/servers/<id>/onboarding/*` and an ordered progress log.
- **Declarative template catalog** — a documented catalog schema
  (`/api/v1/templates/catalog/schema`) with auto-resolved `${SERVICE_*}` magic
  variables (password/user/FQDN/URL/base64) so templates never hardcode generated
  secrets or hosts. See [docs/TEMPLATE_CATALOG_SCHEMA.md](docs/TEMPLATE_CATALOG_SCHEMA.md).
- **Build packs** — zero-Dockerfile detection that inspects a repo and generates
  a Dockerfile + compose from a build plan (`/api/v1/buildpacks/detect`,
  `/generate`), persisted on the application row; defers to an author-provided
  Dockerfile when present.
- **Deployment config snapshots** — immutable, secret-masked config snapshots
  captured before each deploy, with diff and one-click restore + redeploy at
  `/api/v1/apps/<id>/snapshots[/<id>/diff|/restore]`.
- **Projects & Environments** — a Workspace → Project → Environment → Apps
  hierarchy (`/api/v1/projects`, `/api/v1/environments`) with workspace-scoped
  access and resource counts.
- **Shared resources** — polymorphic tags and attachable shared variable groups
  with a merged "resolved" view and masked secrets (`/api/v1/shared/...`).
- **PR preview environments** — ephemeral previews driven by a pull-request
  webhook (`/api/v1/webhooks/pull-request/<token>`) that open, redeploy, and tear
  down per PR, managed at `/api/v1/apps/<id>/previews`.
- **Per-server managed proxy stack** — opt-in Dockerized Traefik or Caddy per
  server with a compose preview before switching, host nginx remaining the
  default (`/api/v1/servers/<id>/proxy*`).
- **Multi-platform agent & fleet management** — native Go agent for Linux,
  Windows, and macOS with HMAC-SHA256 auth and WebSocket + HTTP-poll transports,
  plus a fleet dashboard (inventory, connection status, approval queue,
  discovery, rollouts, and command queue).
- **Native Windows agent** — Windows service, desktop setup wizard (WebView2),
  system tray, and MSI installer; also `.deb`/`.rpm` packages and ARM64 builds.
- **Agent pairing** — short-code and passphrase pairing flows with keypair
  enrollment, the `sk1` connection-string format, and automatic fallback to
  polling when WebSocket connections flap.
- **Remote operations over the agent** — files, packages, services, cron, sudo,
  Docker, Cloudflare tunnels, and streamed job progress on connected servers.
- **Plugin / extension system** — plugin SDK, contribution points, capabilities
  and permissions, marketplace UI, built-in extensions, and a GUI plugin
  (`serverkit-gui`).
- **Status pages** — public status pages with HTTP/TCP/DNS/Ping checks,
  component monitoring, and incident management.
- **Cloud provisioning** — provision servers on DigitalOcean, Hetzner, Vultr,
  and Linode with cost tracking.
- **Git-based services** — GitHub source connections, repository picker,
  manifest detection, and "New Service from repo" (Git extension canonical at
  `/git`).
- **RHEL-family support** — the installer now covers Rocky, AlmaLinux, RHEL, and
  CentOS in addition to Ubuntu/Debian/Fedora.
- **Per-app Web Application Firewall** — ModSecurity v3 + OWASP Core Rule Set
  with detect/block modes, paranoia tuning, a disabled-rule editor, one-click
  apply (nginx include injection), and parsed audit-log events.
- **Container lifecycle controls** — image-update detection with one-click
  apply, idle container auto-sleep, and CPU-driven horizontal auto-scaling, with
  cron-drivable sweeps for the sleep/scale policies.
- **GPU monitoring** — NVIDIA utilization, memory, temperature, power, and
  per-process / per-container usage.
- **Dynamic DNS** — token-authenticated A/AAAA updates synced through a
  connected DNS provider (e.g. Cloudflare).
- **Secrets manager & inbound webhook gateway** — encrypted secret storage and
  inbound webhook endpoints for triggering automation.
- **Passkeys / WebAuthn** — passwordless and second-factor authentication with
  hardware keys, Touch ID, and Windows Hello.
- **Remote service tunnels** — expose a private or NAT'd service through an edge
  server over an agent-managed, NAT-traversing WireGuard tunnel, reusing nginx,
  DNS, and certificates.
- **Connections hub** — a single place to link external accounts (source code,
  cloud, DNS, domain registrars with expiry tracking, SMTP relays, and S3/B2
  storage), with credentials encrypted at rest.
- **WordPress publishing** — publish managed sites at a real subdomain, swap a
  site's URL safely with preview, and attach a custom domain with automatic DNS.
- **Guided installer / updater** — health-checked install and update flow with
  automatic rollback.

### Security

- **Login brute-force is now throttled per client IP.** On top of the existing
  per-user account lockout, ServerKit now blocks a *client IP* after repeated
  failed logins (default 10 failures / 15 min → 15-minute block, returned as
  `429` with `Retry-After`), checked before the password is verified. This stops
  password-spraying across many usernames from one source and closes a gap where
  one attacker could drain the shared login rate-limit for everyone. It also
  guards the one-time login-link redeem and 2FA-code verification endpoints.
  Tunable via `AUTH_IP_MAX_ATTEMPTS` / `AUTH_IP_WINDOW_MINUTES` /
  `AUTH_IP_BLOCK_MINUTES`.
- **Client IP is no longer spoofable behind the proxy.** Rate-limit buckets,
  login lockout and audit-log source IPs previously trusted the *leftmost*
  `X-Forwarded-For` token — a value the client fully controls, so an attacker
  could rotate it to dodge per-IP limits or forge audit trails. ServerKit now
  derives the client IP from one trusted seam (Werkzeug `ProxyFix`, gated by the
  new `TRUST_PROXY_HEADERS` / `TRUSTED_PROXY_HOPS` settings), taking the
  rightmost proxy-appended hop; a forged prefix is discarded. Enabled by default
  in the shipped proxied deploy; off for a directly-exposed dev server. **Note:**
  audit-log source IPs now record the real client instead of nginx's address —
  update any dashboards/alerts built on the old values.
- **Container CVE scanning & SBOM** — per-image vulnerability scans with grype
  and software bill-of-materials generation with syft.
- **Optional, hardened TLS** — best-effort HTTPS that never blocks an install
  (falls back to HTTP), a server-wide TLS 1.2+/AEAD-cipher floor applied at
  install and update, Cloudflare-aware nginx configs, automatic CAA records on
  certificate issuance, and HSTS gated on the operator's recorded SSL choice so
  HTTPS stays optional.
- **Encryption at rest** — system-setting secrets and DNS/cloud provider
  credentials sealed with Fernet; optional client-side backup encryption.

### Changed

- **Marketplace simplified to a two-tab store** — Browse and Installed. The
  Installed tab now lists *every* installed extension (built-in, registry,
  manual) with the full action set (Update / Configure / Enable–Disable /
  Uninstall with keep-vs-purge), replacing the old duplicate "Installed" and
  "ServerKit Plugins" tabs. Each row shows where it came from via a
  Built-in / Registry / Manual source badge, and registry installs are now
  stamped `source_type='registry'` in the database instead of masquerading as
  URL installs. Manual install (URL / host folder / zip upload) moved out of
  tab-land into an "Install manually" modal off the topbar, and Browse dropped
  its KPI strip, sidebar panels, and duplicate category filters in favor of
  search + one chips row (plus an All / By ServerKit / Community publisher
  filter).
- Overhauled the Docker UI (bulk container stats, compose listing) and migrated
  the frontend design system to SCSS `.ui-*` components.
- Unified the local dev launcher (`dev.sh` / `dev.ps1`).
- Agent capabilities and system info are cached to the database, surfaced in the
  System Status card, and re-sent on a periodic cadence.

### Fixed

- Resolved systemic silent failures: empty logs, dead WebSocket connections, and
  locked-out agents; stale "online" status is now auto-corrected.
- Hardened the installer: Docker install on Fedora/RHEL, SELinux + nginx
  reverse-proxy configuration, and low-RAM swap setup.
- Stopped dropping capability/sysinfo payloads on transient `/poll` failures.
- **Extension pages no longer ghost-render on every route** — the plugin
  loader's legacy auto-render (any plugin index default export renders
  globally) only excluded plugins declaring a *widget* contribution, so the
  WordPress and Cloudflare-ops builtins mounted their whole pages on every
  navigation: pages stacked into one view, the Cloudflare page fetched
  `zones/undefined`, and the WordPress sub-router swallowed the current URL as
  a site id. Legacy auto-render now skips any plugin declaring any
  contribution, and the surplus default exports were removed.
- **Live updates (WebSocket) actually work now** — the deployed gunicorn
  worker class (gevent-websocket) double-answered the WebSocket handshake
  against the app's `threading` async mode; browsers reported "Invalid frame
  header" and every panel silently fell back to polling. The service unit and
  Docker image now run a plain threaded worker (still a single process, which
  the agent gateway requires) with `simple-websocket` serving the socket.
- Settings → About now reports the real panel version on custom-directory
  installs and in Docker: version resolution honors the install location
  (`SERVERKIT_INSTALL_DIR`, rendered into the service unit from the installer's
  `SERVERKIT_DIR`), prefers the running tree over a stale `/opt/serverkit`, and
  the Docker image now ships the `VERSION` file (containers previously showed
  the `1.0.0` fallback). The File Manager's "Stack" quick link follows the same
  resolved install directory instead of assuming `/opt/serverkit`, and when
  browsing a remote agent the quick-access rail now matches that box: Linux
  agents get their agent config dir alongside the generic paths, Windows agents
  get `ProgramData\ServerKit\Agent` + `C:\Users` instead of Unix paths that
  don't exist there. Agents newer than v1.0.4 self-report their real install
  and config directories in `system_info` (stored on the server record,
  migration 047), and the rail prefers those over the installer conventions.
- **Scripts reliability (round 2)** — swept the whole install/update/uninstall/CLI
  shell surface for the "benign non-zero under `set -e`/`pipefail`" failure class
  behind the July 2 update outage:
  - **Data loss closed:** re-running `install.sh` over a live install no longer
    destroys `.env` (secret keys) and the SQLite database — it now detects the
    existing install correctly and carries live state across the re-deploy.
  - The default **no-domain curl-pipe install** works again (aborted at the nginx
    phase since v1.6.25); blank Enter at the interactive domain prompt no longer
    aborts; `--release` updates can complete (progress output was corrupting the
    captured tarball path); updates on boxes with no prior backups no longer
    report failure after succeeding.
  - Rollback can no longer run twice or abort mid-flight; uninstall never aborts
    mid-teardown and works on remnants-only boxes; `serverkit start/restart/logs/
    add-site` degrade gracefully on partial installs, non-systemd, and RHEL-family
    nginx layouts; `serverkit doctor` exits non-zero when checks fail.
  - Agent enrollment: version discovery now pages past 30 releases (panel releases
    could push every agent tag off page 1), the panel injects its known agent
    version into the served installer, and the downloaded agent binary is
    checksum-verified. Package installs, swap setup, Docker bootstrap, and the
    Rocky/RHEL 9 OpenSSL/OpenSSH upgrade ordering are hardened across distro
    families (incl. Fedora 41+ dnf5 and busybox/Alpine).

### Testing & Infra

- Added a Vagrant + Hyper-V runner (Debian/Fedora/Rocky) and a Multipass-based
  end-to-end harness that runs on Windows.
- Shell-script test harness: a fresh-minimal-box loop now proves every
  observation/discovery function in `install.sh` and `update.sh` survives a
  zero-app box under strict mode (the July 2 outage class), backed by a shared
  failing-stub library and new `test_cli.sh`/`test_agent_install.sh` suites
  (171 assertions total across the five suites).
- Scripts CI now performs a **real install + update end-to-end** on every PR
  touching `scripts/**` (the updater self-updates from `main`, so this gates the
  fleet), plus nightly release-tarball install and update-from-latest-release
  jobs and an advisory full-severity shellcheck pass.

---

## Released

Current development version: **1.6.7**. Recent point releases (`1.4.x` → `1.6.7`)
delivered the agent fleet, plugin system, and installer hardening listed above.
Until tagged panel releases land, consult `git log` and the
[GitHub releases page](https://github.com/jhd3197/ServerKit/releases) for the
detailed history.
