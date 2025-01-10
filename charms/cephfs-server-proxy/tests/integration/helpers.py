# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Helpers for cephfs-client integration tests."""

import json
import logging
from typing import Any

from charms.filesystem_client.v0.filesystem_info import CephfsInfo
from pylxd import Client
from tenacity import retry, stop_after_attempt, wait_exponential

_logger = logging.getLogger(__name__)

FS_NAME = "cephfs"
USERNAME = "fs-client"
PATH = "/"


def bootstrap_microceph() -> CephfsInfo:
    """Bootstrap a minimal Microceph cluster in LXD.

    Returns: A tuple with the share info and auth info to mount the CephFS share.
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
    _logger.info("Installing Microceph inside LXD container")

    _wait_for_microceph(instance)

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
    _wait_for_microceph(instance)

    _exec_commands(
        instance,
        [
            ["snap", "install", "microceph"],
            ["microceph", "cluster", "bootstrap"],
            ["microceph", "disk", "add", "loop,2G,3"],
            ["microceph.ceph", "osd", "pool", "create", f"{FS_NAME}_data"],
            ["microceph.ceph", "osd", "pool", "create", f"{FS_NAME}_metadata"],
            ["microceph.ceph", "fs", "new", FS_NAME, f"{FS_NAME}_metadata", f"{FS_NAME}_data"],
            [
                "microceph.ceph",
                "fs",
                "authorize",
                FS_NAME,
                f"client.{USERNAME}",
                PATH,
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
def _wait_for_microceph(instance) -> None:
    # Need to extract this into its own function to apply the tenacity decorator
    _exec_command(instance, ["echo"])
    _exec_command(instance, ["systemctl", "is-active", "snapd", "--quiet"])


@retry(wait=wait_exponential(max=6), stop=stop_after_attempt(10))
def _mount_cephfs(instance) -> None:
    # Need to extract this into its own function to apply the tenacity decorator
    # Wait until the cluster is ready to mount the filesystem.
    status = json.loads(_exec_command(instance, ["microceph.ceph", "-s", "-f", "json"]))
    if status["health"]["status"] != "HEALTH_OK":
        raise Exception("CephFS is not available")

    _exec_command(instance, ["mount", "-t", "ceph", f"admin@.{FS_NAME}={PATH}", "/mnt"])


def _get_cephfs_info(instance: Any) -> CephfsInfo:
    status = json.loads(_exec_command(instance, ["microceph.ceph", "-s", "-f", "json"]))
    fsid = status["fsid"]
    host = instance.state().network["enp5s0"]["addresses"][0]["address"] + ":6789"
    key = _exec_command(instance, ["microceph.ceph", "auth", "print-key", f"client.{USERNAME}"])
    return CephfsInfo(
        fsid=fsid, name=FS_NAME, path=PATH, monitor_hosts=[host], user=USERNAME, key=key
    )


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
