---
name: webctl
description: Browser automation via CLI. Use when browsing websites, filling forms, extracting data from web pages, taking screenshots, or automating web interactions. Preferred over MCP browser tools for better context control.
allowed-tools: Bash, Read
---

# webctl - Browser Automation CLI

CLI tool for browser automation. Use this instead of MCP browser tools - it gives you control over what enters your context.

## RULES (Read First!)

1. **ALWAYS start a session:** `webctl start`
2. **ALWAYS end your session:** `webctl stop --daemon`
3. **ALWAYS snapshot before interacting** to see what elements exist
4. **ALWAYS use `--interactive-only`** with snapshot (unless you need to read page text)
5. **ALWAYS use `name~="text"`** not `name="text"` (see "Why name~=" below)
6. **ALWAYS quote queries:** `'role=button name~="Submit"'`
7. **Output is compact by default** - use `--force` if truncated (>200 elements)

## Quick Start Template

```bash
# 1. Start browser
webctl start                              # Visible browser
# OR: webctl start --mode unattended      # Headless (no window)

# 2. Go to page
webctl navigate "https://example.com"

# 3. See what's on the page (DO THIS BEFORE CLICKING ANYTHING)
webctl snapshot --interactive-only

# 4. Interact with elements
webctl click 'role=button name~="Submit"'

# 5. End session
webctl stop --daemon
```

---

## How Queries Work

Queries find elements on the page using two parts:

```
role=ROLE name~="TEXT"
```

- **`role=X`** - Matches elements with ARIA role X (button, link, textbox, etc.)
- **`name~="Y"`** - Matches elements whose visible label CONTAINS Y
- **Both must match** - The element must have the right role AND contain the text

**Quoting rules:**
- Wrap the ENTIRE query in single quotes: `'...'`
- Wrap the name value in double quotes: `name~="..."`
- Full example: `'role=button name~="Submit"'`

### Why `name~=` Instead of `name=`

| Syntax | Behavior | Example |
|--------|----------|---------|
| `name~="Submit"` | Contains "Submit" | Matches "Submit", "Submit Form", "Submit Now" |
| `name="Submit"` | Exactly "Submit" | ONLY matches "Submit", fails on "Submit Form" |

**Use `name~=` because:**
- Web pages change slightly over time
- Button text might be "Submit" today, "Submit Form" tomorrow
- `name=` will silently fail to find the element if text changes
- `name~=` is more robust and forgiving

### Common Roles Reference

| Role | What It Matches | Example Query |
|------|-----------------|---------------|
| `button` | Buttons, submit buttons | `'role=button name~="Submit"'` |
| `link` | Links, anchor tags | `'role=link name~="Sign in"'` |
| `textbox` | Text inputs, text areas | `'role=textbox name~="Email"'` |
| `combobox` | Dropdowns, select menus | `'role=combobox name~="Country"'` |
| `checkbox` | Checkboxes | `'role=checkbox name~="Remember"'` |
| `radio` | Radio buttons | `'role=radio name~="Option A"'` |
| `form` | Forms (for scoping) | `--within "role=form"` |
| `main` | Main content area | `--within "role=main"` |
| `table` | Tables | `--within "role=table"` |
| `navigation` | Nav menus | `--within "role=navigation"` |

---

## Session Lifecycle

### Starting a Session

```bash
webctl start                      # Visible browser (see what's happening)
webctl start --mode unattended    # Headless (no window, faster)
webctl -s myprofile start         # Named session (separate cookies/login state)
```

**What happens:**
- Browser launches (or connects to existing daemon)
- Session is ready for commands
- Default timeout for commands: 30 seconds

### Ending a Session

```bash
webctl stop --daemon              # Closes browser AND daemon
webctl stop                       # Just closes browser, daemon stays running
```

**IMPORTANT: Always end with `webctl stop --daemon`**

### What If I Forget to Stop?

- Browser stays open consuming memory
- Session remains active
- You can still run `webctl stop --daemon` later to clean up
- Starting a new `webctl start` will reuse the existing session

