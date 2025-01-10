#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""NFS server proxy charm operator for mount non-charmed NFS shares."""

import logging
from typing import cast

import ops
from charms.filesystem_client.v0.filesystem_info import FilesystemProvides, NfsInfo

logger = logging.getLogger(__name__)


class NFSServerProxyCharm(ops.CharmBase):
    """NFS server proxy charmed operator."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._filesystem = FilesystemProvides(self, "filesystem", "server-peers")
        self.framework.observe(self.on.config_changed, self._on_config_changed)

    def _on_config_changed(self, _) -> None:
        """Handle updates to NFS server proxy configuration."""
        if (hostname := cast(str | None, self.config.get("hostname"))) is None:
            self.unit.status = ops.BlockedStatus("No configured hostname")
            return

        if (path := cast(str | None, self.config.get("path"))) is None:
            self.unit.status = ops.BlockedStatus("No configured path")
            return

        port = cast(int | None, self.config.get("port"))

        self._filesystem.set_info(
            NfsInfo(
                hostname=hostname,
                path=path,
                port=port,
            )
        )

        self.unit.status = ops.ActiveStatus()


if __name__ == "__main__":  # pragma: nocover
    ops.main(NFSServerProxyCharm)
