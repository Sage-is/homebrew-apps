# Poka-Yoke Lessons — How We Build So Mistakes Are Hard

> **New here?** Read [00-start-here.md](00-start-here.md) first. It explains what poka-yoke is, why we care, and gives you a small vocabulary so the rest of this page makes sense.

**Poka-yoke** (POH-kah YOH-kay) is a Japanese term from the Toyota factories. It means *mistake-proofing*. The idea: don't trust people to remember the rule — design the rule into the tool, so the wrong move feels harder than the right one.

This doc is a master lesson plan for how we mistake-proof our code, scripts, and releases. Every lesson is anchored in something we actually do today, with file pointers so you can read the real thing.

It's written so anyone on the team can follow — you don't need to be a senior engineer.

---

## The big idea

Mistakes don't usually come from people being careless. They come from systems that *make the wrong move easy*. A good poka-yoke turns the wrong move into a wall the person bumps into — not a cliff they fall off.

We use four kinds of guard:

1. **Shape guards** — the thing only fits one way (versioned filenames, typed protocols).
2. **Content checks** — read the truth from the source, not the label (git remote, hardlink count).
3. **Bounded retries** — when the world isn't ready yet, wait with a limit, then complain.
4. **Loud blast radius** — destructive actions name what they'll destroy *before* they do it.

Each lesson below shows one of these in action.

---

## Lesson 1 — Two living things, not one frozen one

**The mistake:** trying to use one file or one tag for both "latest" and "stay on v1."

**The fix:** keep two files. Both alive. Both kept up to date. One tracks latest, one is pinned to a major version.

**Where to see it:** [Formula/ai-ui.rb](../Formula/ai-ui.rb) and [Formula/ai-ui@1.rb](../Formula/ai-ui@1.rb). Full write-up in [versioned-formulas.md](versioned-formulas.md).

**Why it's a poka-yoke:** if we used a git tag for "stay on v1," users who installed it last year would be running a frozen recipe that no longer works on today's macOS. With two living files, the *software* is pinned but the *recipe* stays alive.

**The rule:** when you need both "newest" and "stable," make two living things, not one snapshot.

---

## Lesson 2 — Derive, don't hand-type

**The mistake:** hand-typing numbers that should be calculated.

**The fix:** let the script derive them from real input.

