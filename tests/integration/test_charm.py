#!/usr/bin/env python3
# Copyright 2024-2025 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import jubilant
import pytest
from conftest import Machines
from constants import (
    CEPHFS_SERVER_PROXY,
    CHARMS,
    FILESYSTEM_CLIENT,
    MOUNT_PROVIDER,
    MOUNT_REQUIRERS,
    NFS_SERVER_PROXY,
)
from helpers import (
    bootstrap_microceph,
    bootstrap_nfs_server,
    charm_channel,
    check_files,
)

logger = logging.getLogger(__name__)


@pytest.mark.order(1)
def test_deploy(
    juju: jubilant.Juju,
    base: str,
    filesystem_client: str | Path,
    nfs_server_proxy: str | Path,
    cephfs_server_proxy: str | Path,
    test_mount_client: Path,
    machines: Machines,
) -> None:
    """Build the charm-under-test and deploy it together with related charms.

    Assert on the unit status before any relations/configurations take place.
    """
    logger.info(f"Deploying {', '.join(CHARMS)}")

    # Deploy the ubuntu charm onto the mounts machine.
    juju.deploy(
        "ubuntu",
        "ubuntu",
        base=base,
        to=machines.mounts_machine_id,
    )

    # Deploy filesystem-client and mount-provider as subordinates (0 units).
    for app in [FILESYSTEM_CLIENT, MOUNT_PROVIDER]:
        juju.deploy(
            str(filesystem_client),
            app,
            channel=charm_channel(filesystem_client),
        )

    # Deploy the NFS and CephFS server proxies onto the storage machines.
    juju.deploy(
        str(nfs_server_proxy),
        NFS_SERVER_PROXY,
        base=base,
        channel=charm_channel(nfs_server_proxy),
        to=machines.storage_machine_id,
    )
    juju.deploy(
        str(cephfs_server_proxy),
        CEPHFS_SERVER_PROXY,
        base=base,
        channel=charm_channel(cephfs_server_proxy),
        to=machines.storage_machine_id,
    )

    # Deploy the test mount client charms onto the mounts machine.
    for app in MOUNT_REQUIRERS:
        juju.deploy(str(test_mount_client), app, base=base, to=machines.mounts_machine_id)

    # Bootstrap the NFS server and MicroCeph cluster concurrently.
    with ThreadPoolExecutor(max_workers=2) as pool:
        nfs_future = pool.submit(bootstrap_nfs_server, juju, machines.storage_machine_id, base)
        cephfs_future = pool.submit(bootstrap_microceph, juju, machines.storage_machine_id, base)

    nfs_info = nfs_future.result()
    cephfs_info = cephfs_future.result()

    juju.config(
        NFS_SERVER_PROXY,
        values={
            "hostname": nfs_info.hostname,
            "path": nfs_info.path,
        },
    )
    juju.config(
        CEPHFS_SERVER_PROXY,
        values={
            "fsid": cephfs_info.fsid,
            "sharepoint": f"{cephfs_info.name}:{cephfs_info.path}",
            "monitor-hosts": " ".join(cephfs_info.monitor_hosts),
            "auth-info": f"{cephfs_info.user}:{cephfs_info.key}",
        },
    )

    # Wait for the proxy charms and ubuntu to become active.
    juju.wait(
        lambda status: jubilant.all_active(
            status, NFS_SERVER_PROXY, CEPHFS_SERVER_PROXY, "ubuntu"
        ),
        error=lambda status: jubilant.any_error(
            status, NFS_SERVER_PROXY, CEPHFS_SERVER_PROXY, "ubuntu"
        ),
    )


@pytest.mark.order(2)
def test_integrate(juju: jubilant.Juju) -> None:
    juju.integrate(f"{FILESYSTEM_CLIENT}:juju-info", "ubuntu:juju-info")
    for app in MOUNT_REQUIRERS:
        juju.integrate(f"{MOUNT_PROVIDER}:mount", f"{app}:mount")

    juju.wait(
        lambda status: (
            jubilant.all_active(status, "ubuntu")
            and jubilant.all_blocked(status, *MOUNT_REQUIRERS, FILESYSTEM_CLIENT)
            and jubilant.all_waiting(status, MOUNT_PROVIDER)
        ),
        error=lambda status: jubilant.any_error(
            status, "ubuntu", *MOUNT_REQUIRERS, FILESYSTEM_CLIENT, MOUNT_PROVIDER
        ),
    )

    status = juju.status()
    for unit in status.apps[FILESYSTEM_CLIENT].units.values():
        assert unit.workload_status.message == "Missing `mountpoint` config or `mount` integration"

    for unit in status.apps[MOUNT_PROVIDER].units.values():
        assert unit.workload_status.message == "Waiting for mountpoint from `mount` integration"


