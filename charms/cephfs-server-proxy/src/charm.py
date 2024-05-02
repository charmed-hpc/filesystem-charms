#!/usr/bin/env python3
# Copyright 2024 Ubuntu
# See LICENSE file for licensing details.

"""Charm the application."""

import json
import logging
from typing import Any, Optional, Tuple

import ops
from charms.storage_libs.v0.cephfs_interfaces import (
    CephFSAuthInfo,
    CephFSProvides,
    CephFSShareInfo,
    ShareRequestedEvent,
)

logger = logging.getLogger(__name__)

PEER_NAME = "proxy"


class CharmError(Exception):
    """Raise if the charm encounters an error."""


class CephFSServerProxyCharm(ops.CharmBase):
    """CephFS server proxy charmed operator."""

    _REQUIRED_CONFIGS = ["fsid", "sharepoint", "monitor-hosts", "auth-info"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._cephfs_share = CephFSProvides(self, "cephfs-share")
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self._cephfs_share.on.share_requested, self._on_share_requested)

    def _on_config_changed(self, _) -> None:
        """Handle updates to CephFS server proxy configuration."""
        if self.get_state("config"):
            logger.warning("Filesystem info can only be set once. Ignoring new config.")
            return

        config = {k: self.config.get(k) for k in CephFSServerProxyCharm._REQUIRED_CONFIGS}

        # This method catches both uninitialized configs and empty strings/lists.
        missing = [k for (k, v) in config.items() if not v]

        if missing:
            msg = f"Missing config key{'s' if len(missing) > 1 else ''} {', '.join(missing)}"
            logger.error(msg)
            self.unit.status = ops.BlockedStatus(msg)
            return

        # All configs are set from this point.
        fsid = str(config["fsid"])
        fs_name, fs_path = str(config["sharepoint"]).split(
            ":"
        )  # Expected format: <filesystem_name>:<filesystem_shared_path>
        monitor_hosts = str(
            config["monitor-hosts"]
        ).split()  # Expected format: <ip/hostname>:<port> <ip/hostname>:<port>
        username, cephx_key = str(config["auth-info"]).split(
            ":"
        )  # Expected format: <username>:<cephx-base64-key>

        # Assume all config is correct. The relation library should be the one in charge of validating inputs.
        self.set_state(
            "config",
            {
                "fsid": fsid,
                "name": fs_name,
                "path": fs_path,
                "monitor_hosts": monitor_hosts,
                "username": username,
                "key": cephx_key,
            },
        )

        self.unit.status = ops.ActiveStatus("Exporting share")

    def _on_share_requested(self, event: ShareRequestedEvent) -> None:
        """Handle when CephFS client requests a CephFS share."""
        logger.debug(("NFS share requested with parameters " f"('name'={event.name})"))
        if not (mount_info := self.mount_info):
            logger.warning("Deferring ShareRequested event because filesystem info is not set")
            self.unit.status = ops.BlockedStatus("No configured filesystem info")
            event.defer()
            return
        share_info, auth_info = mount_info
        logger.debug(f"CephFS mount info set to {share_info}")
        self._cephfs_share.set_share(event.relation.id, share_info=share_info, auth_info=auth_info)

    @property
    def mount_info(self) -> Optional[Tuple[CephFSShareInfo, CephFSAuthInfo]]:
        """Get the required info to mount the proxied CephFS share."""
        if not (config := self.get_state("config")):
            return

        share_info = CephFSShareInfo(
            fsid=config["fsid"],
            name=config["name"],
            path=config["path"],
            monitor_hosts=config["monitor_hosts"],
        )
        auth_info = CephFSAuthInfo(username=config["username"], key=config["key"])

        return (share_info, auth_info)

    @property
    def peers(self) -> Optional[ops.Relation]:
        """Fetch the peer relation."""
        return self.model.get_relation(PEER_NAME)

    def get_state(self, key: str) -> dict[Any, Any]:
        """Get a value from the global state."""
        if not self.peers:
            return {}

        data = self.peers.data[self.app].get(key, "{}")
        return json.loads(data)

    def set_state(self, key: str, data: Any) -> None:
        """Insert a value into the global state."""
        if not self.peers:
            raise CharmError(
                "Peer relation can only be accessed after the relation is established"
            )

        self.peers.data[self.app][key] = json.dumps(data)


if __name__ == "__main__":  # pragma: nocover
    ops.main(CephFSServerProxyCharm)  # type: ignore
