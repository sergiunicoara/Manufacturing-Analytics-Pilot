"""Live-database tests need SQL Server (and, for least privilege, two logins). By default they skip when
unavailable so the suite runs anywhere; with REQUIRE_DB_TESTS=1 an unavailable database is a FAILURE,
so a run can never look green while silently skipping them."""
import os

import pytest


def unavailable(reason: str):
    if os.environ.get("REQUIRE_DB_TESTS") == "1":
        pytest.fail(f"REQUIRE_DB_TESTS=1 but a database test cannot run: {reason}", pytrace=False)
    pytest.skip(reason)
