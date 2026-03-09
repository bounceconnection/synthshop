"""Tests for the ProductStore — CRUD operations on product JSON files."""

import pytest

from synthshop.core.models import Condition, Product, ProductStatus
from synthshop.core.product_store import ProductStore


@pytest.fixture
def store(tmp_path):
    """ProductStore backed by a temporary directory."""
    return ProductStore(products_dir=tmp_path)


@pytest.fixture
def sample_product():
    """A minimal product for testing."""
    return Product(make="Roland", model="Juno-106", price=1200.0, condition=Condition.GOOD)


class TestSave:
    def test_save_creates_json_file(self, store, sample_product):
        path = store.save(sample_product)
        assert path.exists()
        assert path.suffix == ".json"
        assert path.name == f"{sample_product.id}.json"

    def test_save_overwrites_existing(self, store, sample_product):
        store.save(sample_product)
        sample_product.price = 1100.0
        store.save(sample_product)
        reloaded = store.load(sample_product.id)
        assert reloaded.price == 1100.0


class TestLoad:
    def test_load_existing(self, store, sample_product):
        store.save(sample_product)
        loaded = store.load(sample_product.id)
        assert loaded.make == "Roland"
        assert loaded.model == "Juno-106"
        assert loaded.price == 1200.0
        assert loaded.id == sample_product.id

    def test_load_missing_raises(self, store):
        with pytest.raises(FileNotFoundError, match="Product not found"):
            store.load("nonexistent")


class TestListAll:
    def test_list_empty(self, store):
        assert store.list_all() == []

    def test_list_multiple(self, store):
        p1 = Product(make="Roland", model="Juno-106", price=1200.0)
        p2 = Product(make="Moog", model="Sub 37", price=1500.0)
        store.save(p1)
        store.save(p2)
        products = store.list_all()
        assert len(products) == 2

    def test_list_sorted_newest_first(self, store):
        p1 = Product(make="Roland", model="Juno-106", price=1200.0)
        store.save(p1)
        p2 = Product(make="Moog", model="Sub 37", price=1500.0)
        store.save(p2)
        products = store.list_all()
        # p2 was saved second, so its updated_at is newer
        assert products[0].id == p2.id

    def test_list_skips_malformed_files(self, store, sample_product):
        store.save(sample_product)
        # Write a malformed JSON file
        (store.products_dir / "bad.json").write_text("not valid json")
        products = store.list_all()
        assert len(products) == 1


class TestListByStatus:
    def test_filter_by_status(self, store):
        p1 = Product(make="Roland", model="Juno-106", price=1200.0, status=ProductStatus.DRAFT)
        p2 = Product(make="Moog", model="Sub 37", price=1500.0, status=ProductStatus.LISTED)
        p3 = Product(make="Korg", model="MS-20", price=900.0, status=ProductStatus.DRAFT)
        store.save(p1)
        store.save(p2)
        store.save(p3)
        drafts = store.list_by_status(ProductStatus.DRAFT)
        assert len(drafts) == 2
        listed = store.list_by_status(ProductStatus.LISTED)
        assert len(listed) == 1
        assert listed[0].make == "Moog"


class TestDelete:
    def test_delete_existing(self, store, sample_product):
        store.save(sample_product)
        store.delete(sample_product.id)
        assert not store.exists(sample_product.id)

    def test_delete_missing_raises(self, store):
        with pytest.raises(FileNotFoundError, match="Product not found"):
            store.delete("nonexistent")


class TestExists:
    def test_exists_true(self, store, sample_product):
        store.save(sample_product)
        assert store.exists(sample_product.id)

    def test_exists_false(self, store):
        assert not store.exists("nonexistent")
