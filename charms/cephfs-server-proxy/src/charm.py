#!/usr/bin/env python3
# Copyright 2024 Ubuntu
# See LICENSE file for licensing details.

"""Charm the application."""

import logging
import json

from typing import Optional

from charms.storage_libs.v0.ceph_interfaces import CephFSProvides, ShareRequestedEvent, CephFSShareInfo

import ops

logger = logging.getLogger(__name__)


class CephFSProxyOperatorCharm(ops.CharmBase):
    """CephFS server proxy charmed operator."""
    _REQUIRED_CONFIGS = ["fsid", "sharepoint", "monitor-hosts", "auth-info"]
    _stored = ops.StoredState()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._stored.set_default(
            share_info = None
        )
        self._cephfs_share = CephFSProvides(self, "cephfs-share")
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self._cephfs_share.on.share_requested, self._on_share_requested)
    
    def _on_config_changed(self, _) -> None:
        """Handle updates to CephFS server proxy configuration."""
        if self._stored.share_info:
            logger.warning("Filesystem info can only be set once. Ignoring new config.")
            return

        config = { k:self.config.get(k) for k in CephFSProxyOperatorCharm._REQUIRED_CONFIGS }

        # This method catches both uninitialized configs and empty strings/lists.
        missing = [k for (k, v) in config.items() if not v]

        if missing:
            msg = f"Missing config key{'s' if len(missing) > 1 else ''} {', '.join(missing)}"
            logger.error(msg)
            self.unit.status = ops.BlockedStatus(msg)
            return
        
        # All configs are set from this point.
        fsid = config["fsid"]
        fs_name, fs_path = config["sharepoint"].split(':') # Expected format: <filesystem_name>:<filesystem_shared_path>
        monitor_hosts = config["monitor-hosts"].split() # Expected format: <ip/hostname>:<port> <ip/hostname>:<port>
        username, cephx_key = config["auth-info"].split(':') # Expected format: <username>:<cephx-base64-key>
        auth_info = self.app.add_secret(
            {
                'username': username,
                'cephx-key': cephx_key
            },
            label="auth-info",
            description="Auth info to authenticate against the CephFS share"
        )

        # Assume all config is correct. The relation library should be the one in charge of validating inputs.
        self.share_info = CephFSShareInfo(
            fsid=fsid,
            name=fs_name,
            path=fs_path,
            monitor_hosts=monitor_hosts,
            auth_id=auth_info.id
        )

        self.unit.status = ops.ActiveStatus("Exporting share")
    
    def _on_share_requested(self, event: ShareRequestedEvent) -> None:
        """Handle when CephFS client requests a CephFS share."""
        logger.debug(
            (
                "NFS share requested with parameters "
                f"('name'={event.name})"
            )
        )
        if not self.share_info:
            logger.warning("Deferring ShareRequested event because filesystem info is not set")
            self.unit.status = ops.BlockedStatus("No configured filesystem info")
            event.defer()
        else:
            self.model.get_secret(label="auth-info").grant(event.relation)

            logger.debug(f"CephFS mount info set to {self._stored.share_info}")
            self._cephfs_share.set_share(event.relation.id, share_info=self.share_info)

    @property
    def share_info(self) -> Optional[CephFSShareInfo]:
        if not (share_info := self._stored.share_info):
            return

        return CephFSShareInfo(**share_info)

    @share_info.setter
    def share_info(self, value: CephFSShareInfo) -> None:
        self._stored.share_info = value.dict()



if __name__ == "__main__":  # pragma: nocover
    ops.main(CephFSProxyOperatorCharm)  # type: ignore