@pytest.mark.order(3)
def test_nfs(juju: jubilant.Juju) -> None:
    juju.integrate(f"{FILESYSTEM_CLIENT}:filesystem", f"{NFS_SERVER_PROXY}:filesystem")
    juju.integrate(f"{MOUNT_PROVIDER}:filesystem", f"{NFS_SERVER_PROXY}:filesystem")
    juju.config(
        FILESYSTEM_CLIENT,
        values={
            "mountpoint": "/nfs",
            "nodev": "true",
            "read-only": "true",
        },
    )
    for app in MOUNT_REQUIRERS:
        juju.config(app, values={"mountpoint": f"/{app}"})

    juju.wait(
        lambda status: jubilant.all_active(status, FILESYSTEM_CLIENT, *MOUNT_REQUIRERS),
        error=lambda status: (
            jubilant.any_error(status, FILESYSTEM_CLIENT, MOUNT_PROVIDER)
            or (
                jubilant.all_agents_idle(status)
                and jubilant.any_blocked(status, FILESYSTEM_CLIENT, MOUNT_PROVIDER)
            )
        ),
    )

    check_files(juju, "ubuntu/0", "/nfs")
    for app in MOUNT_REQUIRERS:
        check_files(juju, f"{app}/0", f"/{app}")

    # Remove NFS relations after testing
    juju.remove_relation(f"{FILESYSTEM_CLIENT}:filesystem", f"{NFS_SERVER_PROXY}:filesystem")
    juju.remove_relation(f"{MOUNT_PROVIDER}:filesystem", f"{NFS_SERVER_PROXY}:filesystem")
    juju.wait(
        lambda status: jubilant.all_blocked(status, FILESYSTEM_CLIENT, MOUNT_PROVIDER),
        error=lambda status: jubilant.any_error(status, FILESYSTEM_CLIENT, MOUNT_PROVIDER),
    )


@pytest.mark.order(4)
def test_cephfs(juju: jubilant.Juju) -> None:

    # Reconfigure and integrate with CephFS.
    juju.config(
        FILESYSTEM_CLIENT,
        values={
            "mountpoint": "/cephfs",
            "noexec": "true",
            "nosuid": "true",
            "nodev": "false",
        },
    )
    juju.integrate(f"{FILESYSTEM_CLIENT}:filesystem", f"{CEPHFS_SERVER_PROXY}:filesystem")
    juju.integrate(f"{MOUNT_PROVIDER}:filesystem", f"{CEPHFS_SERVER_PROXY}:filesystem")
    juju.wait(
        lambda status: jubilant.all_active(status, FILESYSTEM_CLIENT, MOUNT_PROVIDER),
        error=lambda status: (
            jubilant.any_error(status, FILESYSTEM_CLIENT, MOUNT_PROVIDER)
            or (
                jubilant.all_agents_idle(status)
                and jubilant.any_blocked(status, FILESYSTEM_CLIENT, MOUNT_PROVIDER)
            )
        ),
    )

    check_files(juju, "ubuntu/0", "/cephfs")
    for app in MOUNT_REQUIRERS:
        check_files(juju, f"{app}/0", f"/{app}")

    juju.remove_relation(f"{FILESYSTEM_CLIENT}:filesystem", f"{CEPHFS_SERVER_PROXY}:filesystem")
    juju.remove_relation(f"{MOUNT_PROVIDER}:filesystem", f"{CEPHFS_SERVER_PROXY}:filesystem")

    juju.wait(
        lambda status: jubilant.all_blocked(status, FILESYSTEM_CLIENT, MOUNT_PROVIDER),
        error=lambda status: jubilant.any_error(status, FILESYSTEM_CLIENT, MOUNT_PROVIDER),
    )
