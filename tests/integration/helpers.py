# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Helpers for integration tests."""

import json
import logging
import textwrap
from typing import Any

from pylxd import Client
from tenacity import retry, stop_after_attempt, wait_exponential

from charms.filesystem_client.v0.filesystem_info import CephfsInfo, NfsInfo

_logger = logging.getLogger(__name__)

CEPH_FS_NAME = "cephfs"
CEPH_USERNAME = "fs-client"
CEPH_PATH = "/"


def _exec_command(instance: Any, cmd: [str]) -> str:
    _logger.info(f"Executing `{' '.join(cmd)}`")
    code, stdout, stderr = instance.execute(cmd, environment={"DEBIAN_FRONTEND": "noninteractive"})
    if code != 0:
        _logger.error(stderr)
        raise Exception(f"Failed to execute command `{' '.join(cmd)}` in instance")
    _logger.info(stdout)
    return stdout


def _exec_commands(instance: Any, cmds: [[str]]) -> None:
    for cmd in cmds:
        _exec_command(instance, cmd)


def bootstrap_nfs_server() -> NfsInfo:
    """Bootstrap a minimal NFS kernel server in LXD.

    Returns:
        NfsInfo: Information to mount the NFS share.
    """
    client = Client()

    if client.instances.exists("nfs-server"):
        _logger.info("NFS server already exists")
        instance = client.instances.get("nfs-server")
        address = instance.state().network["enp5s0"]["addresses"][0]["address"]
        _logger.info(f"NFS share endpoint is nfs://{address}/data")
        return NfsInfo(hostname=address, port=None, path="/data")

    _logger.info("Bootstrapping minimal NFS kernel server")
    config = {
        "name": "nfs-server",
        "source": {
            "alias": "noble/amd64",
            "mode": "pull",
            "protocol": "simplestreams",
            "server": "https://cloud-images.ubuntu.com/releases",
            "type": "image",
        },
        "type": "virtual-machine",
    }
    client.instances.create(config, wait=True)
    instance = client.instances.get(config["name"])
    instance.start(wait=True)
    _logger.info("Installing NFS server inside LXD container")

    _wait_for_machine(instance)

    _exec_commands(
        instance,
        [
            ["apt", "update", "-y"],
            ["apt", "upgrade", "-y"],
        ],
    )

    # Restart in case there was a kernel update.
    instance.restart(wait=True)
    _wait_for_machine(instance)

    _exec_command(instance, ["apt", "-y", "install", "nfs-kernel-server"])

    exports = textwrap.dedent(
        """
        /srv     *(ro,sync,subtree_check)
        /data    *(rw,sync,no_subtree_check,no_root_squash)
        """
    ).strip("\n")
    _logger.info(f"Uploading the following /etc/exports file:\n{exports}")
    instance.files.put("/etc/exports", exports)
    _logger.info("Starting NFS server")
    _exec_commands(
        instance,
        [
            ["mkdir", "-p", "/data"],
            ["exportfs", "-a"],
            ["systemctl", "restart", "nfs-kernel-server"],
        ],
    )
    for i in [1, 2, 3]:
        _exec_command(instance, ["touch", f"/data/test-{i}"])
    address = instance.state().network["enp5s0"]["addresses"][0]["address"]
    _logger.info(f"NFS share endpoint is nfs://{address}/data")
    return NfsInfo(hostname=address, port=None, path="/data")


