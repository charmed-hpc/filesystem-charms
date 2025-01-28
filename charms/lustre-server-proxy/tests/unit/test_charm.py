#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
"""Test base charm events such as Install, ConfigChanged, etc."""

from charm import LustreServerProxyCharm
from ops import testing

from charms.filesystem_client.v0.filesystem_info import LustreInfo


def test_config_none():
    """Test config-changed handler when there are no configs."""
    context = testing.Context(LustreServerProxyCharm)
    """Test config-changed handler when there is no configuration."""
    out = context.run(context.on.config_changed(), testing.State())
    assert out.unit_status == testing.BlockedStatus(message="No configured mgs-nids")


def test_config_no_fs_name():
    """Test config-changed handler when there is no configured fs-name."""
    context = testing.Context(LustreServerProxyCharm)
    state = testing.State(config={"mgs-nids": "demo-mgs1@tcp1"})
    out = context.run(context.on.config_changed(), state)
    assert out.unit_status == testing.BlockedStatus(message="No configured fs-name")


def test_config_no_mgs_ids():
    """Test config-changed handler when there is no configured mgs-nids."""
    context = testing.Context(LustreServerProxyCharm)
    state = testing.State(config={"fs-name": "lustre"})
    out = context.run(context.on.config_changed(), state)
    assert out.unit_status == testing.BlockedStatus(message="No configured mgs-nids")


def test_config_full():
    """Test config-changed handler with full config parameters."""
    rel = testing.PeerRelation(
        endpoint="server-peers",
    )
    state = testing.State(
        config={"mgs-nids": "demo-mgs1@tcp1 demo-mgs2@tcp1", "fs-name": "lustre"},
        relations={rel},
        leader=True,
    )
    context = testing.Context(LustreServerProxyCharm)

    with context(context.on.config_changed(), state) as manager:
        out = manager.run()
        info = LustreInfo.from_uri(
            out.get_relation(rel.id).local_app_data["endpoint"], manager.charm.model
        )

    assert out.unit_status == testing.ActiveStatus()

    assert info.fs_name == "lustre"
    assert info.mgs_ids == ["demo-mgs1@tcp1", "demo-mgs2@tcp1"]
