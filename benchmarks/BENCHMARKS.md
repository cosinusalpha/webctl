# webctl vs agent-browser Benchmarks

Four real-world tasks comparing webctl and Vercel's agent-browser.
Metrics: command count, token consumption, cookie handling, data quality.

## Setup

```bash
# webctl — start from project root
uv run webctl start

# agent-browser — from benchmarks dir
cd benchmarks/agent-browser
# uses npx or ./node_modules/.bin/agent-browser
```

---

## 1. E-Commerce: Find product price and shipping

**Task**: Find the price and shipping cost of a Logitech MX Master 3S mouse on Amazon.de

**What to check**: cookie banner dismissed, search works, prices visible, shipping info visible

### webctl

```bash
uv run webctl navigate https://www.amazon.de
# Expect: cookie_banner_dismissed: true, headings + interactive in summary
uv run webctl type 'role=searchbox' 'Logitech MX Master 3S'
uv run webctl press Enter
uv run webctl wait load
uv run webctl snapshot --view a11y --grep 'MX Master|Preis|price|EUR|€'
# Expect: product names, prices (e.g. "99,99 €"), "Andere Angebote", shipping text
```

### agent-browser

```bash
./node_modules/.bin/agent-browser open https://www.amazon.de
./node_modules/.bin/agent-browser snapshot
# Must manually find and click cookie accept button (#sp-cc-accept or ref)
./node_modules/.bin/agent-browser click '@eN'  # cookie accept ref
./node_modules/.bin/agent-browser click '@eN'  # searchbox ref
./node_modules/.bin/agent-browser type '@eN' 'Logitech MX Master 3S'
./node_modules/.bin/agent-browser press Enter
./node_modules/.bin/agent-browser wait 3000
./node_modules/.bin/agent-browser snapshot
# Must scan full snapshot for prices — no grep/filter
```

### Key differences
- webctl auto-dismisses Amazon cookie banner (`#sp-cc-accept` selector)
- webctl `--grep` filters results to just price/product lines
- webctl text nodes show prices ("99,99 €"), review snippets, shipping info
- agent-browser returns full unfiltered a11y tree

---

## 2. News: Extract headlines

**Task**: Gather the top headlines from spiegel.de

**What to check**: Sourcepoint iframe consent dismissed, headlines extracted, markdown view works

### webctl

```bash
uv run webctl navigate https://www.spiegel.de
# Expect: cookie_banner_dismissed: true, top 5 headings in summary
uv run webctl snapshot --view a11y --roles heading --limit 20
# Expect: headline text visible
uv run webctl snapshot --view md
# Expect: clean markdown with article headlines, ~30KB
```

### agent-browser

```bash
./node_modules/.bin/agent-browser open https://www.spiegel.de
./node_modules/.bin/agent-browser snapshot
# Consent overlay blocks interaction — Sourcepoint iframe consent
# Must find accept button in snapshot (may not be visible — it's in an iframe)
# agent-browser has no iframe-aware consent handling
# May need: agent-browser get text @ref or manual workaround
./node_modules/.bin/agent-browser snapshot
# No markdown view — only a11y tree
```

### Key differences
- webctl auto-dismisses Sourcepoint iframe consent ("Einwilligen und weiter")
- webctl has `--view md` for readable article content via Readability.js + MarkItDown
- webctl navigate summary already includes top 5 headlines (zero extra commands)
- agent-browser has no markdown extraction, no iframe consent handling

---

## 3. Local Search: Find restaurant with filters

**Task**: Find a vegan Chinese restaurant in Berlin with a rating higher than 4 on Google Maps

**What to check**: Google consent handled, restaurant names + ratings + prices + addresses extracted

### webctl

```bash
uv run webctl navigate 'https://www.google.com/maps/search/vegan+chinese+restaurant+berlin'
# Expect: cookie_banner_dismissed: true
uv run webctl snapshot --view a11y --grep 'vegan|chinese|China|rating|Stern|4\.'
# Expect: restaurant names, "X,X Sterne Y Rezensionen", prices, addresses, hours, review snippets
```

