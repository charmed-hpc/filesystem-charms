#!/usr/bin/env python3
# Copyright 2024 Jose Julian Espina
# See LICENSE file for licensing details.

"""Charm the application."""

import logging

import ops

logger = logging.getLogger(__name__)


class StorageClientCharm(ops.CharmBase):
    """Charm the application."""

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)
        framework.observe(self.on.start, self._on_start)
        framework.observe(
            self.on["fs-share"].relation_joined, self._on_relation_joined
        )
        framework.observe(
            self.on["fs-share"].relation_changed, self._on_relation_joined
        )

    def _on_start(self, event: ops.StartEvent):
        """Handle start event."""
        self.unit.status = ops.ActiveStatus()

    
    def _on_relation_joined(self, event: ops.RelationJoinedEvent) -> None:
        logger.info(dict(event.relation.data))


if __name__ == "__main__":  # pragma: nocover
    ops.main(StorageClientCharm)  # type: ignore