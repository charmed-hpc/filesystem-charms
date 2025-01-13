#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Test base charm events such as Install, ConfigChanged, etc."""

from charm import NFSServerProxyCharm
from ops import testing


def test_config_no_hostname():
    """Test config-changed handler when there is no configured hostname."""
    context = testing.Context(NFSServerProxyCharm)
    out = context.run(context.on.config_changed(), testing.State())
    assert out.unit_status == testing.BlockedStatus(message="No configured hostname")


def test_config_no_path():
    """Test config-changed handler when there is no configured path."""
    context = testing.Context(NFSServerProxyCharm)
    state = testing.State(config={"hostname": "127.0.0.1"})
    out = context.run(context.on.config_changed(), state)
    assert out.unit_status == testing.BlockedStatus(message="No configured path")


def test_config_no_port():
    """Test config-changed handler when there is no configured path."""
    context = testing.Context(NFSServerProxyCharm)
    state = testing.State(config={"hostname": "127.0.0.1", "path": "/srv"})
    out = context.run(context.on.config_changed(), state)
    assert out.unit_status == testing.ActiveStatus()


def test_config_full():
    """Test config-changed handler with full config parameters."""
    context = testing.Context(NFSServerProxyCharm)
    state = testing.State(config={"hostname": "127.0.0.1", "path": "/srv", "port": 1234})
    out = context.run(context.on.config_changed(), state)
    assert out.unit_status == testing.ActiveStatus()
