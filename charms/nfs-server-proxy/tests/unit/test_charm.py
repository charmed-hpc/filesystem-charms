#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Test base charm events such as Install, ConfigChanged, etc."""

import unittest
from unittest.mock import PropertyMock, patch

import ops.testing
from charm import NFSServerProxyCharm
from ops.model import ActiveStatus, BlockedStatus
from ops.testing import Harness


class TestCharm(unittest.TestCase):
    """Test nfs-server-proxy charmed operator."""

    def setUp(self) -> None:
        ops.testing.SIMULATE_CAN_CONNECT = True
        self.addCleanup(setattr, ops.testing, "SIMULATE_CAN_CONNECT", False)
        self.harness = Harness(NFSServerProxyCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()

    @patch(
        "charm.NFSServerProxyCharm.config",
        new_callable=PropertyMock(return_value={"endpoint": None}),
    )
    def test_config_no_endpoint(self, _) -> None:
        """Test config-changed handler when there is no configured endpoint."""
        self.harness.charm.on.config_changed.emit()
        self.assertEqual(self.harness.model.unit.status, BlockedStatus("No configured endpoint"))

    @patch(
        "charm.NFSServerProxyCharm.config",
        new_callable=PropertyMock(return_value={"endpoint": "nfs://127.0.0.1/data"}),
    )
    def test_config_endpoint_already_set(self, _) -> None:
        """Test config-changed handler when endpoint is already configured."""
        # Patch charm stored state.
        self.harness.charm._stored.endpoint = None
        self.harness.charm.on.config_changed.emit()
        self.assertEqual(self.harness.model.unit.status, ActiveStatus("Exporting share"))

    @patch(
        "charm.NFSServerProxyCharm.config",
        new_callable=PropertyMock(return_value={"endpoint": "nfs://127.0.0.1/data"}),
    )
    def test_config(self, _) -> None:
        """Test that the config-changed handler works."""
        # Patch charm stored state.
        self.harness.charm._stored.endpoint = "nfs://127.0.0.1/polaris-research-data"
        self.harness.charm.on.config_changed.emit()
        self.assertEqual(self.harness.model.unit.status, ActiveStatus("Exporting share"))
