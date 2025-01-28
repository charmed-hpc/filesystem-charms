#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Lustre server proxy charm operator for mount non-charmed Lustre shares."""

import logging
from typing import cast

import ops

from charms.filesystem_client.v0.filesystem_info import FilesystemProvides, LustreInfo

logger = logging.getLogger(__name__)


class LustreServerProxyCharm(ops.CharmBase):
    """Lustre server proxy charmed operator."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._filesystem = FilesystemProvides(self, "filesystem", "server-peers")
        self.framework.observe(self.on.config_changed, self._on_config_changed)

    def _on_config_changed(self, _) -> None:
        """Handle updates to Lustre server proxy configuration."""
        if not (mgs_nids := cast(str | None, self.config.get("mgs-nids"))):
            self.unit.status = ops.BlockedStatus("No configured mgs-nids")
            return

        if not (fs_name := cast(str | None, self.config.get("fs-name"))):
            self.unit.status = ops.BlockedStatus("No configured fs-name")
            return

        self._filesystem.set_info(LustreInfo(mgs_ids=mgs_nids.split(), fs_name=fs_name))

        self.unit.status = ops.ActiveStatus()


if __name__ == "__main__":  # pragma: nocover
    ops.main(LustreServerProxyCharm)