**Where to see it:** in [scripts/formula-helpers.sh:15](../scripts/formula-helpers.sh#L15), the `bump_version` helper does this:

```bash
local MAJOR=$(echo "$VER" | awk -F'.' '{print $1}')
local VFORMULA="Formula/ai-ui@${MAJOR}.rb"
```

When we ship v1.0.4, `MAJOR` becomes `1`, so only `ai-ui@1.rb` gets touched. When we ship v2.0.0, `MAJOR` becomes `2`, so `ai-ui@1.rb` is left frozen — exactly as users expect.

**Why it's a poka-yoke:** if we asked a human to "remember which `@N.rb` to bump," they would forget. Deriving from the version number means the right answer falls out of the math.

**The rule:** if the value can be calculated from something you already have, calculate it. Don't ask a human to remember.

---

## Lesson 3 — One verb per command

**The mistake:** 12-step release runbooks. Step 7 is "manually update sha256." Someone forgets step 7. Release breaks.

**The fix:** wrap the 12 steps into two verbs.

**Where to see it:** the [Makefile](../Makefile) — releases are two commands:

```
make patch_release      # bumps version, opens a release branch, commits
make release_finish     # merges, tags, pushes, computes sha256, syncs develop
```

Inside `release_finish`, the Makefile handles six things in order: clear stale git-flow state, run git-flow finish (with a manual fallback), push everything, wait for the GitHub archive, compute sha256, commit to master and sync develop.

**Why it's a poka-yoke:** the human doesn't have to remember the order. The Makefile remembers. The two verbs are the only thing a person has to learn.

**The rule:** if a release takes more than two commands, the script is hiding inside the human's head. Move it into the script.

---

## Lesson 4 — Detect by content, not by name

**The mistake:** trusting that a folder named `ai-ui` is actually our AI-UI repo. Or that a file named `Makefile` isn't secretly hardlinked to three other Makefiles.

**The fix:** read the truth from the source.

**Where to see it:**

- [ai-ui:46-49](../ai-ui#L46) — `is_repo_for` checks the **git remote**, not the folder name. So if a teammate cloned us into `~/code/sage-frontend`, we still find it.
- Global CLAUDE.md — before editing any Makefile, check `stat -f "%l %N"` for the **hardlink count**. If it's > 1, editing with Edit/Write would silently break the hardlink chain.

**Why it's a poka-yoke:** filenames lie. Folder names lie. Inodes and git remotes don't.

**The rule:** when correctness depends on what something *is*, check what it *is* — not what it's *called*.

---

## Lesson 5 — Default to "no"

**The mistake:** when a value is missing, assume the last thing the model (or user) said. This silently turns nothing into something.

**The fix:** start from "no" and require a "yes" to flip the bit.

**Where to see it:** in nlt-py's parser ([src/nlt/_parser.py:39](../../../nlt-py/src/nlt/_parser.py#L39) in the sister repo):

```python
selections = dict.fromkeys(tool_names, False)
```

Every tool starts at False. The LLM has to **explicitly** say YES to flip one to True. A missing tool stays unselected — it can't accidentally trigger.

The same project also takes the **last** YES/NO token per line (validated across 8,560 trials), so multi-token output is deterministic instead of random.

**Why it's a poka-yoke:** the worst kind of bug is the one where doing nothing does something. Default-deny means silence is silence.

**The rule:** when missing input could mean either "yes" or "no," always pick "no" unless the input *says* yes.

---

## Lesson 6 — Wait, don't fail

**The mistake:** assume the world is ready the instant you ask. The GitHub archive isn't ready yet → release script crashes → human has to babysit.

**The fix:** wait with a bounded retry, then complain clearly.

**Where to see it:** [Makefile:57-84](../Makefile#L57) — `wait_and_update_sha256` retries 5 times with increasing backoff (3s, 6s, 9s, 12s, 15s — 45s max). If the archive isn't ready by then, it prints a helpful message and tells the user how to recover manually.

**Why it's a poka-yoke:** there are two failure modes — *temporary* (just wait) and *real* (something's wrong). A bounded retry tells them apart automatically. The human only gets paged for real problems.

**The rule:** when something depends on an external system, retry with a budget. Don't retry forever. Don't retry zero times.

---

## Lesson 7 — Investigate before deleting

**The mistake:** see weird state, delete it, move on. Sometimes that weird state was the user's in-progress work.

**The fix:** check whether it's stale before removing.

**Where to see it:** [Makefile:48-53](../Makefile#L48) — `clear_stale_gitflow_state`:

```bash
if [ -f .git/gitflow/state/merge.json ] && [ ! -f .git/MERGE_HEAD ]; then
    echo "Clearing stale git-flow merge state from prior run..."
    rm -f .git/gitflow/state/merge.json
fi
```

It only deletes the leftover state file if there's no **real** merge in progress. The presence of `.git/MERGE_HEAD` means a real merge is happening — so we leave the state alone.

**Why it's a poka-yoke:** the wrong move (delete unconditionally) and the right move (check first) are nearly identical. The check is one extra `if`. Cheap insurance.

**The rule:** before removing anything that *might* be in-progress work, check the signal that says "real" vs "stale."

---

## Lesson 8 — Make blast radius explicit

**The mistake:** one "clean up" command that quietly does the most destructive thing it can.

**The fix:** name the modes by their blast radius. Make the user pick.

**Where to see it:** [scripts/nuke-sage](../scripts/nuke-sage) — three explicit modes:

| Mode | What it removes |
|---|---|
| `nuke-sage ai-ui` | Surgical — just one project's containers/volumes |
| `nuke-sage --all` | All Sage Docker + brew artifacts, but **keeps the config vault** |
| `nuke-sage --genesis` | Scorched earth — vaults, Docker provider, Ollama, the works |

There's also a sub-flag, `--include-docker-data`, that **only works with `--genesis`**. The script enforces this: try to combine it with `--all` and it errors out.

**Why it's a poka-yoke:** the user can't accidentally run "scorched earth" when they meant "surgical." The CLI flag *is* the consent.

**The rule:** when an action has multiple destructive levels, give each level its own name. Don't hide them behind the same command.

---

## Lesson 9 — Dry-run is a first-class citizen

**The mistake:** irreversible actions go straight to "do it."

**The fix:** every destructive command has a `--dry-run` that shows what *would* happen.

**Where to see it:** [scripts/nuke-sage](../scripts/nuke-sage) — `--dry-run` lists every container, volume, image, and brew formula it would remove, without removing anything.

**Why it's a poka-yoke:** dry-run is free. It costs the user 2 seconds and saves them an hour when they realize they were in the wrong directory.

**The rule:** if the action can't be undone, `--dry-run` is required.

---

## Lesson 10 — Label who runs what

**The mistake:** a multi-step plan says "deploy to staging" without saying *who* deploys. The human and the script both wait for each other.

**The fix:** every step is labeled with one of two tags.

| Tag | Meaning |
|---|---|
| `[WE]` | Sage.is AI runs this from the CLI in the current session |
| `[MANUALLY]` | The human does this by hand — dashboard click, browser flow, git tag/push |

We never use `[USER]` or `[ME]` — those framings imply hand-off. `[WE]` signals that the assistant runs the command but the human is in the loop.

**Where to see it:** the global CLAUDE.md (`~/.claude/CLAUDE.md`).

**Why it's a poka-yoke:** without labels, you get the two-people-in-a-doorway problem — both wait, no one moves. Labels make the next move unambiguous.

**The rule:** in any plan with mixed actors, every step gets an actor label.

---

## Lesson 11 — One source of truth

**The mistake:** the same number lives in three files. They drift apart. Bug reports say `1.0.2` but `--version` says `1.0.1`.

**The fix:** declare it once. Derive every other use.

**Where to see it:**

- [ai-ui:4](../ai-ui#L4) — `VERSION="1.0.3"` is a single shell variable in the CLI.
- [scripts/formula-helpers.sh:13](../scripts/formula-helpers.sh#L13) — `bump_version` updates the formula URL **and** that VERSION line in the same call.
- nlt-py — `__version__` lives in `__init__.py` and is kept in sync with `pyproject.toml` by a single tool.

**Why it's a poka-yoke:** humans copy-paste between files and forget one. Scripts that update everything in one shot don't forget.

**The rule:** if the same value lives in two places, find the script that keeps them in sync — or write it.

---

## Lesson 12 — Fail fast, fail clear

**The mistake:** the script keeps running when a prereq is missing. It eventually fails 80 lines later with a confusing error.

**The fix:** check prereqs first. Exit with a *helpful* message.

**Where to see it:**

- [Makefile:363-367](../Makefile#L363) — `require_gitflow_next` checks for the right git-flow flavor and tells the user the exact `brew install` command if it's missing.
- Every shell script in this repo starts with `set -euo pipefail` — fail on any error, on undefined variables, on broken pipes.

**Why it's a poka-yoke:** a failure 5 lines in is a learning moment. A failure 80 lines in is a debugging session.

**The rule:** check what you need *before* you start. When you fail, name the fix in the error message.

---

## Lesson 13 — Tolerate noise at the edges, fail loud in the middle

**The mistake:** treating LLM output like a strict API. Or treating internal data like it might be malformed.

**The fix:** be generous at the boundary, strict on the inside.

**Where to see it:** nlt-py's parser uses a three-tier fallback for list values: try JSON → try `ast.literal_eval()` → fall back to manual string split. Whatever the LLM throws at us, we try to make sense of it. But once a value is *parsed*, internal code treats it as a strict shape and fails fast on violations.

**Why it's a poka-yoke:** the boundary is where chaos lives. The inside is where you want predictability. Mixing the two — strict at the boundary, lenient inside — gets you the worst of both.

**The rule:** input from outside (LLMs, users, networks) gets fallback chains. Input from inside gets strict types and loud failures.

---

## Lesson 14 — Reset and re-run, don't hand-finish

**The mistake:** automation breaks halfway through. You finish the last 3 steps by hand. Ship it. Tomorrow no one knows the automation is broken.

**The fix:** when automation breaks, reset the state and re-run end-to-end. Fix the automation. Don't paper over it.

**Where to see it:** documented in [memory/feedback_always_test_automation.md](../../../.claude/projects/-Users-somma-Documents-Projects-GitHub-homebrew-apps/memory/feedback_always_test_automation.md). Also baked into the Makefile — `release_finish` has both a git-flow path *and* a manual fallback inside the same target, so the next person who runs it gets the working flow either way.

**Why it's a poka-yoke:** hand-finishing hides the bug. Resetting and re-running surfaces it.

**The rule:** if you finished it by hand once, that's a bug report — not a victory.

---

## Lesson 15 — Remember where the user works

**The mistake:** every command starts from zero. The user has to retype paths. They lose track of where their code lives.

**The fix:** a small registry that remembers — with warnings when its memory goes stale.

**Where to see it:** [ai-ui:30-43](../ai-ui#L30) — `~/.sage-is/projects` stores `project=path` lines. When `ai-ui dev` runs, it walks a five-step lookup: explicit `--dir`, env var, current directory, saved registry, fresh clone. Each step prints what it found.

When the saved path no longer exists, the script *says so* — it doesn't silently re-clone:

```
Heads up — your AI-UI source was at ~/code/ai-ui, but that directory is gone.
  (If you moved it, you can point us there with: ai-ui dev --dir /new/path)
```

There's also an ephemeral-path warning: if your code is under `/tmp` or `/var/folders`, you get a heads-up that it may not survive a reboot.

**Why it's a poka-yoke:** silent re-cloning destroys uncommitted work. Loud "your code is gone" gives the user a chance to recover it.

**The rule:** when remembered state goes missing, say so. Don't quietly start over.

---

---

# Part II — Git: Being Masters of Time Itself

Most tools let you do a thing. Git lets you *undo* a thing — and retry it, in a parallel timeline, without anyone else seeing. Used well, git turns every dangerous move into a safe experiment. That's the deepest poka-yoke we have.

This part of the lesson plan covers how we use git not as a save button, but as a time machine.

The trick is that git gives you two kinds of time:

- **Local time** — the history on your laptop. Yours to rewrite.
- **Shared time** — the history on `origin`, the history other people have pulled. Off limits to rewrite.

Most git mistakes come from confusing the two. The lessons below are how we keep them straight.

---

## Lesson 16 — Branches are alternate timelines

**The mistake:** making a risky change directly on master.

**The fix:** open a branch. The branch is its own timeline. If the experiment fails, delete the timeline. Nothing was harmed.

**Where to see it:** every release in this repo lives on a `release/X.Y.Z` branch. The branch is born when `make patch_release` runs, and it dies when `make release_finish` merges it back. Master never sees the half-finished version. Same for `feature/*` branches and `hotfix/*` branches — see [Makefile:267-345](../Makefile#L267).

**Why it's a poka-yoke:** master is the canonical "now." Branches are sandboxes. Opening a sandbox costs one command. Breaking master costs a war room.

**The rule:** any change with a chance of being wrong starts on a branch.

---

## Lesson 17 — Commits are save points; the reflog is your insurance

**The mistake:** making one giant commit at the end. Or — worse — running `git reset --hard` and thinking your work is gone.

**The fix:** small commits often. And know that `git reflog` exists. Even after a hard reset, your old commits aren't deleted — they're in the reflog for ~30 days. You can find them and bring them back.

**Where to see it:** every release commit in our flow is a small, named save point — "Bump version to 1.0.3," "Update sha256 for v1.0.3." Each one is a moment you can return to with `git checkout`.

**Why it's a poka-yoke:** the scariest mistake — "I just deleted hours of work" — is almost never real. Git keeps a hidden ledger. The reflog is what makes git mistakes recoverable.

**The rule:** commit early, commit often. When something goes wrong, check `git reflog` before you panic.

---

## Lesson 18 — Rewrite the past you own. Never rewrite the past you share.

**The mistake:** force-pushing master. Rewriting shared history. Other people's clones now disagree with the source of truth — and the only fix is for everyone to re-clone.

**The fix:** two timelines, two rules.

| Timeline | Rule |
|---|---|
| **Local** (your branch, before pushing) | Rewrite freely. `git rebase`, `git commit --amend`, `git reset --soft` are fine. |
| **Shared** (anything pushed and pulled by others) | Never rewrite. Add a *new* commit that undoes the old one. That's `git revert`. |

**Where to see it:** our git-flow finish targets in [Makefile](../Makefile) only ever *add* commits to master — they never rewrite it. Every "merge" adds history. Nothing changes existing history.

**Why it's a poka-yoke:** rewriting shared history is the one git move that genuinely destroys other people's work. Treating shared history as immutable is the only reliable way to prevent that.

**The rule:** rewrite locally to make your story clean. Never rewrite history that lives on the shared timeline.

---

## Lesson 19 — Tags are permanent landmarks

**The mistake:** "let me check out the build that worked last Tuesday" — followed by 40 minutes of git archaeology.

**The fix:** tag the moment. `v1.0.3` is now a permanent name for an exact tree state.

**Where to see it:** every release in this repo gets a tag. The Homebrew formula points at that tag's URL ([Formula/ai-ui.rb:4](../Formula/ai-ui.rb#L4)). Anyone can `git checkout v1.0.3` and see exactly what shipped.

**Why it's a poka-yoke:** tags turn "find me the commit" into "I already named it." Search becomes lookup.

**The rule:** every release gets a tag. Tags don't get moved. Ever.

---

## Lesson 20 — Worktrees: visit two timelines at once

**The mistake:** stash your work, switch branches to fix a bug, come back, restore stash, fight conflicts.

**The fix:** `git worktree add ../hotfix master` — a second copy of the repo, on a different branch, in a different folder. Edit both at once. Delete the worktree when you're done.

**Why it's a poka-yoke:** stashing and switching is where work gets lost. Worktrees let you keep both timelines alive on disk at the same time.

**The rule:** when you need to work on two branches at once, use a worktree — not a stash.

---

## Lesson 21 — Bisect: binary-search through time

**The mistake:** a bug appeared "sometime in the last two weeks." You read every commit. You guess. You're wrong.

**The fix:** `git bisect` — git binary-searches through commits, asking you "is this one good?" each time. In about seven questions you've narrowed 100 commits down to the one that broke things.

**Why it's a poka-yoke:** finding the bad commit was a memory and intuition task. Bisect turns it into a yes/no game. The human can't get it wrong as long as they answer each question honestly.

**The rule:** when a bug's origin isn't obvious, don't read commits — bisect them.

---

## Lesson 22 — Blame is for context, not for blame

**The mistake:** see weird code, delete it. Turns out it was load-bearing — fixing a bug from 2023 that you don't know about.

**The fix:** `git blame` (or `git log -p` on the file) tells you *why* that line was written, by reading the commit message that introduced it. The author noted the constraint. You read it. You make the right call.

**Where to see it:** every commit in this repo has a real subject line — "Fix grammatical error in README.md," "Refactor nuke-sage-ai script into nuke-sage for universal cleanup." The history is readable, so blame produces useful context.

**Why it's a poka-yoke:** code without history looks arbitrary. Blame restores the *why*, which is exactly what you need before deleting something.

**The rule:** before removing code that looks weird, blame it. The commit message will usually tell you whether you can.

---

## The git philosophy in one sentence

> **The present is the only place you act. The past is a library. The future is a sandbox.**

- **Master is the present.** It's the one place "now" lives. We add to it, never edit it.
- **History is the library.** Tags, log, blame, bisect, reflog — all read-only views of moments past.
- **Branches are sandboxes for the future.** We try things there. If they don't work, the sandbox is just deleted.

Every git poka-yoke we use is some version of *keep these three roles from blurring*.

---

## How to add a new lesson

When you find a new mistake worth preventing:

1. **Name the mistake** — the wrong move someone made (or almost made).
2. **Find the cheapest guard** — usually a shape, a content check, a retry budget, or a confirmation.
3. **Put the guard in the tool, not the runbook** — runbooks are read once and forgotten.
4. **Write a lesson here** — short, with file pointers, in the same shape as the lessons above.

Lessons we *don't* keep: ones that say "be careful." Careful is not a poka-yoke. Careful is a runbook in someone's head.

---

## See also

- [versioned-formulas.md](versioned-formulas.md) — full write-up of Lesson 1
- [../Makefile](../Makefile) — release flow
- [../scripts/formula-helpers.sh](../scripts/formula-helpers.sh) — `bump_version` and `create_versioned_formula`
- [../scripts/nuke-sage](../scripts/nuke-sage) — blast-radius modes
- [../ai-ui](../ai-ui) — the CLI, including the project registry
- `~/.claude/CLAUDE.md` — global conventions ([MANUALLY]/[WE], hardlinks, ai-coauthor)
