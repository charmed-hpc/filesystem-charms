#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import unittest
from pathlib import Path

import yaml
from charm import PEER_NAME, CephFSServerProxyCharm
from ops import BlockedStatus
from ops.testing import Harness

METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())
APP_NAME = METADATA["name"]


class TestCephFSShare(unittest.TestCase):
    """Test cephfs-share integration."""

    def setUp(self) -> None:
        self.harness = Harness(CephFSServerProxyCharm)
        self.integration_id = self.harness.add_relation("cephfs-share", "cephfs-client")
        self.harness.add_relation_unit(self.integration_id, "cephfs-client/0")
        self.harness.add_relation(
            PEER_NAME,
            APP_NAME,
        )
        self.harness.set_leader(True)
        self.harness.begin()

    def test_share_requested_no_config(self) -> None:
        """Test share requested handler when the config is not set."""
        integration = self.harness.charm.model.get_relation("cephfs-share", self.integration_id)
        app = self.harness.charm.model.get_app("cephfs-client")
        self.harness.charm._cephfs_share.on.share_requested.emit(integration, app)
        self.assertEqual(
            self.harness.charm.model.unit.status, BlockedStatus("No configured filesystem info")
        )

    def test_share_requested(self) -> None:
        """Test share requested with config set."""
        # Patch charm stored state
        self.harness.charm.set_state(
            "config",
            {
                "fsid": "354ca7c4-f10d-11ee-93f8-1f85f87b7845",
                "name": "ceph-fs",
                "path": "/",
                "monitor_hosts": ["10.5.0.80:6789", "10.5.2.23:6789", "10.5.2.17:6789"],
                "username": "ceph-client",
                "key": "AQAPdQldX264KBAAOyaxen/y0XBl1qxlGPTabw==",
            },
        )

        integration = self.harness.charm.model.get_relation("cephfs-share", self.integration_id)
        app = self.harness.charm.model.get_app("cephfs-client")
        self.harness.charm._cephfs_share.on.share_requested.emit(integration, app)
