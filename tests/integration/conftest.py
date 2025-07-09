# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import os
from pathlib import Path

import pytest
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

FILESYSTEM_CLIENT_DIR = (
    Path(filesystem_client) if (filesystem_client := os.getenv("FILESYSTEM_CLIENT_DIR")) else None
)
NFS_SERVER_PROXY_DIR = (
    Path(nfs_server_proxy) if (nfs_server_proxy := os.getenv("NFS_SERVER_PROXY_DIR")) else None
)
CEPHFS_SERVER_PROXY_DIR = (
    Path(cephfs_server_proxy)
    if (cephfs_server_proxy := os.getenv("CEPHFS_SERVER_PROXY_DIR"))
    else None
)


def pytest_addoption(parser) -> None:
    parser.addoption(
        "--charm-base",
        action="store",
        default="ubuntu@24.04",
        help="Charm base version to use for integration tests",
    )


@pytest.fixture(scope="module")
def charm_base(request) -> str:
    """Get slurmctld charm base to use."""
    return request.config.option.charm_base


@pytest.fixture(scope="module")
async def filesystem_client_charm(request, ops_test: OpsTest) -> str | Path:
    """Pack filesystem-client charm to use for integration tests.

    If the `FILESYSTEM_CLIENT_DIR` environment variable is not set, this will pull the charm from
    Charmhub instead.

    Returns:
        `Path` if "filesystem-client" is built locally. `str` otherwise.
    """
    if not FILESYSTEM_CLIENT_DIR:
        logger.info("Pulling filesystem-client from Charmhub")
        return "filesystem-client"

    return await ops_test.build_charm(FILESYSTEM_CLIENT_DIR, verbosity="verbose")


@pytest.fixture(scope="module")
async def nfs_server_proxy_charm(request, ops_test: OpsTest) -> str | Path:
    """Pack nfs-server-proxy charm to use for integration tests.

    If the `NFS_SERVER_PROXY_DIR` environment variable is not set, this will pull the charm from
    Charmhub instead.

    Returns:
        `Path` if "nfs-server-proxy" is built locally. `str` otherwise..
    """
    if not NFS_SERVER_PROXY_DIR:
        logger.info("Pulling nfs-server-proxy from Charmhub")
        return "nfs-server-proxy"

    return await ops_test.build_charm(NFS_SERVER_PROXY_DIR, verbosity="verbose")


@pytest.fixture(scope="module")
async def cephfs_server_proxy_charm(request, ops_test: OpsTest) -> str | Path:
    """Pack cephfs-server-proxy charm to use for integration tests.

    If the `CEPHFS_SERVER_PROXY_DIR` environment variable is not set, this will pull the charm from
    Charmhub instead.

    Returns:
        `Path` if "cephfs-server-proxy" is built locally. `str` otherwise.
    """
    if not CEPHFS_SERVER_PROXY_DIR:
        logger.info("Pulling cephfs-server-proxy from Charmhub")
        return "cephfs-server-proxy"

    return await ops_test.build_charm(CEPHFS_SERVER_PROXY_DIR, verbosity="verbose")


@pytest.fixture(scope="module")
async def test_mount_client_charm(request, ops_test: OpsTest) -> Path:
    """Pack test-mount-client charm to use for integration tests.

    Returns:
        `Path` of the charm file built locally.
    """
    return await ops_test.build_charm(
        Path(os.getenv("TEST_MOUNT_CLIENT_DIR")), verbosity="verbose"
    )