### What If I Start Twice?

- It reuses the existing session (safe to do)
- No error, no duplicate browsers

### What If a Command Fails Mid-Session?

- Session stays active
- Browser stays on current page
- You can retry the command or continue with other commands
- Use `webctl status` to check session state

---

## Snapshot Commands

### Use `--interactive-only` (Default Choice)

```bash
webctl snapshot --interactive-only
```

Shows only elements you can interact with: buttons, links, inputs, checkboxes, etc.

**Use this when:** You want to click, type, or interact with the page.

### Use Full Snapshot (Without `--interactive-only`)

```bash
webctl snapshot
```

Shows ALL elements including text, headings, paragraphs.

**Use this when:** You need to read text content on the page.

### Use `--within` to Scope (Reduce Noise)

```bash
webctl snapshot --interactive-only --within "role=main"
webctl snapshot --interactive-only --within "role=form"
```

**What `--within` does:**
- Filters output to only show elements INSIDE the specified container
- Reduces noise from navigation, footers, sidebars
- Use it when page has too many elements

**Common scopes:**
- `--within "role=main"` - Main content, skip nav/footer
- `--within "role=form"` - Just the form
- `--within "role=table"` - Just a table
- `--within "role=dialog"` - Just a modal/popup

### Limit Output Size

```bash
webctl snapshot --interactive-only --limit 30
```

Caps output at 30 elements. Use when pages have many elements.

---

## Large Output Handling

webctl uses **compact output by default**. On large pages (>200 elements), it shows:
- Summary header: `# Snapshot: 523 elements (89 button, 156 link, ...)`
- Preview of first 20 elements
- Hint: "Use --force for full output"

### Commands for Large Pages

```bash
webctl snapshot                              # Compact output (fast)
webctl snapshot --force                      # Full output even if large
webctl snapshot --grep "button|submit"       # Filter by regex pattern
webctl snapshot --names-only                 # Minimal output (role + name only)
webctl snapshot --max-name-length 50         # Truncate long names
```

### When to Use Each Option

| Scenario | Command |
|----------|---------|
| Need all elements | `--force` |
| Looking for specific text | `--grep "pattern"` |
| Too much output | `--interactive-only` or `--within "role=main"` |

---

## AI-Friendly Features

### Quick Page Assessment (Zero Context)

```bash
webctl snapshot --count                    # Just counts, no elements
webctl status --brief                      # One-line: URL | elements | errors | state
```

### Copy-Paste Ready Queries

```bash
webctl snapshot --show-query --interactive-only
# n1 button "Submit"  [role=button name~="Submit"]
# Copy the query in brackets to use with click/type
```

### Reliable Actions

```bash
webctl click '...' --retry 3               # Retry up to 3 times
webctl click '...' --wait network-idle     # Wait after click
webctl type '...' "text" --submit --wait network-idle
```

### Bulk Form Fill

```bash
webctl fill-form '{"Email": "user@example.com", "Password": "secret"}'
webctl fill-form '{"Remember me": true}'   # Checkboxes use boolean
```

---

## Interaction Commands

### Click

```bash
webctl click 'role=button name~="Submit"'
webctl click 'role=link name~="Sign in"'
webctl click 'role=button name~="Next"'
```

### Type Text

```bash
# Type into a field
webctl type 'role=textbox name~="Email"' "user@example.com"

# Type and press Enter (for search, login forms)
webctl type 'role=textbox name~="Search"' "my query" --submit
```

### Select from Dropdown

```bash
# By visible text (PREFERRED)
webctl select 'role=combobox name~="Country"' --label "Germany"

# By value attribute
webctl select 'role=combobox name~="Country"' --value "DE"
```

### Checkboxes

```bash
webctl check 'role=checkbox name~="Remember"'
webctl uncheck 'role=checkbox name~="Newsletter"'
```

### Keyboard

```bash
webctl press Enter
webctl press Tab
webctl press Escape
```

### Scroll

```bash
webctl scroll down
webctl scroll up
webctl scroll --to "role=list" down   # Scroll element into view
```

