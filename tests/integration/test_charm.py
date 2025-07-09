#!/usr/bin/env python3
# Copyright 2024-2025 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
import logging
from collections.abc import Awaitable
from pathlib import Path

import juju
import pytest
from helpers import bootstrap_microceph, bootstrap_nfs_server
from juju.unit import Unit
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

FILESYSTEM_CLIENT = "filesystem-client"
MOUNT_PROVIDER = "mount-provider"
NFS_SERVER_PROXY = "nfs-server-proxy"
CEPHFS_SERVER_PROXY = "cephfs-server-proxy"
MOUNT_REQUIRERS = ["srv", "shared"]
CHARMS = [
    FILESYSTEM_CLIENT,
    NFS_SERVER_PROXY,
    CEPHFS_SERVER_PROXY,
    MOUNT_PROVIDER,
] + MOUNT_REQUIRERS


@pytest.mark.abort_on_fail
@pytest.mark.order(1)
async def test_build_and_deploy(  # noqa: C901
    ops_test: OpsTest,
    charm_base: str,
    filesystem_client_charm: Awaitable[str | Path],
    nfs_server_proxy_charm: Awaitable[str | Path],
    cephfs_server_proxy_charm: Awaitable[str | Path],
    test_mount_client_charm: Awaitable[Path],
) -> None:
    """Build the charm-under-test and deploy it together with related charms.

    Assert on the unit status before any relations/configurations take place.
    """
    logger.info(f"Deploying {', '.join(CHARMS)}")

    async def create_machine(
        constraints: juju.constraints.ConstraintsDict,
    ) -> juju.machine.Machine:
        machine: juju.machine.Machine = await ops_test.model.add_machine(
            constraints=constraints,
            series="noble",
        )
        while machine.status != "running":
            await asyncio.sleep(3)
        return machine

    storage_machine, mounts_machine = await asyncio.gather(
        create_machine(
            constraints=juju.constraints.parse("mem=4G root-disk=20G virt-type=virtual-machine")
        ),
        create_machine(constraints=juju.constraints.parse("virt-type=virtual-machine")),
    )

    # Deploy the charms and wait for active/idle status
    async with asyncio.TaskGroup() as tg:
        tg.create_task(
            ops_test.model.deploy(
                "ubuntu",
                application_name="ubuntu",
                base="ubuntu@24.04",
                to=mounts_machine.entity_id,
            )
        )

        async def deploy_filesystem_clients():
            filesystem_client = await filesystem_client_charm

            for app in [FILESYSTEM_CLIENT, MOUNT_PROVIDER]:
                tg.create_task(
                    ops_test.model.deploy(
                        str(filesystem_client),
                        application_name=app,
                        channel="edge" if isinstance(filesystem_client, str) else None,
                        num_units=0,
                    )
                )

        tg.create_task(deploy_filesystem_clients())

        async def deploy_nfs_proxy():
            """Deploy an NFS server and pass its info to a new `nfs_server_proxy_charm`."""

            async def deploy_charm():
                nfs_server_proxy = await nfs_server_proxy_charm
                (
                    await ops_test.model.deploy(
                        str(nfs_server_proxy),
                        base=charm_base,
                        application_name=NFS_SERVER_PROXY,
                        to=storage_machine.entity_id,
                    ),
                )

            nfs_info, _ = await asyncio.gather(
                bootstrap_nfs_server(ops_test, storage_machine.entity_id), deploy_charm()
            )

            await ops_test.model.applications[NFS_SERVER_PROXY].set_config(
                {"hostname": nfs_info.hostname, "path": nfs_info.path}
            )

        tg.create_task(deploy_nfs_proxy())

        async def deploy_cephfs_proxy():
            """Deploy a MicroCeph cluster and pass its info to a new `cephfs_server_proxy_charm`."""

            async def deploy_charm():
                cephfs_server_proxy = await cephfs_server_proxy_charm
                await ops_test.model.deploy(
                    str(cephfs_server_proxy),
                    base=charm_base,
                    application_name=CEPHFS_SERVER_PROXY,
                    to=storage_machine.entity_id,
                )

            cephfs_info, _ = await asyncio.gather(
                bootstrap_microceph(ops_test, storage_machine.entity_id),
                deploy_charm(),
            )

            await ops_test.model.applications[CEPHFS_SERVER_PROXY].set_config(
                {
                    "fsid": cephfs_info.fsid,
                    "sharepoint": f"{cephfs_info.name}:{cephfs_info.path}",
                    "monitor-hosts": " ".join(cephfs_info.monitor_hosts),
                    "auth-info": f"{cephfs_info.user}:{cephfs_info.key}",
                }
            )

        tg.create_task(deploy_cephfs_proxy())

        tg.create_task(
            ops_test.model.wait_for_idle(
                apps=[NFS_SERVER_PROXY, CEPHFS_SERVER_PROXY, "ubuntu"],
                status="active",
                # We cannot throw on blocked because the proxy charms could be deployed before their "bootstrappers",
                # and since bootstrapping returns the required config for the proxies, they could block until
                # bootstrapping finishes and their config is set.
                raise_on_blocked=False,
                raise_on_error=True,
                timeout=5000,
            )
        )

        async def deploy_test_mount_clients():
            """Deploy the test mount clients."""
            test_mount_client = await test_mount_client_charm

            for app in MOUNT_REQUIRERS:
                tg.create_task(
                    ops_test.model.deploy(
                        str(test_mount_client),
                        base=charm_base,
                        application_name=app,
                        to=mounts_machine.entity_id,
                    )
                )

        tg.create_task(deploy_test_mount_clients())


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
                apps=MOUNT_REQUIRERS, status="blocked", raise_on_error=True
            )
        )
        tg.create_task(
            ops_test.model.wait_for_idle(
                apps=[FILESYSTEM_CLIENT], status="blocked", raise_on_error=True
            )
        )
        tg.create_task(
            ops_test.model.wait_for_idle(
                apps=[MOUNT_PROVIDER], status="waiting", raise_on_error=True
            )
        )
        for app in MOUNT_REQUIRERS:
            tg.create_task(ops_test.model.integrate(f"{MOUNT_PROVIDER}:mount", f"{app}:mount"))

    for unit in ops_test.model.applications[FILESYSTEM_CLIENT].units:
        assert unit.workload_status_message == "Missing `mountpoint` config or `mount` integration"

    for unit in ops_test.model.applications[MOUNT_PROVIDER].units:
        assert unit.workload_status_message == "Waiting for mountpoint from `mount` integration"


