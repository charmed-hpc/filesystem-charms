#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Configure integration test run."""

import pathlib
from typing import Any, Coroutine

import pytest
from pytest_operator.plugin import OpsTest


@pytest.fixture(scope="module")
async def nfs_server_proxy_charm(ops_test: OpsTest) -> Coroutine[Any, Any, pathlib.Path]:
    """Build nfs-server-proxy charm to use for integration tests."""
    charm = await ops_test.build_charm(".")
    return charm
