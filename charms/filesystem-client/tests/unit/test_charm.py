#!/usr/bin/env python3
# Copyright 2024-2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for FilesystemClientCharm."""

import json
from dataclasses import asdict
from unittest.mock import MagicMock, patch

import pytest
from charm import FilesystemClientCharm
from charms.filesystem_client.v0.mount_info import MountInfo
from ops import testing
from utils.manager import Error


@pytest.fixture(scope="function")
def ctx() -> testing.Context[FilesystemClientCharm]:
    """Mock `FilesystemClientCharm` context."""
    return testing.Context(FilesystemClientCharm)


@pytest.fixture(scope="function")
def mock_mounts_manager() -> MagicMock:
    """Mock `charm.MountsManager`."""
    with patch("charm.MountsManager") as manager_cls:
        mock_manager = MagicMock()
        manager_cls.return_value = mock_manager
        yield mock_manager


@pytest.mark.parametrize(
    "is_setup", (pytest.param(True, id="is setup"), pytest.param(False, id="is not setup"))
)
def test_manager_mounted_false_if_exc(
    ctx: testing.Context, is_setup: bool, mock_mounts_manager: MagicMock
) -> None:
    """mounted=false is written in the case of a raised exception."""
    mount_rel = testing.SubordinateRelation(endpoint="mount")
    state_in = testing.State(relations={mount_rel})

    mock_mounts_manager.supported.return_value = True
    mock_mounts_manager.is_setup.return_value = is_setup
    mock_mounts_manager.setup.side_effect = Error("apt failed")
    mock_mounts_manager.mounts.side_effect = RuntimeError("unexpected")

    state_out = ctx.run(ctx.on.config_changed(), state_in)

    assert state_out.get_relation(mount_rel.id).local_unit_data["mounted"] == "false"
    assert state_out.unit_status == testing.BlockedStatus(
        "Failed to mount filesystems. See `juju debug-log` for details"
    )


def test_successful_mount_sets_mounted_true(
    ctx: testing.Context, mock_mounts_manager: MagicMock
) -> None:
    """mounted=true is written and ActiveStatus is set after a successful mount."""
    mount_rel = testing.SubordinateRelation(
        endpoint="mount",
        remote_app_data={
            k: json.dumps(v) for k, v in asdict(MountInfo(mountpoint="/mnt/nfs")).items()
        },
    )
    fs_rel = testing.Relation(
        endpoint="filesystem",
        remote_app_data={"endpoint": "nfs://(192.168.1.1)/srv"},
    )
    state_in = testing.State(
        leader=True,
        relations={mount_rel, fs_rel},
    )

    mock_mounts_manager.supported.return_value = True
    mock_mounts_manager.is_setup.return_value = True
    mock_mounts_manager.mounts = MagicMock()

    state_out = ctx.run(ctx.on.config_changed(), state_in)

    assert state_out.get_relation(mount_rel.id).local_unit_data["mounted"] == "true"
    assert state_out.unit_status == testing.ActiveStatus("Mounted filesystem at `/mnt/nfs`")


@pytest.mark.parametrize(
    "enable_lustre",
    (pytest.param(True, id="lustre enabled"), pytest.param(False, id="lustre disabled")),
)
def test_enable_lustre_sets_manager_flag(
    ctx: testing.Context, enable_lustre: bool, mock_mounts_manager: MagicMock
) -> None:
    """The enable-lustre config determines if the mount manager enables lustre support."""
    state_in = testing.State(config={"enable-lustre": enable_lustre})
    mock_mounts_manager.supported.return_value = True
    mock_mounts_manager.is_setup.return_value = True
    mock_mounts_manager.mounts = MagicMock()

    state_out = ctx.run(ctx.on.config_changed(), state_in)
    assert mock_mounts_manager.enable_lustre == enable_lustre
    assert state_out.unit_status == testing.BlockedStatus(
        "Missing `mountpoint` config or `mount` integration"
    )
