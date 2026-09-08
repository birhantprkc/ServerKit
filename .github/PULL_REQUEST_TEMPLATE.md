## Description
<!-- Provide a brief summary of the changes and the motivation behind them. -->

## Related Issues
<!-- Link any related issues using keywords like Fixes #123 or Closes #456. -->

## Type of Change
- [ ] 🐛 Bug fix (non-breaking change which fixes an issue)
- [ ] 🚀 New feature (non-breaking change which adds functionality)
- [ ] ⚠️ Breaking change (fix or feature that would cause existing functionality to not work as expected)
- [ ] 📝 Documentation update
- [ ] 🎨 UI/UX improvement

## How Has This Been Tested?
<!-- Describe the tests that you ran to verify your changes. -->

## Checklist
- [ ] My code follows the style guidelines of this project
- [ ] I have performed a self-review of my own code
- [ ] I have commented my code, particularly in hard-to-understand areas
- [ ] I have made corresponding changes to the documentation
- [ ] I have added a concise user-facing note under `Unreleased` in `CHANGELOG.md`, or explained why this change needs no release note
- [ ] My changes generate no new warnings
- [ ] I have added tests that prove my fix is effective or that my feature works
- [ ] New and existing unit tests pass locally with my changes

## Shell scripts only (`install.sh`, `uninstall.sh`, `serverkit`, `scripts/**`)
<!-- These scripts run under `set -Eeuo pipefail` on production boxes, and the
     updater self-updates from main — a broken merge is live on every install
     instantly. Skip this section if the PR doesn't touch them. -->
- [ ] Every new pipeline / command substitution can **benignly fail** (empty dir,
      missing conf, zero matches, dead service) without aborting the script —
      observations are guarded; `halt` is reserved for "cannot safely continue"
- [ ] No `[ … ] && cmd` as a function's **last statement** (returns 1 when the
      test is false and kills unguarded callers) — use `if`
- [ ] Bare `x="$(cmd)"` assignments only wrap **infallible** producers; anything
      that can fail sits inside `if x="$(cmd)"; then`
- [ ] Functions whose stdout is **captured** send progress output to stderr
- [ ] "Warn and continue" helpers are errexit-immune **internally**, and
      teardown/rollback paths never abort over a single failed step
- [ ] Every new observation/discovery/snapshot/report function is appended to
      the **fresh-box loop** in its test suite, and I named which degenerate
      fixture covers the new failure mode
- [ ] Multi-statement test bodies in tested contexts (`if ( … )` subshells,
      the left side of `||`) are **`&&`-chained** — bash suppresses `set -e`
      there even when re-armed inside, so sequential statements assert only
      the last one
- [ ] The fix/feature ships with its **proving test in the same commit**
