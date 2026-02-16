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

