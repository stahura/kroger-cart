# Kroger Cart CLI

Add grocery items to your Kroger/Smith's cart via the [Kroger Public API](https://developer.kroger.com/).

> **Note:** This tool adds items to your cart — it does not and cannot automate checkout. The Kroger API has no checkout endpoint; you always complete purchases manually in your browser or mobile app.

## Features

- 🛒 Search and add items to your cart for **delivery** or **pickup**
- � `--deals` mode to check promotions and savings
- �📄 Multiple input methods: CLI flags, JSON, CSV, or stdin
- 🔐 OAuth2 + PKCE authentication with automatic token refresh
- 🔑 Optional OS keychain storage (`pip install kroger-cart[keyring]`)
- 🔄 Automatic retry with exponential backoff on transient errors
- 🔍 `--dry-run` mode to preview without modifying your cart
- 📊 Machine-readable `--output json` for automation

## Why This Tool

**One command, entire grocery list.** Pass all your items in and get a single JSON result back. No multi-step workflows, no interactive prompts, no back-and-forth.

```bash
kroger-cart --json '[{"query": "milk", "quantity": 2}, {"query": "eggs"}]' --output json
```

**Built for AI agents.** An agent reads your grocery list, reasons about what to search for, and calls this CLI once. All the searching and cart-adding happens inside a single process — the agent doesn't need to make a separate call for every item. This keeps agent costs low and execution fast.

**Works with anything.** It's a CLI that takes input and produces JSON output. Pipe from a script, call from an AI agent, run from cron, or just type it yourself. No protocol lock-in, no specific AI platform required.

## Quick Start

### 1. Install

```bash
pip install kroger-cart

# Or install from source:
pip install -e .

# Optional: enable OS keychain for token storage
pip install kroger-cart[keyring]
```