Expected results (may change over time):
- Kamala Vegan — 4.7 stars, Boxhagener Str. 85, 20-30€
- Dream Vegan-Vietnamese — 5.0 stars, Neue Bahnhofstraße 7a, 10-20€
- Susu Vegan Food — 4.8 stars, Eberswalder Str. 29, 10-20€
- KIM999 Vegan — 4.8 stars, Mollstraße 31, 10-20€

### agent-browser

```bash
./node_modules/.bin/agent-browser open 'https://www.google.com/maps/search/vegan+chinese+restaurant+berlin'
./node_modules/.bin/agent-browser snapshot
# Lands on Google consent page — must find and click "Alle akzeptieren"
./node_modules/.bin/agent-browser click '@eN'  # accept button ref
./node_modules/.bin/agent-browser wait 3000
./node_modules/.bin/agent-browser snapshot
# Full unfiltered tree — ~2,500 tokens
# Has restaurant names, ratings, addresses — but no review counts, no prices, no snippets
```

### Key differences
- webctl: 2 commands, ~800 tokens, auto-cookie, grep-filtered, prices + review counts visible
- agent-browser: 4 commands, ~3,000 tokens, manual cookie, unfiltered, less text node data

---

## 4. Web Search: Find fan sites with descriptions

**Task**: Google for penguin fan sites and return the top 3 with a description

**What to check**: search engine bot detection, result extraction with descriptions

Note: Google blocks headless browsers. Use DuckDuckGo as fallback.
Note: "penguin" gets interpreted as Pittsburgh Penguins (hockey). For animal penguins use "penguin bird fan sites".

### webctl

```bash
uv run webctl navigate 'https://duckduckgo.com/?q=penguin+fan+sites'
# Expect: search results loaded, no CAPTCHA
uv run webctl snapshot --view a11y --grep 'penguin|Penguin|fan'
# Expect: result titles (heading), URLs (link/paragraph), descriptions (text)
# Alternative: uv run webctl snapshot --view md for formatted results
```

Expected results (may change over time):
1. Pittsburgh Penguins Official (nhl.com/penguins) — "The official National Hockey League website including news, rosters, stats, schedules"
2. Pens Labyrinth (penslabyrinth.com) — "The latest Pittsburgh Penguins news, rumors, player updates, stats, analysis, editorials"
3. Pensburgh (pensburgh.com) — "Your best source for quality Pittsburgh Penguins news, rumors, analysis, stats and scores from the fan perspective"

### agent-browser

```bash
./node_modules/.bin/agent-browser open 'https://duckduckgo.com/?q=penguin+fan+sites'
./node_modules/.bin/agent-browser snapshot
# FAILED: DuckDuckGo CAPTCHA ("Select all squares containing a duck")
# No search results returned — bot detection blocked agent-browser
```

### Key differences
- webctl passed DuckDuckGo bot detection, agent-browser got CAPTCHA-blocked
- webctl `--grep` extracts just matching results; agent-browser returned no results at all
- Google.com blocks both tools (CAPTCHA), but DDG only blocks agent-browser

---

## Results Summary (2026-03-20)

| Metric | webctl | agent-browser |
|---|---|---|
| **1. Amazon price search** | | |
| Commands | 5 (navigate, type, press, wait, snapshot) | ~8 (open, snapshot, click cookie, click search, type, press, wait, snapshot) |
| Cookie consent | auto | manual |
| Price visibility | yes (text nodes) | partial |
| **2. Spiegel headlines** | | |
| Commands | 1-2 (navigate gives headlines, optional snapshot) | 3+ (open, snapshot, consent workaround, snapshot) |
| Cookie consent | auto (iframe) | stuck (iframe not accessible) |
| Markdown view | yes (Readability.js) | no |
| **3. Google Maps restaurants** | | |
| Commands | 2 | 4 |
| Tokens (approx) | ~800 | ~3,000 |
| Cookie consent | auto | manual |
| Output filtering | `--grep` | none |
| Review counts/prices | yes | no |
| **4. DuckDuckGo search** | | |
| Commands | 2 | 2 (but failed) |
| Bot detection | passed | CAPTCHA-blocked |
| Result data | titles + URLs + descriptions | none (blocked) |
| Output filtering | `--grep` | n/a |
