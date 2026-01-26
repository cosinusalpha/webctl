# webctl

**Browser automation for AI agents and humans, built on the command line.**

```bash
# 1. Install
pip install webctl

# 2. Auto-configure your agent (creates skills/prompts for all supported agents)
webctl init

# 3. Start browsing
webctl start
webctl navigate "https://google.com"
webctl snapshot --interactive-only
```

**`webctl init` automatically generates the skills and prompts your agents need to drive the browser.**

## Why CLI Instead of MCP?

MCP browser tools have a fundamental problem: **the server controls what enters your context**. With Playwright MCP, every response includes the full accessibility tree plus console messages. After a few page queries, your context window is full. This leads to degraded performance, lost context, and higher costs.

CLI flips this around: **you control what enters context**.

```bash
# Filter before context
webctl snapshot --interactive-only --limit 30      # Only buttons, links, inputs
webctl snapshot --within "role=main"               # Skip nav, footer, ads

# Pipe through Unix tools
webctl snapshot | grep -i "submit"                 # Find specific elements
webctl --format jsonl snapshot | jq '.data.role'   # Extract with jq
```

Beyond filtering, CLI gives you:

| Capability         | CLI                           | MCP                    |
|--------------------|-------------------------------|------------------------|
| **Filter output**  | Built-in flags + grep/jq/head | Server decides         |
| **Debug**          | Run same command as agent     | Opaque                 |
| **Cache & Cost**   | `webctl snapshot > cache.txt` | Every call hits server |
| **Script**         | Save to .sh, version control  | Ephemeral              |
| **Human takeover** | Same commands                 | Different interface    |

---

## Agent Integration

**Step 1: Install**

```bash
pip install webctl
webctl setup  # Downloads Chromium
```

**Step 2: Generate Skills/Prompts**

```bash
webctl init              # Project-level (recommended)
webctl init --global     # Global (works across all projects)
```

This creates:

- **Skills** for Claude Code and Goose (loaded on-demand when doing web tasks)
- **Lean prompts** for Gemini, Copilot, and Codex (always in context)

**Supported agents:**

| Agent            | Format | Location (project)                | Location (global)                         |
|------------------|--------|-----------------------------------|-------------------------------------------|
| `claude`         | Skill  | `.claude/skills/webctl/SKILL.md`  | `~/.claude/skills/webctl/SKILL.md`        |
| `goose`          | Skill  | `.agents/skills/webctl/SKILL.md`  | `~/.config/agents/skills/webctl/SKILL.md` |
| `gemini`         | Prompt | `GEMINI.md`                       | `~/.gemini/GEMINI.md`                     |
| `copilot`        | Prompt | `.github/copilot-instructions.md` | -                                         |
| `codex`          | Prompt | `AGENTS.md`                       | `~/.codex/AGENTS.md`                      |
| `claude-noskill` | Prompt | `CLAUDE.md` (legacy)              | `~/.claude/CLAUDE.md`                     |

**Why skills?** Skills are loaded on-demand - your agent only reads webctl instructions when actually doing web automation. This keeps your context clean for other tasks.

**Select specific agents:**

```bash
webctl init --agents claude,gemini    # Only Claude and Gemini
webctl init --agents claude-noskill   # Legacy CLAUDE.md format
```

**Step 3: Add to Config (optional)**

If your agent doesn't auto-detect the generated files, add this to your system prompt:

> For web browsing, use webctl CLI. Run `webctl agent-prompt` for instructions.

*Note: If a browser MCP is already configured, disable it to avoid conflicts.*

---

## Quick Start (Human Usage)

Verify the installation works by driving it yourself:

```bash
webctl start                    # Opens visible browser window
webctl navigate "https://example.com"
webctl snapshot --interactive-only
webctl stop --daemon            # Closes browser and daemon
```

<details>
<summary>Global installation with `uv`</summary>

```bash
uv tool install webctl
uv tool run webctl
```

</details>

<details>
<summary>Linux system dependencies</summary>

```bash
playwright install-deps chromium
# Or manually install libraries listed in Playwright documentation
```

</details>

---

## Core Concepts

### Sessions

Browser stays open across commands. Cookies persist to disk.

```bash
webctl start                    # Visible browser
webctl start --mode unattended  # Headless (invisible)
webctl -s work start            # Named profile (separate cookies)
```

### Element Queries

Semantic targeting based on ARIA roles - stable across CSS refactors:

```bash
role=button                     # Any button
role=button name="Submit"       # Exact match
role=button name~="Submit"      # Contains text (preferred)
```

### Output Control

```bash
webctl snapshot                                    # Human-readable
webctl --quiet navigate "..."                      # Suppress events
webctl --result-only --format jsonl navigate "..." # Pure JSON
```

---

## Commands

### Navigation & Observation

```bash
webctl navigate "https://..."
webctl back / forward / reload
webctl snapshot --interactive-only        # Buttons, links, inputs only
webctl snapshot --within "role=main"      # Scope to container
webctl query "role=button name~=Submit"   # Debug query
webctl screenshot --path shot.png
```

