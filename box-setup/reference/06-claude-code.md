# 06 — Claude Code on the node

**Why:** put an AI agent *on the box itself*, primed with this node's exact setup, so the operator
can troubleshoot by asking questions in plain language — the "the server helps fix itself" part.

> This guide uses **Claude Code**, but any comparable coding agent works — **local** (e.g. an
> Ollama/llama.cpp-backed CLI agent) or **cloud**. The only thing that matters is that it can read
> this repo's [`CLAUDE.md`](../CLAUDE.md) + [`skills/`](../skills/) for context. Adapt the install
> step below to your agent of choice.

## 1. Install (mentor, over SSH)

The native installer needs no Node.js:

```bash
curl -fsSL https://claude.ai/install.sh | bash
# adds ~/.local/bin/claude ; ensure it's on PATH:
grep -q '.local/bin' ~/.bashrc || echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
~/.local/bin/claude --version
```

## 2. Give it context (the skills bundle)

Clone this repo onto the box — its `CLAUDE.md` describes the node layout, ports, the file-only-log
trap, and the recovery skills, so the agent starts already knowing the setup:

```bash
git clone --depth 1 https://github.com/xAlisher/logos-node-starter.git ~/logos-node-starter
```

## 3. Log in (operator, with their OWN account)

Login is interactive and tied to the operator's Anthropic account, so **they** do it:

```bash
source ~/.bashrc
cd ~/logos-node-starter      # run from here so the agent reads CLAUDE.md + skills/
claude
```

- Pick a theme → choose **log in with your Claude/Anthropic account** (not "API key").
- Open the printed link, sign in (create an account at claude.ai if needed), approve, paste the
  code back.
- 💡 Pasting the code is far easier over **SSH from a desktop** than at the bare console.

## 4. Test it

Ask the on-box agent:

> "Is my Logos node healthy? Check it and explain what you find."

It should run `curl http://127.0.0.1:8080/cryptarchia/info`, check `systemctl --user status
logos-node dashboard`, know where the logs are, and explain the result — and it has the
crash-loop / recovery skills in [`../skills/`](../skills/) for when something actually breaks.
