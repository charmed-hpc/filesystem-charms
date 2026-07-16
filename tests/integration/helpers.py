# Copyright 2025-2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Helpers for integration tests."""

import json
import logging
import re
import textwrap
from pathlib import Path

import jubilant
import tenacity
from charms.filesystem_client.v0.filesystem_info import CephfsInfo, LustreInfo, NfsInfo

_logger = logging.getLogger(__name__)

DEFAULT_CHARM_CHANNEL = "latest/edge"

CEPH_FS_NAME = "cephfs"
CEPH_USERNAME = "fs-client"
CEPH_PATH = "/"

LUSTRE_FS_NAME = "lustrefs"


def add_machine(juju: jubilant.Juju, constraints: str, base: str | None = None) -> str:
    """Add a Juju machine with the given constraints and return its ID.

    Blocks until the machine reaches "started" status.
    """
    # `add-machine` writes its output to stderr.
    args = ["add-machine", "--constraints", constraints]
    if base:
        args += ["--base", base]
    _stdout, stderr = juju._cli(*args)
    match = re.search(r"(\d+)", stderr)
    if not match:
        raise RuntimeError(f"Could not parse machine ID from output: {stderr!r}")
    machine_id = match.group(1)

    juju.wait(
        lambda status: (
            machine_id in status.machines
            and status.machines[machine_id].juju_status.current == "started"
        ),
    )
    return machine_id


def charm_channel(charm: str | Path) -> str | None:
    """Return the default channel when deploying from Charmhub, None for local charms."""
    return DEFAULT_CHARM_CHANNEL if isinstance(charm, str) else None


def bootstrap_nfs_server(juju: jubilant.Juju, machine_id: str, base: str) -> NfsInfo:
    """Bootstrap a minimal NFS kernel server in Juju.

    Returns:
        NfsInfo: Information to mount the NFS share.
    """
    juju.deploy("ubuntu", "nfs-server", base=base, to=machine_id)
    juju.wait(
        lambda status: jubilant.all_active(status, "nfs-server"),
        timeout=1000,
    )

    unit = "nfs-server/0"

    juju.exec("sudo apt -y install nfs-kernel-server", unit=unit)

    exports = textwrap.dedent("""
        /data    *(rw,sync,no_subtree_check,no_root_squash)
        """).strip("\n")
    _logger.info("Uploading the following /etc/exports file:\n%s", exports)
    escaped_exports = exports.replace(chr(10), "\\n")
    juju.exec(f"sudo bash -c 'echo -e \"{escaped_exports}\" > /etc/exports'", unit=unit)

    _logger.info("Starting NFS server")
    juju.exec("sudo mkdir -p /data", unit=unit)
    juju.exec("sudo exportfs -a", unit=unit)
    juju.exec("sudo systemctl restart nfs-kernel-server", unit=unit)

    for i in [1, 2, 3]:
        juju.exec(f"sudo touch /data/test-{i}", unit=unit)

    address = juju.exec("hostname", unit=unit).stdout.strip()
    _logger.info("NFS share endpoint is nfs://%s/data", address)
    return NfsInfo(hostname=address, port=None, path="/data")


def bootstrap_lustre_server(
    juju: jubilant.Juju, lustre_server: str | Path, base: str, machine_id: str
) -> LustreInfo:
    """Bootstrap a minimal Lustre server in Juju.

    Returns:
        LustreInfo: Information to mount the Lustre share.
    """
    # 3 units: 1x MGS+MDS, 2x OSS.
    juju.deploy(lustre_server, "lustre-server", base=base, num_units=3, to=[machine_id] * 3)
    juju.wait(
        lambda status: jubilant.all_active(status, "lustre-server"),
        timeout=2000,
    )

    unit = "lustre-server/0"
    host = juju.exec("hostname -I", unit=unit).stdout.strip() + "@tcp"

    juju.exec("mkdir -p /mnt/scratch", unit=unit)
    juju.exec(f"mount -t lustre {host}:/{LUSTRE_FS_NAME} /mnt/scratch", unit=unit)
    for i in [1, 2, 3]:
        juju.exec(f"touch /mnt/scratch/test-{i}", unit=unit)

    _logger.info("Lustre share host is %s with filesystem name %s", host, LUSTRE_FS_NAME)
    return LustreInfo(mgs_ids=[host], fs_name=LUSTRE_FS_NAME)


