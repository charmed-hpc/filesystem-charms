#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
"""Test base charm events such as Install, ConfigChanged, etc."""

from ops import testing

from charm import CephFSServerProxyCharm


def test_config_missing_all():
    """Test config-changed handler when there is no configured hostname."""
    context = testing.Context(CephFSServerProxyCharm)
    out = context.run(context.on.config_changed(), testing.State())
    assert out.unit_status.name == "blocked"
    assert "Missing required configuration" in out.unit_status.message


def test_config_missing_fsid():
    """Test config-changed handler when there is no configured fsid."""
    context = testing.Context(CephFSServerProxyCharm)
    state = testing.State(
        config={
            "sharepoint": "ceph-fs:/",
            "monitor-hosts": "10.5.0.80:6789 10.5.2.23:6789 10.5.2.17:6789",
            "auth-info": "ceph-client:AQAPdQldX264KBAAOyaxen/y0XBl1qxlGPTabw==",
        }
    )
    out = context.run(context.on.config_changed(), state)
    assert out.unit_status == testing.BlockedStatus(
        message="Missing required configuration for `fsid`"
    )


def test_config_invalid_sharepoint():
    """Test config-changed handler when the sharepoint is in an invalid format."""
    context = testing.Context(CephFSServerProxyCharm)
    state = testing.State(
        config={
            "fsid": "354ca7c4-f10d-11ee-93f8-1f85f87b7845",
            "sharepoint": "invalid-things",
            "monitor-hosts": "10.5.0.80:6789 10.5.2.23:6789 10.5.2.17:6789",
            "auth-info": "ceph-client:AQAPdQldX264KBAAOyaxen/y0XBl1qxlGPTabw==",
        }
    )
    out = context.run(context.on.config_changed(), state)
    assert out.unit_status == testing.BlockedStatus(message="Invalid sharepoint `invalid-things`")


def test_config_invalid_auth_info():
    """Test config-changed handler when the auth into is in an invalid format."""
    context = testing.Context(CephFSServerProxyCharm)
    state = testing.State(
        config={
            "fsid": "354ca7c4-f10d-11ee-93f8-1f85f87b7845",
            "sharepoint": "ceph-fs:/",
            "monitor-hosts": "10.5.0.80:6789 10.5.2.23:6789 10.5.2.17:6789",
            "auth-info": "invalid info",
        }
    )
    out = context.run(context.on.config_changed(), state)
    assert out.unit_status == testing.BlockedStatus(message="Invalid auth-info `invalid info`")


def test_config_full():
    context = testing.Context(CephFSServerProxyCharm)
    state = testing.State(
        config={
            "fsid": "354ca7c4-f10d-11ee-93f8-1f85f87b7845",
            "sharepoint": "ceph-fs:/",
            "monitor-hosts": "10.5.0.80:6789 10.5.2.23:6789 10.5.2.17:6789",
            "auth-info": "ceph-client:AQAPdQldX264KBAAOyaxen/y0XBl1qxlGPTabw==",
        }
    )
    out = context.run(context.on.config_changed(), state)
    assert out.unit_status == testing.ActiveStatus()