### 2. Configure
1. Go to [developer.kroger.com](https://developer.kroger.com/) and create an application.
2. Use a **Production** app (`KROGER_ENV=PROD`) for real Kroger/Smith's shopper accounts.
3. In your Kroger app settings, set Redirect URI (default: `http://localhost:3000`).
4. Run the setup wizard and enter the same values:
```bash
kroger-cart --setup
```
This saves your credentials/config to `~/.config/kroger-cart/.env` (`KROGER_CLIENT_ID`, `KROGER_CLIENT_SECRET`, `KROGER_ENV`, `KROGER_REDIRECT_URI`).
### 3. Link Shopper Account
Run this command to log in with the Kroger account you want to shop with:

```bash
kroger-cart --auth-only
```
This opens a web browser. Sign in and click "Authorize" to give the CLI access to your cart.

### 4. Add Items

```bash
kroger-cart --items "milk 1 gallon" "eggs dozen" "bread"
```

## Usage

### Add items by name

```bash
kroger-cart --items "milk" "eggs" "bread"
```

### Add items with quantities (JSON)

```bash
kroger-cart --json '[{"query": "milk", "quantity": 2}, {"query": "eggs", "quantity": 1}]'
```

### Pipe from another tool (stdin)

```bash
echo '[{"query": "butter"}, {"query": "cheese"}]' | kroger-cart --stdin
```

### Load from CSV

```bash
kroger-cart groceries.csv
```

CSV format:
```csv
query,quantity
milk 1 gallon,2
eggs dozen,1
```

### Check deals

```bash
kroger-cart --deals --items "milk" "eggs" "bread"
```

Shows promo pricing and savings inline:
```
✓ Deals found for (3):
  - Kroger 2% Milk (x1) — $3.49 → $2.99 (SAVE $0.50, 14%)
  - Large Eggs (x1) — $2.79
  - Bread (x1) — $3.29 → $2.50 (SAVE $0.79, 24%) 🔥

💰 2 item(s) on sale — total savings: $1.29
```

### Dry run (preview only)

```bash
kroger-cart --items "steak" --dry-run
```

### Cart status note

Cart retrieval ("get cart" / list current cart contents) is not available to general developers via Kroger Public API access. It is available only with Partner API access.

For public usage of this CLI, review cart contents in the Kroger/Smith's web or mobile app after adding items.

### Machine-readable output

```bash
kroger-cart --items "milk" --output json
```

```json
{
  "success": true,
  "dry_run": false,
  "added": [{"name": "Kroger® 2% Milk", "upc": "0001111041700", "quantity": 1, "query": "milk"}],
  "not_found": [],
  "added_count": 1,
  "not_found_count": 0,
  "cart_url": "https://www.smithsfoodanddrug.com/cart",
  "modality": "DELIVERY"
}
```

## How It Works

The CLI operates in two phases:

1. **Search** — Each item is searched individually against the Kroger product catalog
2. **Add** — All found items are added to the cart in a **single batched API call**

For 5 items, this means 7 API calls total (1 location lookup + 5 searches + 1 batch cart add), not 11.

### Product matching

The CLI **picks the first search result** from Kroger's API for each query. This is by design — the CLI is a dumb pipe that executes whatever search terms it receives.

**The caller is responsible for providing good search queries.** If an AI agent is driving the CLI, the agent should reason about what to search for *before* calling the CLI. For example:

| User says | Agent should search for | Why |
|-----------|------------------------|-----|
| "steak for lomo saltado" | "flank steak" | The agent knows the right cut for the dish |
| "enough yogurt for the week" | "yogurt 32 oz" | The agent estimates a reasonable quantity |
| "milk" | "whole milk 1 gallon" | More specific = better first result |

This separation keeps the CLI simple, testable, and usable by both humans and AI agents — the intelligence lives in the caller, not the tool.

## All Options

| Flag | Default | Description |
|------|---------|-------------|
| `--items ITEM [...]` | — | Item names to search and add |
| `--json JSON` | — | JSON array of `{query, quantity}` objects |
| `--stdin` | — | Read JSON from stdin |
| `--output text\|json` | `text` | Output format |
| `--zip CODE` | `84045` | Zip code for store lookup |
| `--modality DELIVERY\|PICKUP` | `DELIVERY` | Fulfillment type |
| `--env PROD\|CERT` | `PROD` | Kroger API environment |
| `--auth-only` | — | Run authentication only |
| `--dry-run` | — | Search but don't add to cart |
| `--deals` | — | Check deals/promotions (implies `--dry-run`) |
| `--setup` | — | Interactive setup: configure API credentials |
| `--token-storage auto\|file\|keyring` | `auto` | Token storage backend |
| `--version` | — | Show version and exit |

## Project Structure

```
kroger-cart/
├── kroger_cart/           # Main package
│   ├── __init__.py
│   ├── __main__.py        # python -m kroger_cart
│   ├── cli.py             # Argument parsing, orchestration
│   ├── auth.py            # OAuth2 + PKCE, token management
│   ├── api.py             # Kroger API functions
│   └── session.py         # HTTP session with retry
├── tests/                 # Pytest test suite
├── pyproject.toml         # Package config
├── .env.example           # Credentials template
└── LICENSE                # MIT license
```

## Configuration & Token Storage

All configuration is stored in `~/.config/kroger-cart/`:

| File | Purpose |
|------|---------|
| `.env` | API credentials + auth config (`KROGER_CLIENT_ID`, `KROGER_CLIENT_SECRET`, `KROGER_ENV`, `KROGER_REDIRECT_URI`) |
| `tokens.json` | OAuth tokens (auto-managed, chmod 600) |

Run `kroger-cart --setup` to create the config directory and save your credentials.

OAuth redirect behavior:
- Default callback URI is `http://localhost:3000`
- If you use a different redirect URI in Kroger Developer Portal, set `KROGER_REDIRECT_URI` to the exact same value in `~/.config/kroger-cart/.env`
- Redirect URI must match exactly between your app settings and the CLI config

By default, tokens are stored in `tokens.json` with restricted file permissions (chmod 600 on Unix).
For enhanced security, install the keyring extra:

```bash
pip install kroger-cart[keyring]
```

This uses your OS keychain (macOS Keychain, GNOME Keyring, Windows Credential Locker). Falls back to file storage automatically on headless systems.

You can force a specific backend:

```bash
kroger-cart --items "milk" --token-storage keyring
kroger-cart --items "milk" --token-storage file
```

## Development

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

## License

MIT
