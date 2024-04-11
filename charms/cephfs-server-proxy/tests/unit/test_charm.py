# Copyright 2024 Ubuntu
# See LICENSE file for licensing details.
#
# Learn more about testing at: https://juju.is/docs/sdk/testing

import unittest
from unittest.mock import PropertyMock, patch

import ops.testing
from charm import CephFSProxyOperatorCharm
from ops.model import ActiveStatus, BlockedStatus
from ops.testing import Harness

class TestCharm(unittest.TestCase):
    def setUp(self):
        self.harness = ops.testing.Harness(CephFSProxyOperatorCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()

    @patch(
        "charm.CephFSProxyOperatorCharm.config",
        new_callable=PropertyMock(return_value={
            "fsid": "354ca7c4-f10d-11ee-93f8-1f85f87b7845",
            "sharepoint": "ceph-fs:/",
            "monitor-hosts": "10.5.0.80:6789 10.5.2.23:6789 10.5.2.17:6789",
            "auth-info": "ceph-client:AQAPdQxmX264KBAAOyaxen/y0XBl1qxlGPTabw=="
        }),
    )
    def test_config_endpoint(self, _) -> None:
        """Test config-changed handler."""
        # Patch charm stored state.
        self.harness.charm._stored.share_info = None
        self.harness.charm.on.config_changed.emit()
        self.assertEqual(self.harness.model.unit.status, ActiveStatus("Exporting share"))