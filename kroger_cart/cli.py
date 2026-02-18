"""
CLI entry point for the Kroger Cart tool.
Handles argument parsing, item loading, output formatting, and orchestration.
"""

import os
import sys
import json
import csv
import logging
import argparse
import requests
from urllib.parse import urlparse

from dotenv import load_dotenv

from kroger_cart import __version__
from kroger_cart.session import create_session
from kroger_cart.auth import TokenManager, get_storage_backend
from kroger_cart import api

logger = logging.getLogger(__name__)


# ─── Argument Parsing ────────────────────────────────────────────────────────


def parse_args(argv=None):
    """Parse command-line arguments.

    Args:
        argv: Argument list (default: sys.argv[1:]). Testable.
    """
    parser = argparse.ArgumentParser(
        description="Kroger Cart CLI — Add grocery items to your Kroger/Smith's cart.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --items "milk" "eggs" "bread"
  %(prog)s --items "milk 1 gallon" --output json
  %(prog)s --json '[{"query": "milk", "quantity": 2}, {"query": "eggs"}]'
  echo '[{"query": "eggs"}]' | %(prog)s --stdin
  %(prog)s groceries.csv
  %(prog)s --items "butter" --zip 84045 --modality PICKUP
  %(prog)s --items "cheese" --dry-run
  %(prog)s --deals --items "milk" "eggs" "bread"
  %(prog)s --cart
        """,
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    # Input methods (mutually exclusive)
    input_group = parser.add_mutually_exclusive_group()
    input_group.add_argument(
        "--items",
        nargs="+",
        metavar="ITEM",
        help='Item names to search and add (e.g., --items "milk" "eggs")',
    )
    input_group.add_argument(
        "--json",
        dest="json_input",
        metavar="JSON",
        help='JSON array of items: [{"query": "milk", "quantity": 2}]',
    )
    input_group.add_argument(
        "--stdin",
        action="store_true",
        help="Read JSON array of items from stdin",
    )
    input_group.add_argument(
        "csv_file",
        nargs="?",
        default=None,
        help="CSV file with items (columns: query/name/upc, quantity)",
    )

    # Options
    parser.add_argument(
        "--zip",
        default=os.environ.get("KROGER_ZIP", "84045"),
        help="Zip code for store lookup (default: 84045)",
    )
    parser.add_argument(
        "--modality",
        choices=["DELIVERY", "PICKUP"],
        default="DELIVERY",
        help="Fulfillment modality (default: DELIVERY)",
    )
    parser.add_argument(
        "--env",
        choices=["PROD", "CERT"],
        default=os.environ.get("KROGER_ENV", "PROD").upper(),
        help="Kroger API environment (default: PROD)",
    )
    parser.add_argument(
        "--output",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text). Use json for programmatic parsing.",
    )
    parser.add_argument(
        "--auth-only",
        action="store_true",
        help="Only run authentication flow, do not add items.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Search for products but do not add them to the cart.",
    )
    parser.add_argument(
        "--deals",
        action="store_true",
        help="Check deals/promotions for items (implies --dry-run).",
    )
    parser.add_argument(
        "--token-storage",
        choices=["auto", "file", "keyring"],
        default="auto",
        help="Token storage backend (default: auto-detect).",
    )
    parser.add_argument(
        "--cart",
        action="store_true",
        help="Show current cart contents and exit.",
    )
    parser.add_argument(
        "--setup",
        action="store_true",
        help="Interactive setup: configure API credentials.",
    )

    return parser.parse_args(argv)


# ─── Config Directory ────────────────────────────────────────────────────────


def get_config_dir() -> str:
    """Get the configuration directory path.

    Uses ~/.config/kroger-cart/ on all platforms.
    Creates the directory if it doesn't exist.
    """
    config_dir = os.path.join(os.path.expanduser("~"), ".config", "kroger-cart")
    os.makedirs(config_dir, exist_ok=True)
    return config_dir


def run_setup():
    """Interactive setup: create config directory and write credentials."""
    config_dir = get_config_dir()
    env_path = os.path.join(config_dir, ".env")

    print("\n🔧 Kroger Cart Setup")
    print("=" * 40)
    print(f"Config directory: {config_dir}")
    print()

    if os.path.exists(env_path):
        print(f"⚠️  Config file already exists: {env_path}")
        overwrite = input("  Overwrite? [y/N]: ").strip().lower()
        if overwrite != "y":
            print("Setup cancelled.")
            return

    print("Get your credentials from https://developer.kroger.com/")
    print("Use a Production app for real shopper accounts (KROGER_ENV=PROD).")
    print()
    client_id = input("  Client ID: ").strip()
    client_secret = input("  Client Secret: ").strip()
    env_input = input("  Environment [PROD/CERT] (default: PROD): ").strip().upper()
    kroger_env = env_input if env_input in {"PROD", "CERT"} else "PROD"
    redirect_input = input(
        "  Redirect URI (default: http://localhost:3000): "
    ).strip()
    redirect_uri = redirect_input or "http://localhost:3000"

    if not client_id or not client_secret:
        print("\n❌ Both Client ID and Client Secret are required.")
        sys.exit(1)
    parsed_redirect = urlparse(redirect_uri)
    if not parsed_redirect.scheme or not parsed_redirect.hostname or parsed_redirect.port is None:
        print(
            "\n❌ Redirect URI must include scheme, host, and port "
            "(example: http://localhost:3000)."
        )
        sys.exit(1)

    with open(env_path, "w") as f:
        f.write(f"KROGER_CLIENT_ID={client_id}\n")
        f.write(f"KROGER_CLIENT_SECRET={client_secret}\n")
        f.write(f"KROGER_ENV={kroger_env}\n")
        f.write(f"KROGER_REDIRECT_URI={redirect_uri}\n")

    # Restrict permissions (Unix only)
    try:
        os.chmod(env_path, 0o600)
    except OSError:
        pass

    print(f"\n✅ Credentials saved to {env_path}")
    print("\nNext step: run `kroger-cart --auth-only` to link your shopper account.")


# ─── Logging Setup ───────────────────────────────────────────────────────────


def setup_logging(json_mode: bool):
    """Configure logging based on output mode.

    In JSON mode, suppress info logs so only JSON goes to stdout.
    """
    level = logging.WARNING if json_mode else logging.INFO
    logging.basicConfig(level=level, format="%(message)s")


# ─── Item Loading ────────────────────────────────────────────────────────────


def load_items_from_csv(filename: str) -> list[dict]:
    """Load items from a CSV file.

    Supports columns: query, name, upc, quantity.
    """
    items = []
    with open(filename, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            item = {}
            if "query" in row:
                item["query"] = row["query"]
            elif "upc" in row:
                item["upc"] = row["upc"]
            elif "name" in row:
                item["query"] = row["name"]

            if "quantity" in row and row["quantity"]:
                item["quantity"] = int(row["quantity"])
            else:
                item["quantity"] = 1

            items.append(item)
    return items


def load_items(args) -> list[dict]:
    """Load items from whichever input method was specified."""
    if args.items:
        return [{"query": name, "quantity": 1} for name in args.items]

    if args.json_input:
        try:
            items = json.loads(args.json_input)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in --json: {e}") from e
        if not isinstance(items, list):
            raise ValueError("--json must be a JSON array, e.g. '[{\"query\": \"milk\"}]'")
        for item in items:
            item.setdefault("quantity", 1)
        return items

    if args.stdin:
        try:
            items = json.loads(sys.stdin.read())
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON from stdin: {e}") from e
        if not isinstance(items, list):
            raise ValueError("stdin must contain a JSON array")
        for item in items:
            item.setdefault("quantity", 1)
        return items

    if args.csv_file:
        return load_items_from_csv(args.csv_file)

    return []


# ─── Item Processing ─────────────────────────────────────────────────────────


def process_items(
    session, access_token: str, api_base: str, items: list[dict],
    zip_code: str, modality: str, dry_run: bool = False,
    location_id: str | None = None,
) -> tuple[list[dict], list[str], str]:
    """Search for items and add them to the cart.

    Searches are done individually (each item needs its own query), but
    cart additions are batched into a single API call for efficiency.

    Args:
        session: HTTP session.
        access_token: OAuth token.
        api_base: API base URL.
        items: List of item dicts with query/upc and quantity.
        zip_code: Store zip code.
        modality: DELIVERY or PICKUP.
        dry_run: If True, search but don't add to cart.
        location_id: Optional pre-resolved location ID.

    Returns:
        Tuple of (added_items, not_found_queries, location_id).
    """
    if not location_id:
        location_id = api.find_location(session, access_token, api_base, zip_code)

    found = []
    not_found = []

    mode_label = "DRY RUN" if dry_run else modality
    logger.info(f"\nProcessing {len(items)} items ({mode_label})...\n")

    # Phase 1: Search for all items
    for item in items:
        query = item.get("query") or item.get("upc") or item.get("name")
        quantity = item.get("quantity", 1)

        logger.info(f"Searching for: {query}...")
        products = api.search_product(session, access_token, api_base, query, location_id)

        if not products:
            logger.info(f"  ❌ Not found: {query}")
            not_found.append(query)
            continue

        product = products[0]
        info = api.extract_product_info(product)
        upc = info["upc"]
        name = info["name"]
        logger.info(f"  ✓ Found: {name} (UPC: {upc})")

        item_data = {"name": name, "upc": upc, "quantity": quantity, "query": query}
        if "price" in info:
            item_data["price"] = info["price"]
        if "promo_price" in info:
            item_data["promo_price"] = info["promo_price"]
        if info.get("on_sale"):
            item_data["on_sale"] = True
            item_data["savings"] = info.get("savings", 0)
            item_data["savings_pct"] = info.get("savings_pct", 0)
        else:
            item_data["on_sale"] = False
        if "in_stock" in info:
            item_data["in_stock"] = info["in_stock"]
        found.append(item_data)

    # Phase 2: Batch add all found items to cart in a single API call
    if found and not dry_run:
        try:
            api.add_to_cart_batch(session, access_token, api_base, found, modality)
            logger.info(f"\n  ✓ Added {len(found)} items to cart in one batch")
        except Exception as e:
            logger.info(f"\n  ❌ Batch cart add failed: {e}")
            # Move all found items to not_found on batch failure
            not_found.extend(item["query"] for item in found)
            found = []
    elif dry_run and found:
        for item in found:
            logger.info(f"  🔍 Would add: {item['name']} (x{item['quantity']}) — DRY RUN")

    return found, not_found, location_id


# ─── Output ──────────────────────────────────────────────────────────────────


def _format_price_str(item: dict) -> str:
    """Format price string with promo pricing and savings."""
    if not item.get("price"):
        return ""
    if item.get("on_sale") and item.get("promo_price"):
        savings = item.get("savings", 0)
        pct = item.get("savings_pct", 0)
        hot = " 🔥" if pct >= 20 else ""
        return (
            f" — ${item['price']:.2f} → ${item['promo_price']:.2f}"
            f" (SAVE ${savings:.2f}, {pct}%){hot}"
        )
    return f" — ${item['price']:.2f}"


def _deal_summary(added: list) -> str | None:
    """Return a total-savings summary line, or None if no deals."""
    deals = [i for i in added if i.get("on_sale")]
    if not deals:
        return None
    total = sum(i.get("savings", 0) * i.get("quantity", 1) for i in deals)
    return f"💰 {len(deals)} item(s) on sale — total savings: ${total:.2f}"


def print_text_summary(
    added: list, not_found: list, modality: str, dry_run: bool, deals_mode: bool = False,
):
    """Print human-readable summary."""
    print("\n" + "=" * 50)
    if deals_mode:
        label = "DEALS CHECK"
    elif dry_run:
        label = "SUMMARY (DRY RUN)"
    else:
        label = "SUMMARY"
    print(label)
    print("=" * 50)

    verb = "Deals found for" if deals_mode else ("Would add" if dry_run else "Successfully added")
    print(f"\n✓ {verb} ({len(added)}):")
    for item in added:
        print(f"  - {item['name']} (x{item['quantity']}){_format_price_str(item)}")

    if not_found:
        print(f"\n✗ Not found or failed ({len(not_found)}):")
        for item in not_found:
            print(f"  - {item}")

    deal_line = _deal_summary(added)
    if deal_line:
        print(f"\n{deal_line}")

    if deals_mode or dry_run:
        if deals_mode:
            print(f"\n🔍 Deals check complete — no items were added to cart.")
        else:
            print(f"\n🔍 Dry run complete — no items were added to cart.")
    else:
        print(f"\n🛒 View your cart: https://www.smithsfoodanddrug.com/cart")
        print("   (Complete checkout manually in your browser)")


def print_json_result(
    added: list, not_found: list, modality: str, dry_run: bool, deals_mode: bool = False,
):
    """Print machine-readable JSON result."""
    deals = [i for i in added if i.get("on_sale")]
    total_savings = sum(i.get("savings", 0) * i.get("quantity", 1) for i in deals)
    result = {
        "success": True,
        "dry_run": dry_run,
        "deals_mode": deals_mode,
        "added": added,
        "not_found": not_found,
        "added_count": len(added),
        "not_found_count": len(not_found),
        "deals_count": len(deals),
        "total_savings": round(total_savings, 2),
        "cart_url": "https://www.smithsfoodanddrug.com/cart",
        "modality": modality,
    }
    print(json.dumps(result, indent=2))


def print_cart_text(cart_items: list):
    """Print human-readable cart contents."""
    print("\n" + "=" * 50)
    print("CURRENT CART")
    print("=" * 50)

    if not cart_items:
        print("\n🛒 Your cart is empty.")
        return

    print(f"\n🛒 {len(cart_items)} item(s) in cart:")
    for item in cart_items:
        upc = item.get("upc", "?")
        qty = item.get("quantity", 1)
        print(f"  - UPC {upc} (x{qty})")

    print(f"\n🔗 https://www.smithsfoodanddrug.com/cart")


def print_cart_json(cart_items: list):
    """Print machine-readable cart contents."""
    print(json.dumps({
        "success": True,
        "cart_items": cart_items,
        "item_count": len(cart_items),
        "cart_url": "https://www.smithsfoodanddrug.com/cart",
    }, indent=2))



# ─── Main ────────────────────────────────────────────────────────────────────


def build_config(args) -> dict:
    """Build configuration dict from environment and CLI args."""
    env = args.env
    base_domain = "api-ce.kroger.com" if env == "CERT" else "api.kroger.com"
    config_dir = get_config_dir()

    return {
        "client_id": os.environ.get("KROGER_CLIENT_ID", ""),
        "client_secret": os.environ.get("KROGER_CLIENT_SECRET", ""),
        "redirect_uri": os.environ.get("KROGER_REDIRECT_URI", "http://localhost:3000"),
        "auth_url": f"https://{base_domain}/v1/connect/oauth2/authorize",
        "token_url": f"https://{base_domain}/v1/connect/oauth2/token",
        "api_base": f"https://{base_domain}/v1",
        "token_file": os.path.join(config_dir, "tokens.json"),
    }


def main(argv=None):
    """Main entry point."""
    # Load .env from config directory first, then fall back to project directory
    config_dir = get_config_dir()
    config_env = os.path.join(config_dir, ".env")
    project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    project_env = os.path.join(project_dir, ".env")

    # Config dir takes priority; project dir is fallback for backward compat
    load_dotenv(config_env)
    load_dotenv(project_env)  # Won't override already-set vars

    args = parse_args(argv)
    json_mode = args.output == "json"
    setup_logging(json_mode)

    # Setup mode — interactive credential configuration
    if args.setup:
        run_setup()
        return

    config = build_config(args)
    session = create_session()

    # Resolve storage backend
    force_storage = None if args.token_storage == "auto" else args.token_storage
    storage = get_storage_backend(config["token_file"], force=force_storage)
    token_mgr = TokenManager(config, session, storage)

    # Auth-only mode
    if args.auth_only:
        logger.info("Running authentication only...")
        token_mgr.get_access_token()
        logger.info("Authentication successful! Tokens saved.")
        if json_mode:
            print(json.dumps({"success": True, "message": "Authenticated successfully."}))
        return

    # Cart view mode
    if args.cart:
        try:
            access_token = token_mgr.get_access_token()
            cart_items = api.get_cart(session, access_token, config["api_base"])
            if json_mode:
                print_cart_json(cart_items)
            else:
                print_cart_text(cart_items)
        except requests.exceptions.ConnectionError:
            _handle_connection_error(json_mode)
        except Exception as e:
            if json_mode:
                print(json.dumps({"success": False, "error": str(e)}))
                sys.exit(1)
            else:
                print(f"\n❌ Error: {e}")
                raise
        return

    # Load items
    try:
        items = load_items(args)
    except ValueError as e:
        print(f"Error: {e}\n", file=sys.stderr)
        if json_mode:
            print(json.dumps({"success": False, "error": str(e)}))
        sys.exit(1)

    if not items:
        print("Error: No items provided.\n", file=sys.stderr)
        print("Usage:", file=sys.stderr)
        print('  kroger-cart --items "milk" "eggs" "bread"', file=sys.stderr)
        print("  kroger-cart --json '[{\"query\": \"milk\", \"quantity\": 2}]'", file=sys.stderr)
        print("  echo '[{\"query\": \"eggs\"}]' | kroger-cart --stdin", file=sys.stderr)
        print("  kroger-cart groceries.csv", file=sys.stderr)
        if json_mode:
            print(json.dumps({"success": False, "error": "No items provided."}))
        sys.exit(1)

    # --deals implies --dry-run
    deals_mode = args.deals
    dry_run = args.dry_run or deals_mode

    try:
        access_token = token_mgr.get_access_token()
        added, not_found, location_id = process_items(
            session=session,
            access_token=access_token,
            api_base=config["api_base"],
            items=items,
            zip_code=args.zip,
            modality=args.modality,
            dry_run=dry_run,
        )

        if json_mode:
            print_json_result(added, not_found, args.modality, dry_run, deals_mode)
        else:
            print_text_summary(added, not_found, args.modality, dry_run, deals_mode)

    except requests.exceptions.ConnectionError:
        _handle_connection_error(json_mode)
    except Exception as e:
        if json_mode:
            print(json.dumps({"success": False, "error": str(e)}))
            sys.exit(1)
        else:
            print(f"\n❌ Error: {e}")
            raise


def _handle_connection_error(json_mode: bool):
    """Handle network connection errors with a user-friendly message."""
    msg = "Network error: could not connect to the Kroger API. Check your internet connection."
    if json_mode:
        print(json.dumps({"success": False, "error": msg}))
    else:
        print(f"\n❌ {msg}")
    sys.exit(1)
