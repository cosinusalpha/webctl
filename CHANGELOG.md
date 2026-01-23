# Changelog

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
