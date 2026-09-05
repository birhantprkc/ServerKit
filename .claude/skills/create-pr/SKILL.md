---
name: create-pr
description: Draft or revise a local ServerKit pull request title and description from the branch diff. Use for PR descriptions and dev-to-main promotion notes; keep issue replies, advisories, and marketing copy in their own formats.
---

# Create PR Description

Write for a reviewer who has not seen the conversation. Explain the problem,
the resulting behavior, and the evidence needed to assess the change. Save a
local Markdown draft; creating or updating a GitHub PR is a separate action.

## Establish the comparison

Read the repository instructions and any PR template. Inspect the branch,
working-tree status, and available refs before selecting a base:

```bash
git status --short
git branch --show-current
git branch -a
```

Use the user's specified base or the existing PR's base when available. For
ServerKit's normal workflow, feature branches target `dev`; `dev` promotions
target `main`. Check `.github/workflows/main-promotion.yml` if the requested
target conflicts with that flow. Explain the conflict without changing the
branch or silently choosing a different target. Ask only if the intended
comparison cannot be resolved from the request and repository context.

Resolve the base to an available ref, preferring the intended remote-tracking
ref when present. Record the exact base and HEAD commit IDs used. Local refs
can be stale; do not describe them as current remote state without checking.

With `BASE_REF` replaced by that resolved ref, inspect:

```bash
git log BASE_REF..HEAD --oneline
git diff BASE_REF...HEAD --stat
git diff BASE_REF...HEAD
git log BASE_REF..HEAD --format='%aN <%aE>'
```

The three-dot diff compares HEAD to the merge base, so base-only changes do
not enter the description. Read large diffs by area, including relevant
callers when needed to understand behavior. Use commits for context and
authorship; describe the final diff, including fixes made during review.

Uncommitted edits do not belong to the branch comparison unless the user asks
to include them. If included, inspect staged and unstaged diffs and identify
that scope in the handoff. If the comparison is empty or refs are missing,
report that instead of inventing a PR. Do not change code or run a broad audit
as a prerequisite to writing the description.

## Choose the amount of detail

- A small fix usually needs a title, one short paragraph, and relevant
  validation. Two sentences can be enough. Do not pad it to a sentence quota.
- A feature or mixed change may need a short summary and a few bullets for
  distinct outcomes. Skip Highlights if it would repeat the summary.
- A large promotion may need sections grouped by behavior or subsystem.
  Cover material changes and risks, but group mechanical edits instead of
  writing a file-by-file inventory. Link an existing detailed review or plan
  when it helps. Do not invent a shared story for unrelated changes.

Use `<details><summary>Technical changes</summary>` only when a substantial
implementation appendix helps. Put blank lines around Markdown inside it.
Keep upgrade actions, breaking changes, security limitations, and validation
visible outside the accordion. Omit empty sections and template placeholders.

## Write the description

**Title.** Start the file with `# <Title>`. Use a plain sentence-case title that
names the main behavior, preferably under 70 characters, with no trailing
period. Preserve the project's preference for no `feat:`, `fix:`,
`type(scope):`, or square-bracket tags. A subsystem label is fine when useful.
This heading supplies the eventual PR title; the remaining content is the body.

**Opening.** Lead with the concrete problem and what changes. For a bug, name
the trigger and the before/after behavior. For a feature, say what the user can
now do. Explain a design choice when its reason matters to review and is
supported by the diff or recorded context. Do not invent motives or rejected
alternatives. Move protocol timings and long identifier lists into details.

**Voice.** Use direct, conversational prose. Do not manufacture personality
with a joke, metaphor, apology, or dramatic reveal. Cut stock openers such as
"This is the big one," "Turns out," "Three gremlins," and "This PR builds the
bridge." Replace praise such as "real," "proper," "honest," "seamless," or
"robust" with the behavior that earns it. Avoid repeated "one door" or
"same story" framing, "not X, but Y" slogans, and "riding along" transitions.
Keep a technical contrast when it explains an actual compatibility boundary
or tradeoff. Technical qualifiers and terms such as "read-only" or "atomic"
are useful when precise; this is an editorial pass, not a word blacklist.

