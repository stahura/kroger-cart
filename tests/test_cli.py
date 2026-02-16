"""Tests for the CLI module."""

import json
import sys
from unittest.mock import patch, MagicMock

import pytest

from kroger_cart.cli import parse_args, load_items, load_items_from_csv


class TestParseArgs:
    """Test argument parsing."""

    def test_items_flag(self):
        args = parse_args(["--items", "milk", "eggs", "bread"])
        assert args.items == ["milk", "eggs", "bread"]

    def test_json_flag(self):
        args = parse_args(["--json", '[{"query": "milk"}]'])
        assert args.json_input == '[{"query": "milk"}]'

    def test_stdin_flag(self):
        args = parse_args(["--stdin"])
        assert args.stdin is True

    def test_csv_positional(self):
        args = parse_args(["groceries.csv"])
        assert args.csv_file == "groceries.csv"

    def test_dry_run_flag(self):
        args = parse_args(["--items", "milk", "--dry-run"])
        assert args.dry_run is True

    def test_output_json(self):
        args = parse_args(["--items", "milk", "--output", "json"])
        assert args.output == "json"

    def test_modality(self):
        args = parse_args(["--items", "milk", "--modality", "PICKUP"])
        assert args.modality == "PICKUP"

    def test_zip(self):
        args = parse_args(["--items", "milk", "--zip", "90210"])
        assert args.zip == "90210"

    def test_token_storage(self):
        args = parse_args(["--items", "milk", "--token-storage", "keyring"])
        assert args.token_storage == "keyring"

    def test_defaults(self):
        args = parse_args(["--items", "milk"])
        assert args.output == "text"
        assert args.modality == "DELIVERY"
        assert args.dry_run is False
        assert args.deals is False
        assert args.auth_only is False
        assert args.token_storage == "auto"

    def test_deals_flag(self):
        args = parse_args(["--items", "milk", "--deals"])
        assert args.deals is True


class TestLoadItems:
    """Test item loading from different sources."""

    def test_from_items_flag(self):
        args = parse_args(["--items", "milk", "eggs"])
        items = load_items(args)
        assert items == [
            {"query": "milk", "quantity": 1},
            {"query": "eggs", "quantity": 1},
        ]

    def test_from_json_flag(self):
        args = parse_args(["--json", '[{"query": "milk", "quantity": 2}]'])
        items = load_items(args)
        assert items == [{"query": "milk", "quantity": 2}]

    def test_empty_when_no_input(self):
        args = parse_args([])
        items = load_items(args)
        assert items == []


class TestLoadCSV:
    """Test CSV loading."""

    def test_basic_csv(self, tmp_path):
        csv_file = tmp_path / "items.csv"
        csv_file.write_text("query,quantity\nmilk,2\neggs,1\n")
        items = load_items_from_csv(str(csv_file))
        assert len(items) == 2
        assert items[0] == {"query": "milk", "quantity": 2}
        assert items[1] == {"query": "eggs", "quantity": 1}

    def test_csv_with_name_column(self, tmp_path):
        csv_file = tmp_path / "items.csv"
        csv_file.write_text("name,quantity\nbutter,1\n")
        items = load_items_from_csv(str(csv_file))
        assert items[0]["query"] == "butter"

    def test_csv_missing_quantity(self, tmp_path):
        csv_file = tmp_path / "items.csv"
        csv_file.write_text("query\nmilk\n")
        items = load_items_from_csv(str(csv_file))
        assert items[0]["quantity"] == 1