### Interaction

```bash
webctl click 'role=button name~="Submit"'
webctl type 'role=textbox name~="Email"' "user@example.com"
webctl type 'role=textbox name~="Search"' "query" --submit
webctl select 'role=combobox name~="Country"' --label "Germany"
webctl check 'role=checkbox name~="Remember"'
webctl press Enter
webctl scroll down
```

### Wait Conditions

```bash
webctl wait network-idle
webctl wait 'exists:role=button name~="Continue"'
webctl wait 'url-contains:"/dashboard"'
```

### Session & Console

```bash
webctl status                   # Current state & error counts
webctl save                     # Persist cookies now
webctl console --count          # Just counts by level (LLM-friendly)
webctl console --level error    # Filter to errors only
```

---

## Architecture

```
┌─────────────┐  Unix Socket   ┌─────────────┐
│   CLI       │ ◄────────────► │   Daemon    │
│  (webctl)   │   JSON-RPC     │  (browser)  │
└─────────────┘                └─────────────┘
      │                               │
      ▼                               ▼
  Agent/User                   Chromium + Playwright
```

- **CLI**: Stateless, sends commands to daemon
- **Daemon**: Manages browser, auto-starts on first command
- **Socket**: `$WEBCTL_SOCKET_DIR` or OS default (see below)
- **Profiles**: `~/.local/share/webctl/profiles/`

### Socket Paths

| Platform | Default |
|----------|---------|
| Linux | `/run/user/<uid>/webctl-<session>.sock` |
| macOS | `/tmp/webctl-<session>.sock` |
| Windows | `%TEMP%\webctl-<session>.sock` |

Override directory with `WEBCTL_SOCKET_DIR` environment variable.

---

## Security

### IPC Authentication

webctl verifies that CLI commands come from the same user as the daemon:

| Platform | Mechanism | Strength |
|----------|-----------|----------|
| Linux | `SO_PEERCRED` | Kernel-enforced UID check |
| macOS | `LOCAL_PEERCRED` | Kernel-enforced UID check |
| Windows | `SIO_AF_UNIX_GETPEERPID` + process token | Kernel-enforced SID check |

All platforms use kernel-level credential verification. This prevents other users from controlling your browser session.

Note: Root/Administrator can still access any user's session (OS limitation).

---

## Advanced Configuration

### Custom Browser

Use a custom Chromium binary (skips managed installs):

```bash
webctl config set browser_executable_path /path/to/chrome

# One-off override via environment:
WEBCTL_BROWSER_PATH=/path/to/chrome webctl start
```

Allow global Playwright even if versions mismatch (opt-in, use with care):

```bash
webctl config set use_global_playwright true
```

Clear overrides:

```bash
webctl config set browser_executable_path null
webctl config set use_global_playwright false
```

### Proxy Configuration

Configure HTTP/HTTPS proxy for corporate networks or CI environments.

**Via environment variables** (recommended for CI):

```bash
# Standard proxy env vars (auto-detected)
export HTTPS_PROXY=http://proxy.corp.com:8080
export NO_PROXY=localhost,*.internal.com
webctl start

# Or use webctl-specific var (highest priority)
export WEBCTL_PROXY_SERVER=http://proxy.corp.com:8080
```

**Via config file** (persistent):

```bash
webctl config set proxy_server http://proxy.corp.com:8080
webctl config set proxy_bypass localhost,*.internal.com

# For authenticated proxies
webctl config set proxy_username myuser
webctl config set proxy_password mypass
```

**Priority order**: `WEBCTL_PROXY_SERVER` > `HTTPS_PROXY` > `HTTP_PROXY` > config file

Check and clear settings:

```bash
webctl config show              # View all settings
webctl config set proxy_server null   # Clear proxy
```

### Container Deployment

Set `WEBCTL_SOCKET_DIR` to share the Unix socket between host and container (or between containers).

**Daemon in Container, Client on Host:**

```bash
mkdir -p /tmp/webctl-ipc

docker run -d --name webctl-daemon \
  -u $(id -u):$(id -g) \
  -v /tmp/webctl-ipc:/ipc \
  -e WEBCTL_SOCKET_DIR=/ipc \
  my-webctl-image python -m webctl.daemon.server

export WEBCTL_SOCKET_DIR=/tmp/webctl-ipc
webctl start && webctl navigate "https://example.com"
```

`-u $(id -u):$(id -g)` ensures the socket file is owned by your host user.

**Daemon and Client in Separate Containers:**

```bash
docker volume create webctl-ipc

docker run -d --name webctl-daemon \
  -v webctl-ipc:/ipc \
  -e WEBCTL_SOCKET_DIR=/ipc \
  my-webctl-image python -m webctl.daemon.server

docker run --rm \
  -v webctl-ipc:/ipc \
  -e WEBCTL_SOCKET_DIR=/ipc \
  my-webctl-image webctl navigate "https://example.com"
```

No UID matching needed - both containers run as the same user.

---

## License

MIT