**Evidence and limits.** Tie factual claims to the inspected diff, supplied
evidence, or an identified check. Keep distinctions between implemented,
verified, and expected behavior. "Reject pending-MFA tokens on login-link
routes" is more precise than "2FA can no longer be bypassed." Describe the
covered platforms and paths instead of promising future compatibility or
claiming a bug cannot recur. Date measurements and state what they measure;
file counts and line reductions usually belong out of the summary. Do not
infer performance gains from fewer queries or smaller source files alone.

**Validation.** Include concise, relevant results when available, even for
internal changes. Name what was checked and any material gap. Added tests or
CI configuration are changes, not proof that checks passed. Do not invent
results, imply a historical run covered the current HEAD, or rerun expensive
suites just to fill this section. If no execution evidence is available, say
that validation was not run or results were not available, as applicable.

**Upgrade and security notes.** Make required migrations, session invalidation,
new permission requirements, data-retention changes, and remaining limitations
easy to find. Preserve the distinction between JWT-only and API-key-capable
routes; an authentication decorator change can expand access. State the
specific boundary and any known exception. Do not turn a security fix into
a blanket assurance about the product.

**Contributors.** For multi-author work, credit contributors other than the
repo owner in a short section. Exclude bots and consolidate known aliases.
Use a verified GitHub handle when available, otherwise the author's name;
do not guess handles from email addresses or expose their email addresses.
Keep contribution notes tied to the commits or supplied attribution.

No emoji, generated-by footer, or commentary about making the draft sound human.

## Examples

These edits illustrate scope and tone using the local archive. They do not
establish fresh validation of those historical changes.

**Small change** (`.pr/2026-08-21-6.md`):

```markdown
# Consolidate the shared error state

`ErrorBoundary` now renders the shared `ErrorState` component. The extension
SDK exports the same component, preserving the boundary's reporting, retry,
and route-reset behavior.
```

Add actual validation evidence or an accurate statement that it is unavailable.
This change does not need Highlights and a technical accordion.

**Bounded security claim** (`.pr/2026-09-05.md`): replace "Two-factor
authentication can no longer be stepped around" with "Pending-2FA tokens expire
after five minutes and cannot administer login links. Redeeming a link requires
the target account's TOTP step when enabled."

**Direct explanation** (`.pr/2026-08-21-7.md`): replace "The one design call
worth flagging" and its long setup with "Resource pickers use an extended mode
of `/search` to share the command palette's authorization checks. Pickers opt
into cursor paging; the palette keeps its five-results-per-type limit."

## Save and review

For a new draft, create `.pr/` if needed and write `.pr/YYYY-MM-DD.md` using the
current local date. If it exists, use the next available suffix (`-2`, `-3`,
...). When asked to revise
an existing draft, update that file instead of generating another dated copy.
Do not rewrite other historical drafts unless requested.

Read the finished draft against the final diff. Remove repeated explanations,
unnecessary setup, and claims stronger than the evidence. Check that required
upgrade actions remain visible and that links, paths, credit, and validation
match the chosen scope. Do not copy changing counts or release status from
an older `.pr` file without checking them.

Release behavior belongs to the workflows. If bump intent matters, inspect
`.github/workflows/version-bump.yml`; it reads the commit message pushed to
`dev`. A PR title can become that message through a squash or merge workflow,
so do not promise the title can never affect versioning. Drafting a description
does not authorize changing commit messages or `VERSION`.

Return the draft's path and a brief handoff identifying the comparison used
and any material evidence gap. Stop at the local draft unless the user has
also authorized publishing or updating the PR. Honor that authorization when
present; otherwise do not push, open/edit a remote PR, or post comments.
