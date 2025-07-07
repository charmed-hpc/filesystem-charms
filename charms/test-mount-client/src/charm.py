#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Operator to test the `mount_info` interface."""

import logging
from typing import cast

import ops
from ops.framework import EventBase

from charms.filesystem_client.v0.mount_info import MountInfo, MountRequires

logger = logging.getLogger(__name__)


class TestMountClient(ops.CharmBase):
    """Test mount client charmed operator."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._mount = MountRequires(self, "mount")
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self._mount.on.mount_provider_connected, self._on_config_changed)

    def _on_config_changed(self, event: EventBase) -> None:
        """Handle updates to NFS server proxy configuration."""
        mountpoint = cast(str | None, self.config.get("mountpoint"))
        if mountpoint is None:
            self.unit.status = ops.BlockedStatus("No configured mountpoint")
            return

        for relation in self._mount.relations:
            self._mount.set_mount_info(relation.id, MountInfo(mountpoint=mountpoint))

        self.unit.status = ops.ActiveStatus()


if __name__ == "__main__":  # pragma: nocover
    ops.main(TestMountClient)
