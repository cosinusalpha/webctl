---
name: webctl
description: Browser automation via CLI. Use when browsing websites, filling forms, extracting data from web pages, taking screenshots, or automating web interactions. Preferred over MCP browser tools for better context control.
allowed-tools: Bash(webctl *), Bash(webctl), Read
---

# webctl - Browser Automation CLI

## Workflow

```bash
# 1. Navigate — returns structured data + interactive elements with @refs
webctl navigate "https://example.com"

# 2. Interact using @refs or text descriptions
webctl click @e3
webctl type "Email" "user@example.com"

# 3. End session
webctl stop
```

## Choosing the Right Approach

| Goal | Command |
|------|---------|
| Browse / interact with a page | `navigate URL` — returns structured data + a11y snapshot with @refs |
| Read article content | `navigate URL --read` — returns readable markdown |
| Search a website | `navigate URL --search "query"` — types query + returns results |
| Filter specific elements | `navigate URL --grep "€\|price"` — filtered a11y snapshot |

## Commands (10 total)

### navigate - Go to URL
```bash
webctl navigate "https://example.com"                        # structured data + a11y snapshot with @refs (default)
webctl navigate "https://example.com" --grep "€|price"       # filtered a11y snapshot
webctl navigate "https://example.com" --read                  # readable text content
webctl navigate "https://duckduckgo.com" --search "query"     # search + results snapshot
```

The default returns **structured data** (JSON-LD, Open Graph — price, rating, etc.) plus the full a11y snapshot with @refs for interaction.

### snapshot - Re-scan current page
```bash
webctl snapshot                     # all elements with @refs (default)
webctl snapshot --interactive-only  # just buttons/links/inputs
webctl snapshot --read              # readable text content
webctl snapshot --count             # just counts (zero context)
webctl snapshot --grep "pattern"    # filter by regex
webctl snapshot --within "role=main"  # scope to container
```

### click - Click an element
```bash
webctl click @e3                    # by @ref (fastest)
webctl click "Submit"               # by text description
webctl click 'role=button name~="Submit"'  # by query (fallback)
webctl click "Submit" --snapshot    # click + return new page state
webctl click "Next" --snapshot --grep "result"  # click + filtered snapshot
```

### type - Type text (auto-detects dropdowns and checkboxes)
```bash
webctl type @e2 "user@example.com"  # by @ref
webctl type "Email" "user@test.com" # by text description
webctl type "Country" "Germany"     # auto-selects from dropdown
webctl type "Search" "query" --submit  # type + press Enter
webctl type "Email" "x" --snapshot  # type + return page state
```

### do - Batch multiple actions in one call
```bash
webctl do '[["type","Email","user@test.com"],["type","Password","secret"],["click","Log in"]]' --snapshot
```
Actions: `click`, `type`, `press`, `wait`. Stops on first failure.

### press - Keyboard key
```bash
webctl press Enter
webctl press Escape
webctl press Tab
```

### wait - Wait for condition
```bash
webctl wait network-idle
webctl wait 'exists:role=button name~="Continue"'
webctl wait 'url-contains:"/dashboard"'
webctl wait 'hidden:role=dialog'
```

### stop - Close everything
```bash
webctl stop                  # closes browser + daemon (default)
webctl stop --keep-daemon    # only close browser
```

### start - Explicit session start (usually not needed)
```bash
webctl start                         # visible browser
webctl start --mode unattended       # headless
```

## Target Syntax

Actions (click, type) accept three target formats:

| Format | Example | When to use |
|--------|---------|-------------|
| @ref | `@e3` | After a snapshot (fastest, most reliable) |
| Text | `"Submit"` | When you know the element text |
| Query | `'role=button name~="Submit"'` | When text is ambiguous |

**@refs** are assigned by `snapshot` and `navigate --snapshot/--search/--grep`. They reset on each snapshot.

**Text descriptions** are fuzzy-matched against interactive elements. webctl prefers the right role for the action (click prefers buttons/links, type prefers textboxes).

**Query syntax** is the fallback: `role=X name~="Y"` (use `name~=` for contains, `name=` for exact).

## Automatic Fallbacks

These happen transparently — you don't need to handle them:
- **Cookie/popup auto-dismiss**: Overlays are dismissed before actions
- **Scroll-to-find**: If element not found, scrolls down and retries (2x)
- **Click retry on overlay**: If click intercepted by overlay, dismisses and retries
- **Smart type**: `type` on combobox auto-uses select_option; on checkbox auto-uses check/uncheck

## Common Patterns

### Price Lookup (e-commerce)
```bash
webctl navigate "https://amazon.de/dp/B09HM94VDS"
# Structured data (price, rating) + snapshot with @refs for details
webctl stop
```

### Read Article
```bash
webctl navigate "https://spiegel.de" --read
# Returns structured data + readable markdown content
webctl stop
```

### Search and Extract
```bash
webctl navigate "https://duckduckgo.com" --search "query"
# Returns search results with @refs
webctl stop
```

### Login
```bash
webctl navigate "https://example.com/login"
webctl do '[["type","Email","user@example.com"],["type","Password","secret"],["click","Log in"]]' --snapshot
webctl wait 'url-contains:"/dashboard"'
webctl stop
```

### Form with Dropdown
```bash
webctl navigate "https://example.com/form"
webctl do '[["type","Name","John"],["type","Email","john@test.com"],["type","Country","Germany"],["click","Submit"]]' --snapshot
webctl stop
```

### Find Specific Data on Complex Pages
```bash
webctl navigate "https://example.com" --grep "€|price|shipping"
# Returns only elements matching the pattern, with @refs
webctl stop
```

## Human-In-The-Loop

For CAPTCHA, MFA, or manual steps (requires visible browser — don't use `--mode unattended`):

```bash
webctl start                                          # visible browser
webctl navigate "https://example.com/login"
webctl type "Email" "user@example.com" --submit
webctl prompt-secret --prompt "Enter MFA code:"       # pauses for human
webctl wait 'url-contains:"/dashboard"'
webctl stop
```