async def check_files(unit: Unit, path: str) -> None:
    result = (await unit.ssh(f"ls {path}")).strip("\n")
    assert "test-1" in result
    assert "test-2" in result
    assert "test-3" in result


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
            ops_test.model.integrate(
                f"{MOUNT_PROVIDER}:filesystem", f"{NFS_SERVER_PROXY}:filesystem"
            )
        )
        tg.create_task(
            ops_test.model.applications[FILESYSTEM_CLIENT].set_config(
                {"mountpoint": "/nfs", "nodev": "true", "read-only": "true"}
            )
        )
        tg.create_task(
            ops_test.model.wait_for_idle(
                apps=[FILESYSTEM_CLIENT] + MOUNT_REQUIRERS, status="active", raise_on_error=True
            )
        )
        for app in MOUNT_REQUIRERS:
            tg.create_task(ops_test.model.applications[app].set_config({"mountpoint": f"/{app}"}))

    async with asyncio.TaskGroup() as tg:
        tg.create_task(check_files(ops_test.model.applications["ubuntu"].units[0], "/nfs"))
        for app in MOUNT_REQUIRERS:
            tg.create_task(check_files(ops_test.model.applications[app].units[0], f"/{app}"))


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
            ops_test.model.applications[MOUNT_PROVIDER].remove_relation(
                "filesystem", f"{NFS_SERVER_PROXY}:filesystem"
            )
        )
        tg.create_task(
            ops_test.model.wait_for_idle(
                apps=[FILESYSTEM_CLIENT, MOUNT_PROVIDER], status="blocked", raise_on_error=True
            )
        )

    async with asyncio.TaskGroup() as tg:
        tg.create_task(
            ops_test.model.applications[FILESYSTEM_CLIENT].set_config(
                {
                    "mountpoint": "/cephfs",
                    "noexec": "true",
                    "nosuid": "true",
                    "nodev": "false",
                }
            )
        )
        tg.create_task(
            ops_test.model.integrate(
                f"{FILESYSTEM_CLIENT}:filesystem", f"{CEPHFS_SERVER_PROXY}:filesystem"
            )
        )
        tg.create_task(
            ops_test.model.integrate(
                f"{MOUNT_PROVIDER}:filesystem", f"{CEPHFS_SERVER_PROXY}:filesystem"
            )
        )
        tg.create_task(
            ops_test.model.wait_for_idle(
                apps=[FILESYSTEM_CLIENT, MOUNT_PROVIDER], status="active", raise_on_error=True
            )
        )

    async with asyncio.TaskGroup() as tg:
        tg.create_task(check_files(ops_test.model.applications["ubuntu"].units[0], "/cephfs"))
        for app in MOUNT_REQUIRERS:
            tg.create_task(check_files(ops_test.model.applications[app].units[0], f"/{app}"))
