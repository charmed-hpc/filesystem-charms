#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Test nfs-share integration."""

import unittest

import ops.testing
from charm import NFSServerProxyCharm
from ops.model import BlockedStatus
from ops.testing import Harness


class TestNFSShare(unittest.TestCase):
    """Test nfs-share integration."""

    def setUp(self) -> None:
        ops.testing.SIMULATE_CAN_CONNECT = True
        self.addCleanup(setattr, ops.testing, "SIMULATE_CAN_CONNECT", False)
        self.harness = Harness(NFSServerProxyCharm)
        self.integration_id = self.harness.add_relation("nfs-share", "nfs-client")
        self.harness.add_relation_unit(self.integration_id, "nfs-client/0")
        self.harness.set_leader(True)
        self.harness.begin()

    def test_share_requested_no_endpoint(self) -> None:
        """Test share requested handler when there is no configured endpoint."""
        # Patch charm stored state.
        self.harness.charm._stored.endpoint = None
        integration = self.harness.charm.model.get_relation("nfs-share", self.integration_id)
        app = self.harness.charm.model.get_app("nfs-client")
        self.harness.charm._nfs_share.on.share_requested.emit(integration, app)
        self.assertEqual(
            self.harness.charm.model.unit.status, BlockedStatus("No configured endpoint")
        )

    def test_share_requested(self) -> None:
        """Test share requested handler."""
        # Patch charm stored state.
        self.harness.charm._stored.endpoint = "nfs://127.0.0.1/data"
        integration = self.harness.charm.model.get_relation("nfs-share", self.integration_id)
        app = self.harness.charm.model.get_app("nfs-client")
        self.harness.charm._nfs_share.on.share_requested.emit(integration, app)