### File Upload

```bash
webctl upload 'role=button name~="Upload"' --file /path/to/file.pdf
```

---

## Wait Commands

**Default timeout: 30 seconds / 30000ms** (override with `--timeout`, value in milliseconds)

### Wait for Page Load

```bash
webctl wait network-idle              # Wait for all network requests to finish
```

**Use after:** `navigate`, clicking links that load new pages

### Wait for Element to Appear

```bash
webctl wait 'exists:role=button name~="Continue"'
```

**What happens:** Retries query every ~100ms until element appears or timeout.

**Use when:** Element loads dynamically after page load.

### Wait for Element to Disappear

```bash
webctl wait 'hidden:role=dialog'
webctl wait 'hidden:role=progressbar'
```

**Use when:** Waiting for loading spinners or modals to close.

### Wait for URL Change

```bash
webctl wait 'url-contains:"/dashboard"'
```

**Use after:** Form submission, login, actions that redirect.

### Custom Timeout

```bash
webctl wait --timeout 60000 network-idle    # 60 seconds
webctl wait --timeout 5000 'exists:role=button name~="Load"'  # 5 seconds
```

---

## Navigation Commands

```bash
webctl navigate "https://example.com"
webctl back
webctl forward
webctl reload
```

**Note:** `navigate` waits for initial page load but NOT for all dynamic content. Add `webctl wait network-idle` if page loads data dynamically.

---

## When Queries Fail - Troubleshooting

### Step 1: Check What's Actually on the Page

```bash
webctl snapshot --interactive-only
```

Look at the output. Is your element there with a different name?

### Step 2: Test Your Query

```bash
webctl query 'role=button name~="Submit"'
```

**What this shows:**
- Matching elements (if any)
- Suggestions for similar elements if no exact match
- Helps you see what your query matches vs what's available

### Step 3: Common Fixes

| Problem | Solution |
|---------|----------|
| "Element not found" | Check snapshot, adjust query text |
| Element exists but wrong name | Use `name~=` with different text |
| Too many matches | Add more specific text to name |
| Element in popup/modal | Check if modal is open, might need `--within "role=dialog"` |
| Element appeared after page load | Add `webctl wait 'exists:...'` before interacting |

### Step 4: If Still Stuck

```bash
# See ALL elements (not just interactive)
webctl snapshot

# Check for JavaScript errors
webctl console --level error

# Take screenshot to see visual state
webctl screenshot --path debug.png
```

### Step 5: Click Worked but Nothing Happened

Sometimes the click succeeds but the page doesn't respond as expected:

```bash
# Wait for page to respond after clicking
webctl click 'role=button name~="Submit"'
webctl wait network-idle                    # Wait for any network activity
webctl screenshot --path after-click.png    # See what happened visually

# Or wait for expected result
webctl click 'role=button name~="Submit"'
webctl wait 'url-contains:"/success"'       # Wait for redirect
```

**Common causes:**
- Page needs time to process (add `wait network-idle`)
- Button triggers JavaScript, not navigation (check for new elements)
- Form validation failed (check for error messages in snapshot)

---

## Error Messages and What They Mean

| Error | Meaning | Fix |
|-------|---------|-----|
| "Element not found" | Query matched nothing | Check snapshot, adjust query |
| "Element not visible" | Element exists but hidden | Wait for animation/modal, or scroll |
| "Element disabled" | Button/input is disabled | Wait for page to enable it, or check if action is allowed |
| "Timeout" | Action took too long | Increase `--timeout` or add explicit `wait` |
| "Session not found" | No active session | Run `webctl start` first |
| "Navigation failed" | URL couldn't load | Check URL, network, or site might be down |

---

## Output Control

```bash
webctl --quiet navigate "URL"          # Suppress status messages
webctl --result-only snapshot          # Just the data, no metadata
webctl --format jsonl snapshot         # JSON lines (for piping to jq)
```

**When to use:**
- `--quiet` - When you don't need status updates
- `--result-only` - When parsing output programmatically
- `--format jsonl` - When piping to `jq` or other JSON tools

