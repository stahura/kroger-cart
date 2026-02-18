"""Tests for the API module."""

import json
from unittest.mock import MagicMock

import pytest

from kroger_cart.api import find_location, search_product, add_to_cart, extract_product_info


class TestFindLocation:
    """Test location lookup."""

    def test_returns_location_id(self):
        session = MagicMock()
        response = MagicMock()
        response.json.return_value = {
            "data": [
                {
                    "locationId": "01400376",
                    "name": "Smith's",
                    "address": {
                        "addressLine1": "123 Main St",
                        "city": "Lehi",
                    },
                }
            ]
        }
        response.raise_for_status = MagicMock()
        session.get.return_value = response

        result = find_location(session, "token", "https://api.kroger.com/v1", "84045")
        assert result == "01400376"

    def test_raises_when_no_locations(self):
        session = MagicMock()
        response = MagicMock()
        response.json.return_value = {"data": []}
        response.raise_for_status = MagicMock()
        session.get.return_value = response

        with pytest.raises(Exception, match="No Smiths locations found"):
            find_location(session, "token", "https://api.kroger.com/v1", "00000")


class TestSearchProduct:
    """Test product search."""

    def test_returns_product_list(self):
        session = MagicMock()
        response = MagicMock()
        response.json.return_value = {
            "data": [
                {"upc": "001111", "description": "Milk"},
                {"upc": "002222", "description": "Milk 2%"},
            ]
        }
        response.raise_for_status = MagicMock()
        session.get.return_value = response

        results = search_product(session, "token", "https://api.kroger.com/v1", "milk", "loc1")
        assert len(results) == 2
        assert results[0]["upc"] == "001111"

    def test_returns_empty_on_no_match(self):
        session = MagicMock()
        response = MagicMock()
        response.json.return_value = {"data": []}
        response.raise_for_status = MagicMock()
        session.get.return_value = response

        results = search_product(session, "token", "https://api.kroger.com/v1", "xyz", "loc1")
        assert results == []


class TestAddToCart:
    """Test cart addition."""

    def test_handles_204_no_content(self):
        session = MagicMock()
        response = MagicMock()
        response.content = b""  # Empty body
        response.status_code = 204
        response.raise_for_status = MagicMock()
        session.put.return_value = response

        result = add_to_cart(session, "token", "https://api.kroger.com/v1", "001111")
        assert result["status"] == 204

    def test_handles_json_response(self):
        session = MagicMock()
        response = MagicMock()
        response.content = b'{"status": "ok"}'
        response.json.return_value = {"status": "ok"}
        response.raise_for_status = MagicMock()
        session.put.return_value = response

        result = add_to_cart(session, "token", "https://api.kroger.com/v1", "001111", 2, "PICKUP")
        assert result == {"status": "ok"}


class TestExtractProductInfo:
    """Test product info extraction with deal/promo fields."""

    def test_with_promo_price(self):
        product = {
            "upc": "001111",
            "description": "Kroger Milk",
            "brand": "Kroger",
            "items": [{
                "price": {"regular": 3.99, "promo": 2.99},
                "fulfillment": {"inStock": True},
            }],
        }
        info = extract_product_info(product)
        assert info["on_sale"] is True
        assert info["price"] == 3.99
        assert info["promo_price"] == 2.99
        assert info["savings"] == 1.0
        assert info["savings_pct"] == 25
        assert info["in_stock"] is True

    def test_without_promo(self):
        product = {
            "upc": "002222",
            "description": "Eggs",
            "brand": "Store",
            "items": [{
                "price": {"regular": 2.79},
                "fulfillment": {"inStock": True},
            }],
        }
        info = extract_product_info(product)
        assert info["on_sale"] is False
        assert info["price"] == 2.79
        assert "promo_price" not in info
        assert "savings" not in info

    def test_with_national_pricing(self):
        product = {
            "upc": "003333",
            "description": "Bread",
            "brand": "Wonder",
            "items": [{
                "price": {"regular": 3.29, "promo": 2.50},
                "nationalPrice": {"regular": 3.49, "promo": 2.99},
                "fulfillment": {"inStock": True},
            }],
        }
        info = extract_product_info(product)
        assert info["on_sale"] is True
        assert info["national_price"] == 3.49
        assert info["national_promo"] == 2.99
        assert info["savings"] == 0.79
        assert info["savings_pct"] == 24

    def test_no_items_array(self):
        product = {
            "upc": "004444",
            "description": "Mystery Item",
        }
        info = extract_product_info(product)
        assert info["on_sale"] is False
        assert "price" not in info


