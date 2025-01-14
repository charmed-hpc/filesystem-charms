#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
import logging

import juju
import pytest
from helpers import bootstrap_microceph, bootstrap_nfs_server
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

FILESYSTEM_CLIENT = "filesystem-client"
NFS_SERVER_PROXY = "nfs-server-proxy"
CEPHFS_SERVER_PROXY = "cephfs-server-proxy"
CHARMS = [FILESYSTEM_CLIENT, NFS_SERVER_PROXY, CEPHFS_SERVER_PROXY]


@pytest.mark.abort_on_fail
@pytest.mark.order(1)
async def test_build_and_deploy(
    ops_test: OpsTest,
    charm_base: str,
    filesystem_client_charm,
    nfs_server_proxy_charm,
    cephfs_server_proxy_charm,
) -> None:
    """Build the charm-under-test and deploy it together with related charms.

    Assert on the unit status before any relations/configurations take place.
    """
    nfs_info = bootstrap_nfs_server()
    cephfs_info = bootstrap_microceph()
    logger.info(f"Deploying {', '.join(CHARMS)}")

    # Pack charms.
    filesystem_client, nfs_server_proxy, cephfs_server_proxy = await asyncio.gather(
        filesystem_client_charm, nfs_server_proxy_charm, cephfs_server_proxy_charm
    )

    # Deploy the charm and wait for active/idle status
    async with asyncio.TaskGroup() as tg:
        tg.create_task(
            ops_test.model.deploy(
                "ubuntu",
                application_name="ubuntu",
                base="ubuntu@24.04",
                constraints=juju.constraints.parse("virt-type=virtual-machine"),
            )
        )
        tg.create_task(
            ops_test.model.deploy(
                str(filesystem_client),
                application_name=FILESYSTEM_CLIENT,
                num_units=0,
            )
        )
        tg.create_task(
            ops_test.model.deploy(
                str(nfs_server_proxy),
                application_name=NFS_SERVER_PROXY,
                config={"hostname": nfs_info.hostname, "path": nfs_info.path},
            )
        )
        tg.create_task(
            ops_test.model.deploy(
                str(cephfs_server_proxy),
                application_name=CEPHFS_SERVER_PROXY,
                config={
                    "fsid": cephfs_info.fsid,
                    "sharepoint": f"{cephfs_info.name}:{cephfs_info.path}",
                    "monitor-hosts": " ".join(cephfs_info.monitor_hosts),
                    "auth-info": f"{cephfs_info.user}:{cephfs_info.key}",
                },
            )
        )
        tg.create_task(
            ops_test.model.wait_for_idle(
                apps=[NFS_SERVER_PROXY, CEPHFS_SERVER_PROXY, "ubuntu"],
                status="active",
                raise_on_blocked=True,
                raise_on_error=True,
                timeout=1000,
            )
        )


@pytest.mark.abort_on_fail
@pytest.mark.order(2)
async def test_integrate(ops_test: OpsTest) -> None:
    async with asyncio.TaskGroup() as tg:
        tg.create_task(
            ops_test.model.integrate(f"{FILESYSTEM_CLIENT}:juju-info", "ubuntu:juju-info")
        )
        tg.create_task(
            ops_test.model.wait_for_idle(apps=["ubuntu"], status="active", raise_on_error=True)
        )
        tg.create_task(
            ops_test.model.wait_for_idle(
                apps=[FILESYSTEM_CLIENT], status="blocked", raise_on_error=True
            )
        )

    assert (
        ops_test.model.applications[FILESYSTEM_CLIENT].units[0].workload_status_message
        == "Missing `mountpoint` in config."
    )


@pytest.mark.abort_on_fail
@pytest.mark.order(3)
async def test_nfs(ops_test: OpsTest) -> None:
    async with asyncio.TaskGroup() as tg:
        tg.create_task(
            ops_test.model.integrate(
                f"{FILESYSTEM_CLIENT}:filesystem", f"{NFS_SERVER_PROXY}:filesystem"
            )
        )
        tg.create_task(
            ops_test.model.applications[FILESYSTEM_CLIENT].set_config(
                {"mountpoint": "/nfs", "nodev": "true", "read-only": "true"}
            )
        )
        tg.create_task(
            ops_test.model.wait_for_idle(
                apps=[FILESYSTEM_CLIENT], status="active", raise_on_error=True
            )
        )

    unit = ops_test.model.applications["ubuntu"].units[0]
    result = (await unit.ssh("ls /nfs")).strip("\n")
    assert "test-1" in result
    assert "test-2" in result
    assert "test-3" in result


@pytest.mark.abort_on_fail
@pytest.mark.order(4)
async def test_cephfs(ops_test: OpsTest) -> None:
    # Ensure the relation is removed before the config changes.
    # This guarantees that the new mountpoint is fresh.
    async with asyncio.TaskGroup() as tg:
        tg.create_task(
            ops_test.model.applications[FILESYSTEM_CLIENT].remove_relation(
                "filesystem", f"{NFS_SERVER_PROXY}:filesystem"
            )
        )
        tg.create_task(
            ops_test.model.wait_for_idle(
                apps=[FILESYSTEM_CLIENT], status="blocked", raise_on_error=True
            )
        )

    async with asyncio.TaskGroup() as tg:
        tg.create_task(
            ops_test.model.applications[FILESYSTEM_CLIENT].set_config(
                {"mountpoint": "/cephfs", "noexec": "true", "nosuid": "true", "nodev": "false"}
            )
        )
        tg.create_task(
            ops_test.model.integrate(
                f"{FILESYSTEM_CLIENT}:filesystem", f"{CEPHFS_SERVER_PROXY}:filesystem"
            )
        )
        tg.create_task(
            ops_test.model.wait_for_idle(
                apps=[FILESYSTEM_CLIENT], status="active", raise_on_error=True
            )
        )

    unit = ops_test.model.applications["ubuntu"].units[0]
    result = (await unit.ssh("ls /cephfs")).strip("\n")
    assert "test-1" in result
    assert "test-2" in result
    assert "test-3" in result
