"""
Kroger API client functions: location lookup, product search, and cart management.
"""

import logging
from requests import Session

logger = logging.getLogger(__name__)


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
    params = {
        "filter.term": query,
        "filter.locationId": location_id,
        "filter.limit": 5,
    }

    response = session.get(url, headers=get_headers(access_token), params=params)
    response.raise_for_status()
    data = response.json()

    return data.get("data", [])


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
