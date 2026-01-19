# Changelog

## [0.2.0] - 2026-01-19

### Added

- `webctl init` now supports global installs (`--global`), richer agent selection defaults, and clearer dry-run/force reporting while creating skills/prompts for Claude Code, Goose, Gemini CLI, Copilot, Codex, and legacy Claude.
- Expanded skill and prompt templates with detailed query guidance, troubleshooting steps, and ready-to-run flows for AI agents.

### Changed

- `webctl init` defaults exclude the legacy Claude prompt unless explicitly requested and provide better feedback when files already contain webctl instructions.
- README and CLI guidance refreshed with clearer agent-integration steps, quick starts, and usage tips.
