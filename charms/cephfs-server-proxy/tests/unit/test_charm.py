#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import unittest
from pathlib import Path
import yaml
import ops.testing
from charm import PEER_NAME, CephFSServerProxyCharm
from charms.storage_libs.v0.cephfs_interfaces import CephFSAuthInfo, CephFSShareInfo
from ops.model import ActiveStatus, BlockedStatus

METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())
APP_NAME = METADATA["name"]


class TestCharm(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = ops.testing.Harness(CephFSServerProxyCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.add_relation(PEER_NAME, APP_NAME)
        self.harness.set_leader(True)
        self.harness.begin()

    def test_config_unset(self) -> None:
        """Test that the charm blocks if the config is not set."""
        self.harness.charm.on.config_changed.emit()
        self.assertEqual(
            self.harness.model.unit.status,
            BlockedStatus("Missing config keys fsid, sharepoint, monitor-hosts, auth-info"),
        )

    def test_config_already_set(self) -> None:
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

        self.harness.update_config(
            {
                "fsid": "update",
                "sharepoint": "update",
                "monitor-hosts": "update update update",
                "auth-info": "update",
            }
        )

        self._assert_config(
            CephFSShareInfo(
                fsid="354ca7c4-f10d-11ee-93f8-1f85f87b7845",
                name="ceph-fs",
                path="/",
                monitor_hosts=["10.5.0.80:6789", "10.5.2.23:6789", "10.5.2.17:6789"],
            ),
            CephFSAuthInfo(username="ceph-client", key="AQAPdQldX264KBAAOyaxen/y0XBl1qxlGPTabw=="),
        )

    def test_config(self) -> None:
        """Test config-changed handler."""
        # Patch charm stored state.
        self.harness.update_config(
            {
                "fsid": "354ca7c4-f10d-11ee-93f8-1f85f87b7845",
                "sharepoint": "ceph-fs:/",
                "monitor-hosts": "10.5.0.80:6789 10.5.2.23:6789 10.5.2.17:6789",
                "auth-info": "ceph-client:AQAPdQldX264KBAAOyaxen/y0XBl1qxlGPTabw==",
            }
        )
        self.assertEqual(self.harness.model.unit.status, ActiveStatus("Exporting share"))

        self._assert_config(
            CephFSShareInfo(
                fsid="354ca7c4-f10d-11ee-93f8-1f85f87b7845",
                name="ceph-fs",
                path="/",
                monitor_hosts=["10.5.0.80:6789", "10.5.2.23:6789", "10.5.2.17:6789"],
            ),
            CephFSAuthInfo(username="ceph-client", key="AQAPdQldX264KBAAOyaxen/y0XBl1qxlGPTabw=="),
        )

    def _assert_config(self, share_info: CephFSShareInfo, auth_info: CephFSAuthInfo) -> None:
        mount_info = self.harness.charm.mount_info
        self.assertIsNotNone(mount_info)
        charm_share_info, charm_auth_info = mount_info
        self.assertEqual(charm_share_info, share_info)
        self.assertEqual(charm_auth_info, auth_info)
