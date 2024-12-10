#!/usr/bin/env python3
# Copyright 2024 Canonical
# See LICENSE file for licensing details.

"""Charm the application."""

import logging

import ops
from charms.filesystem_client.v0.interfaces import CephfsInfo, FsProvides, NfsInfo

_logger = logging.getLogger(__name__)

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


class FilesystemServerCharm(ops.CharmBase):
    def __init__(self, framework: ops.Framework):
        super().__init__(framework)
        self._fs_share = FsProvides(self, "fs-share", "server-peers")
        framework.observe(self.on.start, self._on_start)

    def _on_start(self, event: ops.StartEvent):
        """Handle start event."""
        _logger.info(self.config.get("type"))
        typ = self.config["type"]
        if "nfs" == typ:
            info = NFS_INFO
        elif "cephfs" == typ:
            info = CEPHFS_INFO
        else:
            raise ValueError("invalid filesystem type")

        self._fs_share.set_fs_info(info)
        _logger.info("set info")
        self.unit.status = ops.ActiveStatus()
        _logger.info("transitioned to active")


if __name__ == "__main__":  # pragma: nocover
    ops.main(FilesystemServerCharm)  # type: ignore