class TestGetCart:
    """Test cart retrieval."""

    def test_returns_cart_items(self):
        session = MagicMock()
        response = MagicMock()
        response.status_code = 200
        response.content = b'{"data": [{"upc": "001111", "quantity": 2}]}'
        response.json.return_value = {"data": [{"upc": "001111", "quantity": 2}]}
        response.raise_for_status = MagicMock()
        session.get.return_value = response

        from kroger_cart.api import get_cart
        result = get_cart(session, "token", "https://api.kroger.com/v1")
        assert len(result) == 1
        assert result[0]["upc"] == "001111"

    def test_returns_empty_on_204(self):
        session = MagicMock()
        response = MagicMock()
        response.status_code = 204
        response.content = b""
        response.raise_for_status = MagicMock()
        session.get.return_value = response

        from kroger_cart.api import get_cart
        result = get_cart(session, "token", "https://api.kroger.com/v1")
        assert result == []

    def test_raises_on_401(self):
        session = MagicMock()
        response = MagicMock()
        response.status_code = 401
        session.get.return_value = response

        from kroger_cart.api import get_cart
        with pytest.raises(Exception, match="Authentication expired"):
            get_cart(session, "token", "https://api.kroger.com/v1")


class TestAddToCartBatch:
    """Test batch cart addition."""

    def test_handles_204_no_content(self):
        session = MagicMock()
        response = MagicMock()
        response.content = b""
        response.status_code = 204
        response.raise_for_status = MagicMock()
        session.put.return_value = response

        from kroger_cart.api import add_to_cart_batch
        result = add_to_cart_batch(
            session, "token", "https://api.kroger.com/v1",
            [{"upc": "001111", "quantity": 1}, {"upc": "002222", "quantity": 2}],
        )
        assert result["status"] == 204

        # Verify correct payload structure
        call_args = session.put.call_args
        payload = call_args.kwargs.get("json") or call_args[1].get("json")
        assert len(payload["items"]) == 2

    def test_raises_on_401(self):
        session = MagicMock()
        response = MagicMock()
        response.status_code = 401
        session.put.return_value = response

        from kroger_cart.api import add_to_cart_batch
        with pytest.raises(Exception, match="Authentication expired"):
            add_to_cart_batch(
                session, "token", "https://api.kroger.com/v1",
                [{"upc": "001111"}],
            )


class TestSanitizeQuery:
    """Test query sanitization."""

    def test_removes_special_characters(self):
        from kroger_cart.api import sanitize_query
        assert sanitize_query("M&M's candy") == "M M s candy"

    def test_collapses_whitespace(self):
        from kroger_cart.api import sanitize_query
        assert sanitize_query("  too   many   spaces  ") == "too many spaces"

    def test_keeps_periods(self):
        from kroger_cart.api import sanitize_query
        assert sanitize_query("Dr. Pepper") == "Dr. Pepper"


class TestSimplifyQuery:
    """Test query simplification."""

    def test_removes_size_descriptors(self):
        from kroger_cart.api import simplify_query
        result = simplify_query("milk 1 gallon")
        assert "gallon" not in result.lower()
        assert "milk" in result.lower()

    def test_removes_ounce_counts(self):
        from kroger_cart.api import simplify_query
        result = simplify_query("yogurt 32 oz")
        assert "oz" not in result.lower()
        assert "yogurt" in result.lower()

    def test_preserves_core_terms(self):
        from kroger_cart.api import simplify_query
        assert simplify_query("whole wheat bread") == "whole wheat bread"


class TestSearchProduct401:
    """Test that search_product raises on 401."""

    def test_raises_on_401(self):
        session = MagicMock()
        response = MagicMock()
        response.status_code = 401
        session.get.return_value = response

        with pytest.raises(Exception, match="Authentication expired"):
            search_product(session, "token", "https://api.kroger.com/v1", "milk", "loc1")