---

## Multi-Tab Support

```bash
webctl pages              # List all open tabs
webctl focus 1            # Switch to tab 1
webctl close-page 2       # Close tab 2
```

**When to use:** When a click opens a new tab, or site has multiple windows.

---

## Human-In-The-Loop (HITL)

**Use when automation can't proceed alone:** CAPTCHA, MFA, complex auth.

**Requires visible browser:** Use `webctl start` (NOT `--mode unattended`)

### Pause for Secret Entry (MFA codes, passwords)

```bash
webctl prompt-secret --prompt "Enter MFA code:"
```

Pauses until human enters a value.

### Pause for Human Action (CAPTCHA, manual steps)

Use shell read command to pause:

```bash
read -p "Please solve the CAPTCHA, then press Enter"
```

Pauses until human presses Enter in terminal.

### Example: Login with MFA

```bash
webctl start                    # Must be visible
webctl navigate "https://example.com/login"
webctl type 'role=textbox name~="Email"' "user@example.com"
webctl type 'role=textbox name~="Password"' "password" --submit
webctl prompt-secret --prompt "Enter MFA code from authenticator:"
webctl wait 'url-contains:"/dashboard"'
webctl stop --daemon
```

---

## Common Patterns

### Login Flow

```bash
webctl start
webctl navigate "https://example.com/login"
webctl snapshot --interactive-only --within "role=form"
webctl type 'role=textbox name~="Email"' "user@example.com"
webctl type 'role=textbox name~="Password"' "password" --submit
webctl wait 'url-contains:"/dashboard"'
webctl snapshot --interactive-only
webctl stop --daemon
```

### Form Submission

```bash
webctl start
webctl navigate "https://example.com/form"
webctl snapshot --interactive-only --within "role=form"
webctl type 'role=textbox name~="Name"' "John Doe"
webctl type 'role=textbox name~="Email"' "john@example.com"
webctl select 'role=combobox name~="Country"' --label "Germany"
webctl check 'role=checkbox name~="Terms"'
webctl click 'role=button name~="Submit"'
webctl wait network-idle
webctl stop --daemon
```

### Search and Read Results

```bash
webctl start
webctl navigate "https://example.com"
webctl type 'role=textbox name~="Search"' "my search query" --submit
webctl wait network-idle
webctl snapshot --within "role=main"    # Full snapshot to read text
webctl stop --daemon
```

---

## Quick Reference

| Task | Command |
|------|---------|
| Start browser | `webctl start` |
| Start headless | `webctl start --mode unattended` |
| Go to URL | `webctl navigate "URL"` |
| Count elements | `webctl snapshot --count` |
| See elements | `webctl snapshot --interactive-only` |
| Show queries | `webctl snapshot --show-query --interactive-only` |
| Quick status | `webctl status --brief` |
| Click button | `webctl click 'role=button name~="Text"'` |
| Click + retry | `webctl click '...' --retry 3` |
| Click + wait | `webctl click '...' --wait network-idle` |
| Type in field | `webctl type 'role=textbox name~="Field"' "value"` |
| Type + Enter | `webctl type '...' "value" --submit` |
| Fill form | `webctl fill-form '{"Email": "x", "Password": "y"}'` |
| Select dropdown | `webctl select 'role=combobox name~="Field"' --label "Option"` |
| Wait for load | `webctl wait network-idle` |
| Wait for element | `webctl wait 'exists:role=button name~="Text"'` |
| Debug query | `webctl query 'role=button name~="Text"'` |
| End session | `webctl stop --daemon` |

---

## Remember

1. **Start:** `webctl start`
2. **Snapshot first:** `webctl snapshot --interactive-only`
3. **Quote queries:** `'role=button name~="Text"'`
4. **Use `name~=`** not `name=`
5. **Debug with:** `webctl query` and `webctl snapshot`
6. **End:** `webctl stop --daemon`

Run `webctl --help` or `webctl <command> --help` for more options.
