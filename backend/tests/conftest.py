"""Shared fixtures for CP2 tests. Generation is expensive enough (a few
seconds) that we run it once per test session rather than per test."""
from __future__ import annotations

import pytest

from app.synthetic.run_generator import generate


@pytest.fixture(scope="session")
def staging_dir(tmp_path_factory):
    return str(tmp_path_factory.mktemp("staging"))


@pytest.fixture(scope="session")
def generated_ctx(staging_dir):
    return generate(staging_dir)


@pytest.fixture(scope="session")
def tables(generated_ctx):
    return generated_ctx.tables
