# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Helpers for integration tests."""

import json
import logging
import textwrap
from collections.abc import Awaitable
from pathlib import Path

import juju
from juju import machine
from pytest_operator.plugin import OpsTest
from tenacity import retry, stop_after_attempt, wait_exponential

from charms.filesystem_client.v0.filesystem_info import CephfsInfo, NfsInfo

_logger = logging.getLogger(__name__)

CEPH_FS_NAME = "cephfs"
CEPH_USERNAME = "fs-client"
CEPH_PATH = "/"


async def _exec_cmd(machine: machine.Machine, cmd: str) -> str:
    _logger.info("Executing `%s`", cmd)
    stdout = await machine.ssh(f"sudo bash -c '{cmd.replace("'", "'\\''")}'", wait_for_active=True)
    if stdout:
        _logger.info("stdout: %s", stdout)

    return stdout


async def _exec_cmds(machine: machine.Machine, cmds: [str]) -> None:
    for cmd in cmds:
        await _exec_cmd(machine, cmd)


async def build_and_deploy_charm(
    ops_test: OpsTest, charm: Awaitable[str | Path], *deploy_args, **deploy_kwargs
):
    """Build and deploy the charm identified by `charm`."""
    charm = await charm
    deploy_kwargs["channel"] = "edge" if isinstance(charm, str) else None
    await ops_test.model.deploy(str(charm), *deploy_args, **deploy_kwargs)


async def bootstrap_nfs_server(ops_test: OpsTest) -> NfsInfo:
    """Bootstrap a minimal NFS kernel server in Juju.

    Returns:
        NfsInfo: Information to mount the NFS share.
    """
    await ops_test.model.deploy(
        "ubuntu",
        application_name="nfs-server",
        base="ubuntu@24.04",
        constraints=juju.constraints.parse("virt-type=virtual-machine"),
    )
    await ops_test.model.wait_for_idle(
        apps=["nfs-server"],
        status="active",
        timeout=1000,
    )

    machine = ops_test.model.applications["nfs-server"].units[0].machine

    await _exec_cmd(machine, "apt -y install nfs-kernel-server")

    exports = textwrap.dedent(
        """
        /srv     *(ro,sync,subtree_check)
        /data    *(rw,sync,no_subtree_check,no_root_squash)
        """
    ).strip("\n")
    _logger.info(f"Uploading the following /etc/exports file:\n{exports}")
    await _exec_cmd(machine, f'echo -e "{exports.replace("\n", "\\n")}" > /etc/exports')
    _logger.info("Starting NFS server")
    await _exec_cmds(
        machine,
        [
            "mkdir -p /data",
            "exportfs -a",
            "systemctl restart nfs-kernel-server",
        ],
    )
    for i in [1, 2, 3]:
        await _exec_cmd(machine, f"touch /data/test-{i}")
    address = (await _exec_cmd(machine, "hostname")).strip()
    _logger.info(f"NFS share endpoint is nfs://{address}/data")
    return NfsInfo(hostname=address, port=None, path="/data")


async def bootstrap_microceph(ops_test: OpsTest) -> CephfsInfo:
    """Bootstrap a minimal Microceph cluster in Juju.

    Returns:
        CephfsInfo: Information to mount the CephFS share.
    """
    _logger.info("Bootstrapping Microceph cluster")

    await ops_test.model.deploy(
        "microceph",
        application_name="microceph",
        base="ubuntu@24.04",
        channel="squid/beta",
        constraints=juju.constraints.parse("mem=4G root-disk=20G virt-type=virtual-machine"),
        num_units=1,
        storage={"osd-standalone": juju.constraints.parse_storage_constraint("loop,3,1G")},
    )
    await ops_test.model.wait_for_idle(
        apps=["microceph"],
        status="active",
        timeout=1000,
    )

    machine = ops_test.model.applications["microceph"].units[0].machine

    await _wait_for_ceph(machine)

    await _exec_cmds(
        machine,
        [
            "ln -s /bin/true",
            "apt install -y ceph-common",
            f"microceph.ceph osd pool create {CEPH_FS_NAME}_data",
            f"microceph.ceph osd pool create {CEPH_FS_NAME}_metadata",
            f"microceph.ceph fs new {CEPH_FS_NAME} {CEPH_FS_NAME}_metadata {CEPH_FS_NAME}_data",
            f"microceph.ceph fs authorize {CEPH_FS_NAME} client.{CEPH_USERNAME} {CEPH_PATH} rw",
            "ln -sf /var/snap/microceph/current/conf/ceph.client.admin.keyring /etc/ceph/ceph.client.admin.keyring",
            "ln -sf /var/snap/microceph/current/conf/ceph.keyring /etc/ceph/ceph.keyring",
            "ln -sf /var/snap/microceph/current/conf/ceph.conf /etc/ceph/ceph.conf",
        ],
    )

    await _wait_for_ceph(machine)
    await _exec_cmd(machine, f"mount -t ceph admin@.{CEPH_FS_NAME}={CEPH_PATH} /mnt")

    for i in [1, 2, 3]:
        await _exec_cmd(machine, f"touch /mnt/test-{i}")

    return await _get_cephfs_info(machine)


@retry(wait=wait_exponential(max=10), stop=stop_after_attempt(20))
async def _wait_for_ceph(machine: machine.Machine) -> None:
    # Wait until the cluster is ready to mount the filesystem.
    status = json.loads(await _exec_cmd(machine, "microceph.ceph -s -f json"))
    if status["health"]["status"] != "HEALTH_OK":
        raise Exception("CephFS is not available")


async def _get_cephfs_info(machine: machine.Machine) -> CephfsInfo:
    status = json.loads(await _exec_cmd(machine, "microceph.ceph -s -f json"))
    fsid = status["fsid"]
    host = (await _exec_cmd(machine, "hostname")).strip() + ":6789"
    key = await _exec_cmd(machine, f"microceph.ceph auth print-key client.{CEPH_USERNAME}")

    return CephfsInfo(
        fsid=fsid,
        name=CEPH_FS_NAME,
        path=CEPH_PATH,
        monitor_hosts=[host],
        user=CEPH_USERNAME,
        key=key,
    )
