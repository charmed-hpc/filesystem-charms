#!/usr/bin/env python3
# Copyright 2024 Canonical
# See LICENSE file for licensing details.

"""Charm the application."""

import logging
import pathlib
import subprocess

import charms.operator_libs_linux.v0.apt as apt
import ops
from charms.storage_libs.v0.cephfs_interfaces import (
    CephFSRequires,
    MountShareEvent,
    ServerConnectedEvent,
)

MOUNTPOINT = "/data"

_logger = logging.getLogger(__name__)


class CephFSClientMock(ops.CharmBase):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._ceph_share = CephFSRequires(self, "cephfs-share")
        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(self._ceph_share.on.server_connected, self._on_server_connected)
        self.framework.observe(self._ceph_share.on.mount_share, self._on_mount_share)
        self.framework.observe(self._ceph_share.on.umount_share, self._on_umount_share)

    def _on_install(self, _) -> None:
        apt.add_package(["ceph-common"], update_cache=True)
        self.unit.status = ops.WaitingStatus("")

    def _on_server_connected(self, event: ServerConnectedEvent) -> None:
        self.unit.status = ops.MaintenanceStatus("Requesting CephFS share")
        self._ceph_share.request_share(event.relation.id, name=MOUNTPOINT)

    def _on_mount_share(self, event: MountShareEvent) -> None:
        share_info = event.share_info
        auth_info = event.auth_info

        pathlib.Path(MOUNTPOINT).mkdir()

        try:
            subprocess.run(
                [
                    "mount",
                    "-t",
                    "ceph",
                    f"{auth_info.username}@{share_info.fsid}.{share_info.name}={share_info.path}",
                    MOUNTPOINT,
                    "-o",
                    f"mon_addr={'/'.join(share_info.monitor_hosts)},secret={auth_info.key}",
                ],
                stderr=subprocess.PIPE,
                check=True,
                text=True,
            )
        except subprocess.CalledProcessError as e:
            _logger.error(f"{e} Reason:\n{e.stderr}")
            raise Exception(f"Failed to mount share at {MOUNTPOINT}")

        self.unit.status = ops.ActiveStatus("")

    def _on_umount_share(self, _) -> None:
        subprocess.run(["umount", MOUNTPOINT], stderr=subprocess.PIPE, check=True, text=True)
        pathlib.Path(MOUNTPOINT).rmdir()
        self.unit.status = ops.WaitingStatus("")


if __name__ == "__main__":  # pragma: nocover
    ops.main(CephFSClientMock)  # type: ignore
