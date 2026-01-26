# Changelog

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
