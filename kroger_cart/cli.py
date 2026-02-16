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

from dotenv import load_dotenv

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
        """,
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
        "--token-storage",
        choices=["auto", "file", "keyring"],
        default="auto",
        help="Token storage backend (default: auto-detect).",
    )

    return parser.parse_args(argv)


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
        return [{"query": item, "quantity": 1} for item in args.items]

    if args.json_input:
        return json.loads(args.json_input)

    if args.stdin:
        raw = sys.stdin.read().strip()
        return json.loads(raw)

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
        upc = product["upc"]
        name = product.get("description", "Unknown")
        logger.info(f"  ✓ Found: {name} (UPC: {upc})")
        found.append({"name": name, "upc": upc, "quantity": quantity, "query": query})

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


def print_text_summary(added: list, not_found: list, modality: str, dry_run: bool):
    """Print human-readable summary."""
    print("\n" + "=" * 50)
    label = "SUMMARY (DRY RUN)" if dry_run else "SUMMARY"
    print(label)
    print("=" * 50)

    verb = "Would add" if dry_run else "Successfully added"
    print(f"\n✓ {verb} ({len(added)}):")
    for item in added:
        print(f"  - {item['name']} (x{item['quantity']})")

    if not_found:
        print(f"\n✗ Not found or failed ({len(not_found)}):")
        for item in not_found:
            print(f"  - {item}")

    if not dry_run:
        print(f"\n🛒 View your cart: https://www.smithsfoodanddrug.com/cart")
        print("   (Complete checkout manually in your browser)")
    else:
        print(f"\n🔍 Dry run complete — no items were added to cart.")


def print_json_result(added: list, not_found: list, modality: str, dry_run: bool):
    """Print machine-readable JSON result."""
    result = {
        "success": True,
        "dry_run": dry_run,
        "added": added,
        "not_found": not_found,
        "added_count": len(added),
        "not_found_count": len(not_found),
        "cart_url": "https://www.smithsfoodanddrug.com/cart",
        "modality": modality,
    }
    print(json.dumps(result, indent=2))


# ─── Main ────────────────────────────────────────────────────────────────────


def build_config(args) -> dict:
    """Build configuration dict from environment and CLI args."""
    env = args.env
    base_domain = "api-ce.kroger.com" if env == "CERT" else "api.kroger.com"

    return {
        "client_id": os.environ.get("KROGER_CLIENT_ID", ""),
        "client_secret": os.environ.get("KROGER_CLIENT_SECRET", ""),
        "redirect_uri": "http://localhost:3000",
        "auth_url": f"https://{base_domain}/v1/connect/oauth2/authorize",
        "token_url": f"https://{base_domain}/v1/connect/oauth2/token",
        "api_base": f"https://{base_domain}/v1",
        "token_file": os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tokens.json"
        ),
    }


def main(argv=None):
    """Main entry point."""
    # Load .env from the project directory
    project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    load_dotenv(os.path.join(project_dir, ".env"))

    args = parse_args(argv)
    json_mode = args.output == "json"
    setup_logging(json_mode)

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

    # Load items
    items = load_items(args)

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

    try:
        access_token = token_mgr.get_access_token()
        added, not_found, location_id = process_items(
            session=session,
            access_token=access_token,
            api_base=config["api_base"],
            items=items,
            zip_code=args.zip,
            modality=args.modality,
            dry_run=args.dry_run,
        )

        if json_mode:
            print_json_result(added, not_found, args.modality, args.dry_run)
        else:
            print_text_summary(added, not_found, args.modality, args.dry_run)

    except Exception as e:
        if json_mode:
            print(json.dumps({"success": False, "error": str(e)}))
            sys.exit(1)
        else:
            print(f"\n❌ Error: {e}")
            raise