def bootstrap_microceph() -> CephfsInfo:
    """Bootstrap a minimal Microceph cluster in LXD.

    Returns:
        CephfsInfo: Information to mount the CephFS share.
    """
    client = Client()
    if client.instances.exists("microceph"):
        _logger.info("Microceph server already exists")
        instance = client.instances.get("microceph")
        return _get_cephfs_info(instance)

    _logger.info("Bootstrapping Microceph cluster")
    config = {
        "name": "microceph",
        "source": {
            "alias": "jammy/amd64",
            "mode": "pull",
            "protocol": "simplestreams",
            "server": "https://cloud-images.ubuntu.com/releases",
            "type": "image",
        },
        "type": "virtual-machine",
    }
    client.instances.create(config, wait=True)
    instance = client.instances.get(config["name"])
    instance.start(wait=True)
    _logger.info("Installing Microceph inside LXD container")

    _wait_for_machine(instance)

    _exec_commands(
        instance,
        [
            ["ln", "-s", "/bin/true"],
            ["apt", "update", "-y"],
            ["apt", "upgrade", "-y"],
            ["apt", "install", "-y", "ceph-common"],
        ],
    )

    # Restart in case there was a kernel update.
    instance.restart(wait=True)
    _wait_for_machine(instance)

    _exec_commands(
        instance,
        [
            ["snap", "install", "microceph"],
            ["microceph", "cluster", "bootstrap"],
            ["microceph", "disk", "add", "loop,1G,3"],
            ["microceph.ceph", "osd", "pool", "create", f"{CEPH_FS_NAME}_data"],
            ["microceph.ceph", "osd", "pool", "create", f"{CEPH_FS_NAME}_metadata"],
            [
                "microceph.ceph",
                "fs",
                "new",
                CEPH_FS_NAME,
                f"{CEPH_FS_NAME}_metadata",
                f"{CEPH_FS_NAME}_data",
            ],
            [
                "microceph.ceph",
                "fs",
                "authorize",
                CEPH_FS_NAME,
                f"client.{CEPH_USERNAME}",
                CEPH_PATH,
                "rw",
            ],
            # Need to generate the test files inside microceph itself.
            [
                "ln",
                "-sf",
                "/var/snap/microceph/current/conf/ceph.client.admin.keyring",
                "/etc/ceph/ceph.client.admin.keyring",
            ],
            [
                "ln",
                "-sf",
                "/var/snap/microceph/current/conf/ceph.keyring",
                "/etc/ceph/ceph.keyring",
            ],
            ["ln", "-sf", "/var/snap/microceph/current/conf/ceph.conf", "/etc/ceph/ceph.conf"],
        ],
    )

    _mount_cephfs(instance)

    for i in [1, 2, 3]:
        _exec_command(instance, ["touch", f"/mnt/test-{i}"])

    return _get_cephfs_info(instance)


@retry(wait=wait_exponential(max=6), stop=stop_after_attempt(10))
def _wait_for_machine(instance) -> None:
    # Need to extract this into its own function to apply the tenacity decorator
    _exec_command(instance, ["echo"])
    _exec_command(instance, ["systemctl", "is-active", "snapd", "--quiet"])


@retry(wait=wait_exponential(max=6), stop=stop_after_attempt(20))
def _mount_cephfs(instance) -> None:
    # Need to extract this into its own function to apply the tenacity decorator
    # Wait until the cluster is ready to mount the filesystem.
    status = json.loads(_exec_command(instance, ["microceph.ceph", "-s", "-f", "json"]))
    if status["health"]["status"] != "HEALTH_OK":
        raise Exception("CephFS is not available")

    _exec_command(instance, ["mount", "-t", "ceph", f"admin@.{CEPH_FS_NAME}={CEPH_PATH}", "/mnt"])


def _get_cephfs_info(instance: Any) -> CephfsInfo:
    status = json.loads(_exec_command(instance, ["microceph.ceph", "-s", "-f", "json"]))
    fsid = status["fsid"]
    host = instance.state().network["enp5s0"]["addresses"][0]["address"] + ":6789"
    key = _exec_command(
        instance, ["microceph.ceph", "auth", "print-key", f"client.{CEPH_USERNAME}"]
    )
    return CephfsInfo(
        fsid=fsid,
        name=CEPH_FS_NAME,
        path=CEPH_PATH,
        monitor_hosts=[host],
        user=CEPH_USERNAME,
        key=key,
    )
