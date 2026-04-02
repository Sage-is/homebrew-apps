# homebrew-apps

**The Sage Homebrew Tap.** One command to install, one command to run.

```bash
brew install sage-is/apps/ai-ui
```

That's it. You're running [Sage AI UI](https://github.com/Sage-is/AI-UI) locally.

## What you get

**ai-ui** — a CLI that deploys Sage AI UI to your machine via Docker. No config files, no YAML, no 47-step setup guide. Just:

```bash
ai-ui start
```

Open your browser, you're in. Private, local, yours.

### Commands

| Command | What it does |
|---------|-------------|
| `ai-ui start` | Pull the image and start the UI (port 8080) |
| `ai-ui stop` | Stop everything cleanly |
| `ai-ui update` | Pull the latest image and restart |
| `ai-ui dev` | Clone the source and run with hot reload |
| `ai-ui open` | Open the UI in your browser |
| `ai-ui logs` | Tail the container logs |
| `ai-ui status` | Check what's running |

Pass `--port 3000` to `start` or `dev` if 8080 is taken.

### Dev mode

Want to hack on the UI itself?

```bash
ai-ui dev --dir ~/src/ai-ui
```

This clones the repo (if needed), mounts the source into the container, and gives you Vite HMR on port 5173. Edit, save, see changes — the usual.

## Versioned installs

Need to pin a major version? We support that.

```bash
brew install sage-is/apps/ai-ui@1
```

The main `ai-ui` formula always tracks the latest. Versioned formulas (`ai-ui@1`, `ai-ui@2`, ...) let you lock to a major release — useful when stability matters more than features.

## Dependencies

- **Docker** — the container runtime (Docker Desktop, OrbStack, or Colima all work)
- **Ollama** — for local LLM inference

`ai-ui` auto-detects and starts your Docker provider if it's not already running. On macOS it checks Docker Desktop, OrbStack, and Colima in that order. If nothing's installed, it offers to set up Docker Desktop via Homebrew.

## For contributors

This repo uses [git-flow-next](https://github.com/will-stone/git-flow-next) for releases. The Makefile handles the full workflow — version bumping, formula URL updates, sha256 computation, the works.

```bash
make help               # see all targets
make release            # interactive release flow
make patch_release      # start a patch bump
make release_finish     # merge, tag, push, update sha256
make check-upstream     # compare local version against upstream AI-UI
```

Release targets automatically update the formula URL, the CLI's VERSION string, and any matching versioned formulas. After tagging, `release_finish` waits for the GitHub archive to become available, computes the sha256, and commits it to both master and develop. Two commands, zero manual steps.

## License

[MIT](LICENSE)
