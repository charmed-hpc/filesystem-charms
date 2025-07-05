#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""NFS server proxy charm operator for mount non-charmed NFS shares."""

import logging
from typing import cast

import ops
from ops.framework import EventBase

from charms.filesystem_client.v0.mount_info import MountRequires, MountInfo

logger = logging.getLogger(__name__)


class NFSServerProxyCharm(ops.CharmBase):
    """NFS server proxy charmed operator."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._mount = MountRequires(self, "mount")
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self._mount.on.mount_provider_connected, self._on_config_changed)
        self.framework.observe(self._mount.on.mounted_filesystem, self._on_mounted_filesystem)
        self.framework.observe(self._mount.on.unmounted_filesystem, self._on_unmounted_filesystem)

    def _on_config_changed(self, event: EventBase) -> None:
        """Handle updates to NFS server proxy configuration."""
        if (mountpoint := cast(str | None, self.config.get("mountpoint"))) is None:
            self.unit.status = ops.BlockedStatus("No configured mountpoint")
            return

        for relation in self._mount.relations:
            self._mount.set_mount_info(relation.id, MountInfo(mountpoint=mountpoint))

        self.unit.status = ops.ActiveStatus()

    def _on_mounted_filesystem(self, _) -> None:
        logger.info("================= mounted filesystem =================")

    def _on_unmounted_filesystem(self, _) -> None:
        logger.info("================= unmounted filesystem =================")


if __name__ == "__main__":  # pragma: nocover
    ops.main(NFSServerProxyCharm)
