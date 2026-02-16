"""
Kroger API client functions: location lookup, product search, and cart management.
"""

import re
import logging
from requests import Session

logger = logging.getLogger(__name__)


def sanitize_query(query: str) -> str:
    """Strip special characters and excess detail that cause 400 errors.

    The Kroger API's filter.term parameter is sensitive to characters
    like &, #, @, and overly specific size descriptors.
    """
    # Remove special characters (keep letters, numbers, spaces, periods)
    cleaned = re.sub(r"[^a-zA-Z0-9\s.]", " ", query)
    # Collapse multiple spaces
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def simplify_query(query: str) -> str:
    """Reduce a query to its core terms by removing size/quantity words.

    Used as a fallback when a specific query gets a 400 error.
    """
    # Remove common size/quantity patterns
    noise = r"\b(\d+\s*(oz|lb|lbs|ct|count|pack|pk|fl|gal|gallon|kg|g|ml|liter|litre)s?)\b"
    simplified = re.sub(noise, "", query, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", simplified).strip()


def extract_product_info(product: dict) -> dict:
    """Extract useful product info including price and stock."""
    info = {
        "upc": product["upc"],
        "name": product.get("description", "Unknown"),
        "brand": product.get("brand", ""),
    }

    # Extract price from items array
    items = product.get("items", [])
    if items:
        item = items[0]
        price_info = item.get("price", {})
        if price_info:
            info["price"] = price_info.get("regular", price_info.get("promo"))
            if price_info.get("promo"):
                info["promo_price"] = price_info["promo"]
        fulfillment = item.get("fulfillment", {})
        info["in_stock"] = fulfillment.get("inStock", True)

    return info


def get_headers(access_token: str) -> dict:
    """Build standard API request headers."""
    return {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }


def find_location(
    session: Session, access_token: str, api_base: str, zip_code: str, chain: str = "Smiths"
) -> str:
    """Find a store location by zip code.

    Args:
        session: HTTP session.
        access_token: OAuth access token.
        api_base: Kroger API base URL.
        zip_code: Zip code to search near.
        chain: Store chain name (default: Smiths).

    Returns:
        Location ID string.

    Raises:
        Exception: If no locations are found.
    """
    url = f"{api_base}/locations"
    params = {
        "filter.zipCode.near": zip_code,
        "filter.chain": chain,
        "filter.limit": 1,
    }

    response = session.get(url, headers=get_headers(access_token), params=params)
    response.raise_for_status()
    data = response.json()

    if not data.get("data"):
        raise Exception(f"No {chain} locations found near zip {zip_code}")

    location = data["data"][0]
    addr = location["address"]
    logger.info(f"Found location: {location['name']} ({addr['addressLine1']}, {addr['city']})")
    return location["locationId"]


def search_product(
    session: Session, access_token: str, api_base: str, query: str, location_id: str
) -> list[dict]:
    """Search for products at a specific location.

    Automatically sanitizes the query and retries with a simplified
    version if the API returns a 400 error.

    Args:
        session: HTTP session.
        access_token: OAuth access token.
        api_base: Kroger API base URL.
        query: Search term.
        location_id: Store location ID.

    Returns:
        List of product dicts from the API.
    """
    url = f"{api_base}/products"
    clean_query = sanitize_query(query)

    # Try with sanitized query first
    attempts = [clean_query]
    simplified = simplify_query(clean_query)
    if simplified and simplified != clean_query:
        attempts.append(simplified)

    for attempt in attempts:
        params = {
            "filter.term": attempt,
            "filter.locationId": location_id,
            "filter.limit": 5,
        }

        response = session.get(url, headers=get_headers(access_token), params=params)

        if response.status_code == 400:
            logger.debug(f"  Query '{attempt}' got 400, trying simpler query...")
            continue

        response.raise_for_status()
        data = response.json()
        results = data.get("data", [])

        if results:
            if attempt != clean_query:
                logger.debug(f"  Found results with simplified query: '{attempt}'")
            return results

    return []


def add_to_cart(
    session: Session,
    access_token: str,
    api_base: str,
    upc: str,
    quantity: int = 1,
    modality: str = "DELIVERY",
) -> dict:
    """Add an item to the cart.

    Args:
        session: HTTP session.
        access_token: OAuth access token.
        api_base: Kroger API base URL.
        upc: Product UPC code.
        quantity: Number of items.
        modality: Fulfillment type (DELIVERY or PICKUP).

    Returns:
        Response dict (may be empty on 204 No Content).
    """
    url = f"{api_base}/cart/add"
    payload = {
        "items": [{"upc": upc, "quantity": quantity}],
        "modality": modality,
    }

    response = session.put(
        url,
        headers={**get_headers(access_token), "Content-Type": "application/json"},
        json=payload,
    )
    response.raise_for_status()

    # Kroger returns 204 No Content on success
    if response.content:
        return response.json()
    return {"status": response.status_code}


def add_to_cart_batch(
    session: Session,
    access_token: str,
    api_base: str,
    items: list[dict],
    modality: str = "DELIVERY",
) -> dict:
    """Add multiple items to the cart in a single API call.

    Args:
        session: HTTP session.
        access_token: OAuth access token.
        api_base: Kroger API base URL.
        items: List of dicts with 'upc' and 'quantity' keys.
        modality: Fulfillment type (DELIVERY or PICKUP).

    Returns:
        Response dict (may be empty on 204 No Content).
    """
    url = f"{api_base}/cart/add"
    payload = {
        "items": [{"upc": item["upc"], "quantity": item.get("quantity", 1)} for item in items],
        "modality": modality,
    }

    response = session.put(
        url,
        headers={**get_headers(access_token), "Content-Type": "application/json"},
        json=payload,
    )
    response.raise_for_status()

    if response.content:
        return response.json()
    return {"status": response.status_code}
