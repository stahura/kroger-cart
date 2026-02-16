#!/usr/bin/env python3
"""Quick wrapper to add items from JSON to Kroger cart."""

import sys
import json
import os

# Add the kroger-cart directory to path
sys.path.insert(0, os.path.expanduser('~/.openclaw/workspace/projects/kroger-cart'))

# Import the functions from smiths_cart
from smiths_cart import get_access_token, find_location, search_product, add_to_cart, process_items

# Load items from JSON argument
if len(sys.argv) < 2:
    print("Usage: python add_items.py '<json_array>'")
    sys.exit(1)

items_json = sys.argv[1]
items = json.loads(items_json)

print(f"Adding {len(items)} items to cart...")
print()

try:
    added, not_found, location_id = process_items(items)
    
    print("\n" + "="*50)
    print("ORDER SUMMARY")
    print("="*50)
    
    print(f"\n✅ Successfully added ({len(added)}):")
    for item in added:
        print(f"  - {item['name']} (x{item['quantity']})")
    
    if not_found:
        print(f"\n❌ Not found ({len(not_found)}):")
        for item in not_found:
            print(f"  - {item}")
    
    print(f"\n🛒 View your cart: https://www.smithsfoodanddrug.com/cart")
    print("   (Complete checkout manually in your browser)")
    
except Exception as e:
    print(f"\n❌ Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