def bootstrap_microceph(juju: jubilant.Juju, machine_id: str, base: str) -> CephfsInfo:
    """Bootstrap a minimal Microceph cluster in Juju.

    Returns:
        CephfsInfo: Information to mount the CephFS share.
    """
    _logger.info("Bootstrapping Microceph cluster")

    juju.deploy(
        "microceph",
        "microceph",
        base=base,
        channel="tentacle/candidate",
        num_units=1,
        storage={"osd-standalone": "loop,3,1G"},
        to=machine_id,
    )
    juju.wait(
        lambda status: jubilant.all_active(status, "microceph"),
        timeout=5000,
    )

    unit = "microceph/0"

    _wait_for_ceph(juju, unit)

    juju.exec("sudo ln -s /bin/true", unit=unit)
    juju.exec("sudo apt install -y ceph-common", unit=unit)
    juju.exec(f"microceph.ceph osd pool create {CEPH_FS_NAME}_data", unit=unit)
    juju.exec(f"microceph.ceph osd pool create {CEPH_FS_NAME}_metadata", unit=unit)
    juju.exec(
        f"microceph.ceph fs new {CEPH_FS_NAME} {CEPH_FS_NAME}_metadata {CEPH_FS_NAME}_data",
        unit=unit,
    )
    juju.exec(
        f"microceph.ceph fs authorize {CEPH_FS_NAME} client.{CEPH_USERNAME} {CEPH_PATH} rw",
        unit=unit,
    )
    juju.exec(
        "sudo ln -sf /var/snap/microceph/current/conf/ceph.client.admin.keyring"
        " /etc/ceph/ceph.client.admin.keyring",
        unit=unit,
    )
    juju.exec(
        "sudo ln -sf /var/snap/microceph/current/conf/ceph.keyring /etc/ceph/ceph.keyring",
        unit=unit,
    )
    juju.exec(
        "sudo ln -sf /var/snap/microceph/current/conf/ceph.conf /etc/ceph/ceph.conf",
        unit=unit,
    )

    _wait_for_ceph(juju, unit)
    juju.exec("mkdir -p /mnt/cephfs", unit=unit)
    juju.exec(f"sudo mount -t ceph admin@.{CEPH_FS_NAME}={CEPH_PATH} /mnt/cephfs", unit=unit)

    for i in [1, 2, 3]:
        juju.exec(f"sudo touch /mnt/cephfs/test-{i}", unit=unit)

    return _get_cephfs_info(juju, unit)


@tenacity.retry(wait=tenacity.wait_exponential(max=20), stop=tenacity.stop_after_attempt(20))
def _wait_for_ceph(juju: jubilant.Juju, unit: str) -> None:
    """Wait until the Ceph cluster is ready."""
    result = juju.exec("microceph.ceph -s -f json", unit=unit)
    status = json.loads(result.stdout)
    if status["health"]["status"] != "HEALTH_OK":
        raise Exception("CephFS is not available")


def _get_cephfs_info(juju: jubilant.Juju, unit: str) -> CephfsInfo:
    """Gather CephFS connection info from the Microceph unit."""
    result = juju.exec("microceph.ceph -s -f json", unit=unit)
    status = json.loads(result.stdout)
    fsid = status["fsid"]

    host = juju.exec("hostname", unit=unit).stdout.strip() + ":6789"
    key = juju.exec(f"microceph.ceph auth print-key client.{CEPH_USERNAME}", unit=unit).stdout

    return CephfsInfo(
        fsid=fsid,
        name=CEPH_FS_NAME,
        path=CEPH_PATH,
        monitor_hosts=[host],
        user=CEPH_USERNAME,
        key=key,
    )


@tenacity.retry(
    wait=tenacity.wait.wait_exponential(multiplier=2, min=1, max=10),
    stop=tenacity.stop_after_attempt(5),
    reraise=True,
)
def check_files(juju: jubilant.Juju, unit_name: str, path: str) -> None:
    result = juju.ssh(unit_name, f"ls {path}")
    assert "test-1" in result
    assert "test-2" in result
    assert "test-3" in result
