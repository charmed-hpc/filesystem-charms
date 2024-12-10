#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
import logging
from pathlib import Path

import pytest
import yaml
from charms.filesystem_client.v0.interfaces import CephfsInfo, NfsInfo
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())
APP_NAME = METADATA["name"]

NFS_INFO = NfsInfo(hostname="192.168.1.254", path="/srv", port=65535)
CEPHFS_INFO = CephfsInfo(
    fsid="123456789-0abc-defg-hijk-lmnopqrstuvw",
    name="filesystem",
    path="/export",
    monitor_hosts=[
        "192.168.1.1:6789",
        "192.168.1.2:6789",
        "192.168.1.3:6789",
    ],
    user="user",
    key="R//appdqz4NP4Bxcc5XWrg==",
)


@pytest.mark.abort_on_fail
@pytest.mark.order(1)
async def test_build_and_deploy(ops_test: OpsTest):
    """Build the charm-under-test and deploy it together with related charms.

    Assert on the unit status before any relations/configurations take place.
    """
    # Build and deploy charm from local source folder
    charm = await ops_test.build_charm(".")
    server = await ops_test.build_charm("./tests/integration/server")

    # Deploy the charm and wait for active/idle status
    await asyncio.gather(
        ops_test.model.deploy("ubuntu", application_name="ubuntu", base="ubuntu@24.04"),
        ops_test.model.deploy(
            charm,
            application_name=APP_NAME,
            num_units=0,
            config={
                "mountinfo": """
            {
                "nfs": {
                    "mountpoint": "/nfs",
                    "nodev": true,
                    "read-only": true
                },
                "cephfs": {
                    "mountpoint": "/cephfs",
                    "noexec": true,
                    "nosuid": true,
                    "nodev": false
                }
            }
            """
            },
        ),
        ops_test.model.deploy(
            server,
            application_name="nfs-server",
            config={
                "type": "nfs",
            },
        ),
        ops_test.model.deploy(
            server,
            application_name="cephfs-server",
            config={
                "type": "cephfs",
            },
        ),
        ops_test.model.wait_for_idle(
            apps=["nfs-server", "cephfs-server", "ubuntu"],
            status="active",
            raise_on_blocked=True,
            timeout=1000,
        ),
    )


@pytest.mark.abort_on_fail
@pytest.mark.order(2)
async def test_integrate(ops_test: OpsTest):
    await ops_test.model.integrate(f"{APP_NAME}:juju-info", "ubuntu:juju-info")
    await ops_test.model.integrate(f"{APP_NAME}:fs-share", "nfs-server:fs-share")
    await ops_test.model.integrate(f"{APP_NAME}:fs-share", "cephfs-server:fs-share")

    await ops_test.model.wait_for_idle(
        apps=[APP_NAME, "ubuntu", "nfs-server", "cephfs-server"], status="active", timeout=1000
    )


async def check_autofs(
    ops_test: OpsTest,
    master_file: str,
    autofs_file: str,
    mountpoint: str,
    opts: [str],
    endpoint: str,
):
    unit = ops_test.model.applications["ubuntu"].units[0]
    master = (await unit.ssh(f"cat {master_file}")).split()
    mount = (await unit.ssh(f"cat {autofs_file}")).split()

    assert autofs_file == master[1]
    assert mountpoint == mount[0]
    assert set(opts) == set(mount[1].removeprefix("-").split(","))
    assert endpoint == mount[2]


@pytest.mark.order(3)
async def test_nfs_files(ops_test: OpsTest):
    await check_autofs(
        ops_test=ops_test,
        master_file="/etc/auto.master.d/nfs.autofs",
        autofs_file="/etc/auto.nfs",
        mountpoint="/nfs",
        opts=["exec", "suid", "nodev", "ro", f"port={NFS_INFO.port}"],
        endpoint=f"{NFS_INFO.hostname}:{NFS_INFO.path}",
    )


@pytest.mark.order(4)
async def test_cephfs_files(ops_test: OpsTest):
    await check_autofs(
        ops_test=ops_test,
        master_file="/etc/auto.master.d/cephfs.autofs",
        autofs_file="/etc/auto.cephfs",
        mountpoint="/cephfs",
        opts=[
            "noexec",
            "nosuid",
            "dev",
            "rw",
            "fstype=ceph",
            f"mon_addr={"/".join(CEPHFS_INFO.monitor_hosts)}",
            f"secret={CEPHFS_INFO.key}",
        ],
        endpoint=f"{CEPHFS_INFO.user}@{CEPHFS_INFO.fsid}.{CEPHFS_INFO.name}={CEPHFS_INFO.path}",
    )
