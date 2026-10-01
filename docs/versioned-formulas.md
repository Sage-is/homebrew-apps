# Why we have `ai-ui` *and* `ai-ui@1`

A short lesson, written so anyone on the team can follow it. The goal is **poka-yoke** — make the right thing easy and the wrong thing hard.

> New to this folder? Read [00-start-here.md](00-start-here.md) first.
> Part of the [poka-yoke master lesson plan](poka-yoke-lessons.md). This page is Lesson 1, written long.

> **Since 2026-09-29 the CLI's version is the AI-UI version it pins** (3.2.0 pins AI-UI 3.2.0; see the README, "One version number"). The lesson below still holds, with AI-UI's major versions: `ai-ui@1` stays frozen at CLI 1.0.4, and the next versioned formula is `ai-ui@3`.

## Two files, one tool, two install options

In [Formula/](../Formula/) you'll see two recipes:

- **`ai-ui.rb`** — the "always give me the newest version" install. Today that's v1.0.3. Tomorrow it might be v2.0, then v3.0.
- **`ai-ui@1.rb`** — the "keep me on version 1, please" install. The `@1` is a label that says *stay on the v1 family forever*.

Both files describe the same tool. They differ in two ways:

| | `ai-ui.rb` | `ai-ui@1.rb` |
|---|---|---|
| Class name | `AiUi` | `AiUiAT1` |
| Extra line | — | `keg_only :versioned_formula` |

That `keg_only` line tells Homebrew "don't put this on the main path — let the regular `ai-ui` win by default." That way both can be installed at once without fighting.

## Why two files? Why not just use git tags?

Homebrew already uses tags — just not for picking which version to install.

**Tags answer "where's the code?"**
Look at the `url` line in [Formula/ai-ui.rb](../Formula/ai-ui.rb). It points to a `vX.Y.Z.tar.gz` — that's a git tag on our source repo. Homebrew downloads that tagged snapshot, builds it, and installs it. So tags are doing real work.

**Files answer "which version do I want?"**
When you type `brew install ai-ui`, Homebrew looks in the tap (this repo) for a file named `ai-ui.rb` and runs it. One name, one file, one recipe.

If Homebrew let you say `brew install ai-ui@v1.0.3`, it would have to:

1. Go look in our git history for that tag,
2. Check out the version of `ai-ui.rb` from back then,
3. Hope the recipe from months ago still works on today's macOS, today's Docker, today's Ollama.

That last part is the trap. Old recipes break. They reference dependencies that have moved, URLs that have rotted, build flags that have changed. Homebrew's promise is "this install will work today" — and a frozen recipe from a year ago can't promise that.

**Two files = two living recipes.**
Both `ai-ui.rb` and `ai-ui@1.rb` are kept up to date. We can fix bugs in the v1 recipe even after v2 ships — a new dependency name, a new checksum, whatever. The version of the *software* is pinned, but the *recipe* stays alive. Tags can't do that. A tag is frozen forever.

In short:

- **Tag** = a snapshot of the software.
- **Formula file** = a living recipe that knows how to install it today.

## When does the difference start mattering?

The day we release v2.0.0.

- `ai-ui.rb` gets bumped to v2.0.0.
- `ai-ui@1.rb` stays frozen at the last v1.x release.

Anyone who typed `brew install ai-ui@1` keeps running v1. Anyone who typed `brew install ai-ui` gets v2 on their next upgrade.

## The poka-yoke

The trap: if our release script bumped *every* `ai-ui@N.rb` file every time we shipped, the "stay on v1" promise would break the moment v2 went out.

The safety net lives in [scripts/ai-ui-formula.sh](../scripts/ai-ui-formula.sh). It updates only `Formula/ai-ui.rb` and `Formula/ai-ui@<major of the new release>.rb`. When a release starts a new major, it first copies the current formula to `ai-ui@<old major>.rb`. That copy carries the class `AiUiAT<N>` and the `keg_only :versioned_formula` line, and the script never touches it again. The `major` comes from the **new** version being released:

```bash
for f in "$formula" "Formula/ai-ui@${major}.rb"; do
  [ -f "$f" ] || continue
  sed -i '' \
    -e "s|^  url \".*\"|  url \"$url\"|" \
    -e "s|^  sha256 \".*\"|  sha256 \"$sha\"|" \
    -e '/^  version "/d' -e '/^  revision /d' \
    "$f"
  echo "  $f -> AI-UI $version"
done
```

So when we ship v1.0.4, the script updates `ai-ui.rb` and `ai-ui@1.rb`. When we later ship v2.0.0, it updates only `ai-ui.rb` — `ai-ui@1.rb` is correctly left alone, frozen at the last v1.x.

## Rules to keep this working

1. **Never hand-edit `ai-ui@N.rb` to point at a different major version.** Run `make ai_ui_formula` after an AI-UI release. That target runs `scripts/ai-ui-formula.sh`, which knows the rule. The old targets (`patch_release`, `minor_release`, `major_release`) no longer exist.
2. **Versioned formulas are created by `make ai_ui_formula`, at the first release of a new major.** The script runs the copy step there. Never create them by hand, and don't `cp` files manually.
3. **If you're tempted to `sed -i` across all formula files at once, stop.** That's the exact mistake the helper is preventing. The whole point of `ai-ui@1.rb` is that it gets *left alone* when the main formula moves to v2.
4. **Old versioned formulas can still get bug fixes** (sha256 corrections, dependency renames) — just not version bumps.

## See also

- [Formula/ai-ui.rb](../Formula/ai-ui.rb) — the rolling latest
- [Formula/ai-ui@1.rb](../Formula/ai-ui@1.rb) — the v1 pin
- [scripts/ai-ui-formula.sh](../scripts/ai-ui-formula.sh) — where the safety net lives
- [Makefile](../Makefile) — the ai_ui_formula target that runs the safety net
