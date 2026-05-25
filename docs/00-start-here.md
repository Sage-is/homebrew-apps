# Start Here — What This Is, In Plain Words

Welcome. If you're reading this, you might be one of these people:

- A doctor, scientist, or CEO who just started writing code with help from AI.
- Someone who learned English last year and is now a teammate on a software team.
- Someone who left school early and learned everything they know by doing.
- A long-time coder who wants the team's shared way of thinking, in one place.

This page is for all of you. It's the **grounding** — the page that explains what we're trying to do here, why it matters, and the ideas that hold every other lesson together.

You don't need to know any code to read this. There's a small vocabulary at the bottom for the words we'll use later.

---

## A factory in Japan

In the 1970s, workers at Toyota would sometimes put a bolt in backwards. The part *looked* right until you tried to use it. Then it broke.

The normal reaction at most factories was: **yell at the worker.** "Be more careful." Hand out warnings. Fire someone.

Toyota tried something different. They asked a better question:

> *What part of the bolt and the hole made the wrong way possible in the first place?*

They re-shaped the hole. Now the bolt only fit one way. The worker could not do it wrong, even if they tried. The mistake disappeared — not because the worker became more careful, but because the wrong move stopped existing.

That idea has a name: **poka-yoke** (POH-kah YOH-kay). It's Japanese for *mistake-proofing*.

The word matters because it changes who is responsible. The mistake is not a moral failure of the worker. It is a design failure of the bolt.

> **Fix the bolt, not the worker.**

That is the whole idea, in one line.

---

## Why this matters more in software, not less

Here is the strange thing about software. The "bolt" is just text. We can re-shape it any time. We can write a small script that checks the shape automatically. We can build a guard into the tool that stops you right away when something is wrong.

In a factory, mistake-proofing costs metal and machine time. In software, it costs about ten minutes of typing.

So when something goes wrong in our code, our scripts, or our releases, the answer is almost never *"be more careful next time."* The answer is:

> **What guard could we add so this never happens again?**

That is the whole game.

---

## The three places mistakes hide

It helps to sort the kinds of mistake into three buckets. Every lesson in this folder is a story about one of them.

### 1. Writing the instructions

Code is just instructions for the computer. You write them in files. The files have names, folders, formats. A small typo in the wrong place can make the computer do nothing — or do the wrong thing, very fast.

**Example:** when we ship a new version, the version number has to appear in several files. If a person types it into each file by hand, sooner or later one of them will be wrong. The mistake-proof answer: write one small script that updates all of them at once. The human never types the number twice.

### 2. Running the instructions

Even good instructions can be run at the wrong time, on the wrong server, with the wrong settings. *"I thought we were on the test site."* *"I forgot to update the version."* *"I deployed to the live site by accident."*

The mistake-proof answer: give dangerous actions clear names. Make the person pick how much damage the action is allowed to do. Add a `--dry-run` flag that shows what *would* happen, without doing it.

> #NOTE: We may actually want to invert this so that dry-running is the default as it's more poka-yoke.

### 3. Tracking time

Software has versions. Versions become tags. Tags become releases. Releases get installed on real people's computers. If you can't tell which version is which, or which version a teammate is running, you can't help them.

The mistake-proof answer: label every release with a tag. Tags don't move. The version number lives in *one* place, and a script updates it for you.

---

## The one rule

> **Mistakes are design failures, not moral failures.**

If you take only one thing from this whole folder, take that.

When something breaks, the question is never *"who should have been more careful?"* The question is:

> *What part of the system made the wrong move possible — and how do we close that off?*

This is true for a senior engineer with twenty years of experience. It is also true for a doctor pasting code from an AI for the first time. The system is supposed to protect both of them. If it didn't, the system needs another guard.

---

## What's in the rest of this folder

Once you've finished this page, you can read the rest in any order.

- **[poka-yoke-lessons.md](poka-yoke-lessons.md)** — the master lesson plan. Twenty-two lessons in two parts. Part I covers how we shape our code and scripts. Part II covers how we use git as a time machine.
- **[versioned-formulas.md](versioned-formulas.md)** — Lesson 1 of the master plan, written long. A real example of mistake-proofing one specific pattern.

You don't have to memorize anything. The lessons exist so that next time a teammate spots a guard in the code and wonders *"why is this here?"* — they can find the answer.

---

## A small vocabulary list

Here are the words the rest of the lessons use. Skim it now. Come back when you need it.

- **Code** — text written in a programming language. Tells the computer what to do.
- **Script** — a small program, often used for one specific task. Most of ours are written in a language called `bash`,  and another language called `python`, and a few in `JavaScript`.
- **Terminal** / **CLI** — a window where you type commands instead of clicking buttons. "CLI" stands for "command-line interface."
- **Makefile** — a recipe book the computer reads. You type `make X` and it runs the recipe named `X`.
- **Repo** (short for "repository") — a folder that holds a project's code, tracked by git.
- **Branch** — a side timeline of a repo. You make a branch when you want to try something without changing the `master` version.
- **Commit** — a save point in your repo. You commit changes so you can come back to them later.
- **Tag** — a permanent label on one specific commit. We use tags like `v1.0.3` to mark releases.
- **Release** — a version of the software that we ship to users.
- **Master** / **main** — the "canonical" or official branch. The version of the truth that everyone trusts.
- **Pull / push** — *Pull* means "download other people's work into my repo." *Push* means "upload my work so other people can pull it."
- **PR** (pull request) — asking the team to review and accept your branch into the `master` for your repo.
- **Hotfix** — an emergency release to fix something broken on the live site or in a published app.

You're grounded. Open [poka-yoke-lessons.md](poka-yoke-lessons.md) when you're ready.
