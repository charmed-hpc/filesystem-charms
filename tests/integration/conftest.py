# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import os
from collections.abc import Iterator
from pathlib import Path

import jubilant
import pytest

logger = logging.getLogger(__name__)

LOCAL_FILESYSTEM_CLIENT = (
    Path(filesystem_client)
    if (filesystem_client := os.getenv("LOCAL_FILESYSTEM_CLIENT"))
    else None
)
LOCAL_NFS_SERVER_PROXY = (
    Path(nfs_server_proxy) if (nfs_server_proxy := os.getenv("LOCAL_NFS_SERVER_PROXY")) else None
)
LOCAL_CEPHFS_SERVER_PROXY = (
    Path(cephfs_server_proxy)
    if (cephfs_server_proxy := os.getenv("LOCAL_CEPHFS_SERVER_PROXY"))
    else None
)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--charm-base",
        action="store",
        default="ubuntu@24.04",
        help="Charm base version to use for integration tests",
    )
    parser.addoption(
        "--keep-models",
        action="store_true",
        default=False,
        help="Keep temporary Juju models after tests complete",
    )


@pytest.fixture(scope="session")
def juju(request: pytest.FixtureRequest) -> Iterator[jubilant.Juju]:
    """Create a temporary Juju model for the test session."""
    keep_models = bool(request.config.getoption("--keep-models"))
    with jubilant.temp_model(keep=keep_models) as juju:
        juju.wait_timeout = 60 * 60
        yield juju
        if request.session.testsfailed:
            log = juju.debug_log(limit=3000)
            print(log, end="")


@pytest.fixture(scope="module")
def base(request: pytest.FixtureRequest) -> str:
    """Get the base to deploy the Slurm charms on."""
    return request.config.getoption("--charm-base")


@pytest.fixture(scope="module")
def filesystem_client(request: pytest.FixtureRequest) -> Path | str:
    """Get filesystem-client charm to use for integration tests.

    If the `LOCAL_FILESYSTEM_CLIENT` environment variable is not set,
    this will pull the charm from Charmhub instead.

    Returns:
        `Path` if "filesystem-client" is built locally. `str` otherwise.
    """
    if not LOCAL_FILESYSTEM_CLIENT:
        logger.info("pulling `filesystem-client` charm from charmhub")
        return "filesystem-client"

    logger.info("using local `filesystem-client` charm located at %s", LOCAL_FILESYSTEM_CLIENT)
    return LOCAL_FILESYSTEM_CLIENT


@pytest.fixture(scope="module")
def nfs_server_proxy(request: pytest.FixtureRequest) -> Path | str:
    """Get nfs-server-proxy charm to use for integration tests.

    If the `LOCAL_NFS_SERVER_PROXY` environment variable is not set,
    this will pull the charm from Charmhub instead.

    Returns:
        `Path` if "nfs-server-proxy" is built locally. `str` otherwise.
    """
    if not LOCAL_NFS_SERVER_PROXY:
        logger.info("pulling `nfs-server-proxy` charm from charmhub")
        return "nfs-server-proxy"

    logger.info("using local `nfs-server-proxy` charm located at %s", LOCAL_NFS_SERVER_PROXY)
    return LOCAL_NFS_SERVER_PROXY


@pytest.fixture(scope="module")
def cephfs_server_proxy(request: pytest.FixtureRequest) -> Path | str:
    """Get cephfs-server-proxy charm to use for integration tests.

    If the `LOCAL_CEPHFS_SERVER_PROXY` environment variable is not set,
    this will pull the charm from Charmhub instead.

    Returns:
        `Path` if "cephfs-server-proxy" is built locally. `str` otherwise.
    """
    if not LOCAL_CEPHFS_SERVER_PROXY:
        logger.info("pulling `cephfs-server-proxy` charm from charmhub")
        return "cephfs-server-proxy"

    logger.info(
        "using local `cephfs-server-proxy` charm located at %s",
        LOCAL_CEPHFS_SERVER_PROXY,
    )
    return LOCAL_CEPHFS_SERVER_PROXY


@pytest.fixture(scope="module")
def test_mount_client() -> Path:
    """Get test-mount-client charm to use for integration tests.

    The `LOCAL_TEST_MOUNT_CLIENT` environment variable must be set to
    the path of the locally-built test-mount-client charm.

    Returns:
        `Path` to the local test-mount-client charm.
    """
    path = os.getenv("LOCAL_TEST_MOUNT_CLIENT")
    if not path:
        raise RuntimeError("TEST_MOUNT_CLIENT_DIR environment variable must be set")

    logger.info("using local `test-mount-client` charm located at %s", path)
    return Path(path)
