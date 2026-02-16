---
name: kroger-cart
description: Add grocery items to a Kroger/Smith's cart via the Kroger API CLI
---

# Kroger Cart Skill

Add items to a Kroger-family grocery cart using the `kroger-cart` CLI. This skill is designed for AI agents that need to automate grocery list → cart workflows.

## Prerequisites

- Python 3.10+
- The CLI installed (`pip install .` from the repo root)
- A `.env` file with your Kroger API credentials (see `.env.example`)
- OAuth tokens (run `kroger-cart --auth-only` once to authenticate via browser; tokens auto-refresh for ~30 days)

## How to Use

### Step 1: Interpret the grocery list

Take the user's grocery list and translate each item into a **specific, searchable query**. The CLI picks the first search result from Kroger's API, so the more specific the query, the better the match.

**You must reason about items before passing them to the CLI:**

| User says | You should search for | Why |
|-----------|----------------------|-----|
| "milk" | "whole milk 1 gallon" | More specific = better first result |
| "steak for tacos" | "flank steak" | Translate dish context into the right cut |
| "yogurt for the week" | "Greek yogurt 32 oz" | Estimate a realistic quantity |
| "a couple onions" | "yellow onion" (quantity: 2) | Separate the item from the quantity |
| "pasta sauce" | "marinara sauce 24 oz" | Be specific about type and size |

### Step 2: Build the JSON input

Format items as a JSON array with `query` and `quantity` fields:

```json
[
  {"query": "whole milk 1 gallon", "quantity": 1},
  {"query": "eggs dozen", "quantity": 1},
  {"query": "flank steak", "quantity": 1},
  {"query": "Greek yogurt 32 oz", "quantity": 2}
]
```

### Step 3: Run the CLI

**Always use `--output json`** so you can parse the results programmatically.

```bash
kroger-cart --output json --json '<the JSON array from Step 2>'
```

**With `--items`** (simpler, when all quantities are 1):

```bash
kroger-cart --output json --items "whole milk 1 gallon" "eggs dozen" "bread loaf"
```

**Piped from another tool via stdin:**

```bash
echo '<JSON array>' | kroger-cart --output json --stdin
```

**Optional flags:**

| Flag | Default | Description |
|------|---------|-------------|
| `--zip CODE` | `84045` | Zip code for store lookup |
| `--modality` | `DELIVERY` | `DELIVERY` or `PICKUP` |
| `--dry-run` | off | Search but don't add to cart (preview) |

### Step 4: Parse the JSON output

The CLI returns a single JSON object:

```json
{
  "success": true,
  "dry_run": false,
  "added": [
    {
      "name": "Kroger® 2% Reduced Fat Milk",
      "upc": "0001111041700",
      "quantity": 1,
      "query": "whole milk 1 gallon",
      "price": 3.49,
      "in_stock": true
    },
    {
      "name": "Kroger® Grade A Large Eggs",
      "upc": "0001111060932",
      "quantity": 1,
      "query": "eggs dozen",
      "price": 4.99,
      "promo_price": 3.99,
      "in_stock": true
    }
  ],
  "not_found": ["some obscure item"],
  "added_count": 2,
  "not_found_count": 1,
  "cart_url": "https://www.smithsfoodanddrug.com/cart",
  "modality": "DELIVERY"
}
```

**Key fields for agents:**
- `price` — Regular price (may be absent if the API doesn't return pricing)
- `promo_price` — Sale price, only present when the item is on promotion
- `in_stock` — Whether the item is available at the selected store

### Step 5: Report results to the user

Tell the user:
- ✅ Which items were successfully added (include the matched product names)
- ❌ Which items were not found — suggest alternative search terms or ask for clarification
- 🛒 Provide the cart URL for checkout: https://www.smithsfoodanddrug.com/cart
- Remind the user that checkout must be completed manually in their browser

## Handling Not-Found Items

If items come back in `not_found`, try rephrasing and running a second pass:

| Failed query | Try instead |
|-------------|-------------|
| "dozen eggs" | "eggs 12 count" |
| "OJ" | "orange juice 64 oz" |
| "chips" | "Lay's potato chips" |
| "2% milk" | "Kroger 2% reduced fat milk" |

You can call the CLI again with just the retry items — previously added items are already in the cart.

## Tips for Better Results

- **Be specific**: "Kroger 2% milk 1 gallon" works better than "milk"
- **Include brand names** when the user mentions them
- **Use realistic sizes**: "chicken breast 1 lb" not just "chicken"
- **Separate quantity from query**: Put "2" in the `quantity` field, not in the search string
- **For exact products**, use UPC codes if available: `{"upc": "0001111041700", "quantity": 1}`

## Troubleshooting

| Error | Solution |
|-------|----------|
| `KROGER_CLIENT_ID not set` | Ensure `.env` file exists with valid credentials |
| `Token refresh failed` | Run `kroger-cart --auth-only` to re-authenticate |
| `No Smiths locations found` | Try a different `--zip` code |
| Exit code 1 with JSON `error` field | Parse the error message from the JSON output |

### Agent-Specific Troubleshooting

**400 Client Errors:** The CLI automatically sanitizes queries (strips `&`, `#`, `@` and other special characters) and retries with a simplified query if the first attempt gets a 400. You generally don't need to worry about this, but if items come back as `not_found`:

- Avoid special characters in queries (`Ben & Jerry's` → `Ben Jerrys`)
- Keep queries concise — `milk` works better than `organic 2% reduced fat milk 1 gallon half gallon`
- Don't include units in the query itself — use the `quantity` field instead

**OAuth Port Errors:** The CLI uses a dynamic port for the OAuth callback server, so `Address already in use` errors should not occur. If authentication fails, run `kroger-cart --auth-only` interactively.

## Example: Full Agent Workflow

User: *"Add stuff for breakfast — eggs, bacon, OJ, and some bread"*

**Step 1 — Reason about items:**
- "eggs" → "eggs dozen" (standard pack)
- "bacon" → "bacon 16 oz" (standard pack)
- "OJ" → "orange juice 64 oz" (abbreviation → full name)
- "some bread" → "white bread loaf" (generic → specific)

**Step 2 — Call the CLI once:**

```bash
kroger-cart --output json --json '[{"query": "eggs dozen", "quantity": 1}, {"query": "bacon 16 oz", "quantity": 1}, {"query": "orange juice 64 oz", "quantity": 1}, {"query": "white bread loaf", "quantity": 1}]'
```

**Step 3 — Report back:**

> ✅ Added 4 items to your Smith's cart:
> - Kroger® Grade A Large Eggs (x1)
> - Oscar Mayer Hardwood Smoked Bacon 16 oz (x1)
> - Tropicana Pure Premium Orange Juice 52 oz (x1)
> - Grandma Sycamore's White Bread (x1)
>
> 🛒 Review and checkout: https://www.smithsfoodanddrug.com/cart
