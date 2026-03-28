# Changelog

## [0.4.0] - 2026-03-28

This release started as an attempt to add ad-blocking and automatic cookie consent handling using techniques from uBlock Origin and cookie consent extensions. That approach didn't pan out, but the effort shifted focus toward benchmarking webctl against Vercel's agent-browser — which revealed concrete opportunities to reduce round-trips, cut token usage, and simplify the agent-facing API. The result is a leaner, faster interface that lets agents do more per turn.

### Added

- Structured data extraction on `navigate` (JSON-LD, Open Graph, meta tags) for token-efficient page understanding without full snapshots.
- `--snapshot`, `--grep`, `--read`, and `--search` flags on `navigate` for flexible observation modes.
- `--snapshot` and `--grep` flags on `click` and `type` for act+observe in one turn.
- Batch `do` command for executing multiple actions in a single call.
- Implicit element resolution — use text descriptions, @refs, or query syntax instead of exact selectors.
- Automatic fallbacks: cookie banner dismiss, scroll-to-find, overlay retry.
- Auto-start sessions on first command (no mandatory `webctl start`).
- Readability.js integration for `--read` mode (clean article extraction).
- Landmark-aware filtering for smarter snapshot output.
- Network idle detection improvements for media streams and WebSockets.
- `WEBCTL_LOG` env var to record command transcripts in shell-transcript format.
- `prompt-secret` command for human-in-the-loop credential entry.

### Changed

- `webctl stop` now closes both browser and daemon by default. Use `--keep-daemon` to preserve the daemon.
- `navigate` returns structured data + page summary by default (no a11y dump). Use `--snapshot` for full accessibility tree.
- `--read` output capped at 16,000 chars / 200 lines.
- SKILL.md rewritten from 590 to 173 lines with decision matrix for choosing the right approach.
- Cookie banner auto-dismiss simplified from regex patterns to CSS selector matching (40+ CMP platforms).

### Fixed

- `webctl doctor` now reports default mode (attended/headless), domain policy status, and warns when no display server is detected in attended mode (#14).
- Domain policy block errors now include the configured allow/deny list for easier debugging (#9).
- Browser launch failures now surface actionable messages for sandboxed environments (Codex, Docker, CI) with specific remediation steps (#13).

### Removed

- `--daemon` flag on `stop` (replaced by inverse `--keep-daemon`).

## [0.3.1] - 2026-01-26

### Added

- Configurable browser selection: set `browser_executable_path` (or `WEBCTL_BROWSER_PATH`) to use a custom Chromium, and `use_global_playwright` to opt into global Playwright even when revisions mismatch.
- Improved browser checks with explicit remediation guidance and support for skipping managed installs when a custom executable is provided.
- Proxy configuration support: set `proxy_url`, `proxy_username`, and `proxy_password` to enable proxy usage in browser sessions, with enhanced CLI commands for proxy management.

### Changed

- Browser setup, doctor, and start now honor custom/global selections and surface clearer version-mismatch warnings and fix commands.
- CLI commands enhanced to support proxy configuration options and better proxy-related feedback.

## [0.3.0] - 2026-01-23

### Added

- Peer credential checks for Unix domain sockets on Linux and macOS, with tests to guard handshake integrity.
- CLI output refinements that trim noisy context and add accessibility-friendly formatting for terminal usage.

### Changed

- Transport now uses Unix domain sockets exclusively; deprecated TCP/named pipe paths were removed and connection error handling tightened.
- UDS server/client paths now run non-blocking to reduce stalls and improve IPC robustness.
- Windows socket handling cleaned up with clearer structure and better error management.
- Broad code cleanup removing unused dependencies, dead helpers, and stray comments for a leaner codebase.

## [0.2.0] - 2026-01-19

### Added

- `webctl init` now supports global installs (`--global`), richer agent selection defaults, and clearer dry-run/force reporting while creating skills/prompts for Claude Code, Goose, Gemini CLI, Copilot, Codex, and legacy Claude.
- Expanded skill and prompt templates with detailed query guidance, troubleshooting steps, and ready-to-run flows for AI agents.

### Changed

- `webctl init` defaults exclude the legacy Claude prompt unless explicitly requested and provide better feedback when files already contain webctl instructions.
- README and CLI guidance refreshed with clearer agent-integration steps, quick starts, and usage tips.
