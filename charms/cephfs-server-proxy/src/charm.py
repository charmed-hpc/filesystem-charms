#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm the application."""

import logging
from typing import cast

import ops

from charms.filesystem_client.v0.filesystem_info import CephfsInfo, FilesystemProvides

logger = logging.getLogger(__name__)


class CharmError(Exception):
    """Raise if the charm encounters an error."""


class CephFSServerProxyCharm(ops.CharmBase):
    """CephFS server proxy charmed operator."""

    _REQUIRED_CONFIGS = ["fsid", "sharepoint", "monitor-hosts", "auth-info"]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._filesystem = FilesystemProvides(self, "filesystem", "server-peers")
        self.framework.observe(self.on.config_changed, self._on_config_changed)

    def _on_config_changed(self, _) -> None:
        """Handle updates to CephFS server proxy configuration."""
        config = {k: self.config.get(k) for k in CephFSServerProxyCharm._REQUIRED_CONFIGS}

        # This method catches both uninitialized configs and empty strings/lists.
        missing = [k for (k, v) in config.items() if not v]

        if missing:
            values = ", ".join(f"`{k}`" for k in missing)
            msg = f"missing required configuration for {values}"

            logger.error(msg)
            self.unit.status = ops.BlockedStatus(msg.capitalize())
            return

        # All configs are set from this point.
        fsid = cast(str, config["fsid"])

        # Expected format: <filesystem_name>:<filesystem_shared_path>
        sharepoint_cfg = cast(str, config["sharepoint"])
        sharepoint = sharepoint_cfg.split(":", maxsplit=1)
        if len(sharepoint) != 2 or not sharepoint[0] or not sharepoint[1]:
            msg = f"invalid sharepoint `{sharepoint_cfg}`"
            logger.error(msg)
            self.unit.status = ops.BlockedStatus(msg.capitalize())
            return

        name, path = sharepoint

        # Expected format: <ip/hostname>:<port> <ip/hostname>:<port>
        monitor_hosts = cast(str, config["monitor-hosts"]).split()

        # Expected format: <username>:<cephx-base64-key>
        auth_info_cfg = cast(str, config["auth-info"])
        auth_info = auth_info_cfg.split(":", maxsplit=1)
        if len(auth_info) != 2 or not auth_info[0] or not auth_info[1]:
            msg = f"invalid auth-info `{auth_info_cfg}`"
            logger.error(msg)
            self.unit.status = ops.BlockedStatus(msg.capitalize())
            return

        user, key = auth_info

        self._filesystem.set_info(
            CephfsInfo(
                fsid=fsid,
                name=name,
                path=path,
                monitor_hosts=monitor_hosts,
                user=user,
                key=key,
            )
        )

        self.unit.status = ops.ActiveStatus()


if __name__ == "__main__":  # pragma: nocover
    ops.main(CephFSServerProxyCharm)  # type: ignore
