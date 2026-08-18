"""Shared test fixtures for OpenSoul integration tests."""

import httpx
import pytest

BASE_URL = "http://localhost:8090"


@pytest.fixture(scope="session")
def base_url():
    return BASE_URL


@pytest.fixture(scope="session")
def client():
    """Shared HTTP client for all tests."""
    with httpx.Client(base_url=BASE_URL, timeout=30) as c:
        yield c
