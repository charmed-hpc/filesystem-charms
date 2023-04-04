#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""NFS server proxy charm operator for mount non-charmed NFS shares."""

import logging

from charms.storage_libs.v0.nfs_interfaces import NFSProvides, ShareRequestedEvent
from ops.charm import CharmBase
from ops.framework import StoredState
from ops.main import main
from ops.model import ActiveStatus, BlockedStatus
from utils.status import Status, StatusPool

logger = logging.getLogger(__name__)


class NFSServerProxyCharm(CharmBase):
    """NFS server proxy charmed operator."""

    _stored = StoredState()

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._status_pool = StatusPool(self)
        self._stored.set_default(endpoint=None)
        self._nfs_share = NFSProvides(self, "nfs-share")
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self._nfs_share.on.share_requested, self._on_share_requested)

    def _on_config_changed(self, _) -> None:
        """Handle updates to NFS server proxy configuration."""
        if (endpoint := self.config.get("endpoint")) is None:
            self._status_pool.add(
                Status(label="no_endpoint", status=BlockedStatus("No configured endpoint"))
            )
        elif endpoint is not None and self._stored.endpoint is None:
            logger.debug(f"Setting NFS share endpoint to {endpoint}")
            self._stored.endpoint = endpoint
        else:
            logger.warning(f"Endpoint can only be set once. Ignoring {endpoint}")

        self._status_pool.add(Status(label="ready", status=ActiveStatus("Exporting share")))

    def _on_share_requested(self, event: ShareRequestedEvent) -> None:
        """Handle when NFS client requests an NFS share."""
        logger.debug(
            (
                "NFS share requested with parameters "
                f"('name'={event.name}, 'allowlist'={event.allowlist}, 'size={event.size}')"
            )
        )
        if self._stored.endpoint is None:
            logger.warning("Deferring ShareRequested event because endpoint is not set")
            self._status_pool.add(Status(label="no_endpoint"))
            event.defer()
        else:
            logger.debug(f"Setting {self._stored.endpoint} as the NFS share endpoint")
            self._nfs_share.set_endpoint(event.relation.id, endpoint=self._stored.endpoint)


if __name__ == "__main__":  # pragma: nocover
    main(NFSServerProxyCharm)
