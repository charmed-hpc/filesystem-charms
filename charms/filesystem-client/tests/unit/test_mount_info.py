#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Test the mount_info charm library."""

import json
from dataclasses import asdict

import ops
import pytest
from ops import testing

from charms.filesystem_client.v0.mount_info import MountInfo, MountProvides, MountRequires

MOUNT_RELATION_NAME = "mount"
MOUNT_RELATION_INTERFACE = "mount_info"
MOUNT_CLIENT_METADATA = {
    "name": "mount-client",
    "requires": {
        MOUNT_RELATION_NAME: {"interface": MOUNT_RELATION_INTERFACE, "scope": "container"}
    },
}
MOUNT_PROVIDER_METADATA = {
    "name": "mount-provider",
    "provides": {
        MOUNT_RELATION_NAME: {"interface": MOUNT_RELATION_INTERFACE, "scope": "container"}
    },
    "subordinate": True,
}
MOUNT_INFO = MountInfo(
    mountpoint="/srv",
    noexec=False,
    read_only=True,
)


class MountProviderCharm(ops.CharmBase):
    """Mock mount provider charm for unit tests."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.mount = MountProvides(self, MOUNT_RELATION_NAME)
        self.framework.observe(self.mount.on.mount_requested, self._on_mount_requested)
        self.framework.observe(self.mount.on.mount_unrequested, self._on_mount_unrequested)

    def _on_mount_requested(self, _) -> None:
        self.mount.set_mount_status(mounted=True)
        assert self.mount.mount_info(self.mount.relations[0].id) == MOUNT_INFO

    def _on_mount_unrequested(self, _) -> None:
        self.mount.set_mount_status(mounted=False)
        assert self.mount.mount_info(self.mount.relations[0].id) is None


class MountRequirerCharm(ops.CharmBase):
    """Mock mount requirer charm for unit tests."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.mount = MountRequires(self, MOUNT_RELATION_NAME)
        self.framework.observe(
            self.mount.on.mount_provider_connected, self._on_mount_provider_connected
        )
        self.framework.observe(self.mount.on.mounted_filesystem, self._on_mounted_filesystem)
        self.framework.observe(self.mount.on.unmounted_filesystem, self._on_unmounted_filesystem)
        self.framework.observe(
            self.mount.on.mount_provider_disconnected, self._on_mount_provider_disconnected
        )

    def _on_mount_provider_connected(self, _) -> None:
        for relation in self.mount.relations:
            self.mount.set_mount_info(relation.id, MOUNT_INFO)

    def _on_mounted_filesystem(self, _) -> None:
        self.unit.status = ops.ActiveStatus("mounted filesystem")

    def _on_unmounted_filesystem(self, _) -> None:
        self.unit.status = ops.WaitingStatus("unmounted filesystem")

    def _on_mount_provider_disconnected(self, _) -> None:
        self.unit.status = ops.BlockedStatus("waiting for mount provider")


@pytest.fixture(scope="module")
def mount_provider_ctx() -> testing.Context:
    """Context for the mount provider charm."""
    yield testing.Context(MountProviderCharm, MOUNT_PROVIDER_METADATA)


@pytest.fixture(scope="module")
def mount_requirer_ctx() -> testing.Context:
    """Context for the mount client charm."""
    yield testing.Context(MountRequirerCharm, MOUNT_CLIENT_METADATA)


def test_provider_mount_requested(mount_provider_ctx: testing.Context):
    """Test handler when the mount client has provided the mount info."""
    rel = testing.SubordinateRelation(
        endpoint=MOUNT_RELATION_NAME,
        interface=MOUNT_RELATION_INTERFACE,
        remote_app_data={k: json.dumps(v) for k, v in asdict(MOUNT_INFO).items()},
    )
    state_in = testing.State(relations={rel})
    ctx = mount_provider_ctx

    state_out = ctx.run(ctx.on.relation_changed(rel), state_in)
    rel_out = state_out.get_relation(rel.id)
    assert rel_out.local_unit_data["mounted"] == "true"


def test_provider_mount_unrequested(mount_provider_ctx: testing.Context):
    """Test handler when the mount client has removed the mount info."""
    rel = testing.SubordinateRelation(
        endpoint=MOUNT_RELATION_NAME, interface=MOUNT_RELATION_INTERFACE, remote_app_data={}
    )
    state_in = testing.State(relations={rel})
    ctx = mount_provider_ctx

    state_out = ctx.run(ctx.on.relation_changed(rel), state_in)
    rel_out = state_out.get_relation(rel.id)
    assert rel_out.local_unit_data["mounted"] == "false"


def test_requirer_mount_provider_connected(mount_requirer_ctx: testing.Context):
    """Test handler when the mount provider connects."""
    ctx = mount_requirer_ctx
    state_in = testing.State.from_context(ctx, leader=True)
    rel_in = state_in.get_relations(MOUNT_RELATION_NAME)[0]

    state_out = ctx.run(ctx.on.relation_created(rel_in), state_in)
    rel_out = state_out.get_relation(rel_in.id)
    output = {k: json.loads(v) for k, v in rel_out.local_app_data.items()}
    assert output == asdict(MOUNT_INFO)


def test_requirer_mounted_filesystem(mount_requirer_ctx: testing.Context):
    """Test handler when the mount provider has mounted the filesystem."""
    rel = testing.SubordinateRelation(
        endpoint=MOUNT_RELATION_NAME,
        interface=MOUNT_RELATION_INTERFACE,
        remote_unit_data={"mounted": "true"},
    )
    state_in = testing.State(relations={rel})
    ctx = mount_requirer_ctx

    state_out = ctx.run(ctx.on.relation_changed(rel), state_in)
    assert state_out.unit_status == testing.ActiveStatus("mounted filesystem")


def test_requirer_unmounted_filesystem(mount_requirer_ctx: testing.Context):
    """Test handler when the mount provider has unmounted the filesystem."""
    rel = testing.SubordinateRelation(
        endpoint=MOUNT_RELATION_NAME,
        interface=MOUNT_RELATION_INTERFACE,
        remote_unit_data={"mounted": "false"},
    )
    state_in = testing.State(relations={rel})
    ctx = mount_requirer_ctx

    state_out = ctx.run(ctx.on.relation_changed(rel), state_in)
    assert state_out.unit_status == testing.WaitingStatus("unmounted filesystem")


def test_requirer_mount_provider_disconnected(mount_requirer_ctx: testing.Context):
    """Test handler when the mount provider disconnects."""
    ctx = mount_requirer_ctx
    state_in = testing.State.from_context(ctx, leader=True)
    rel_in = state_in.get_relations(MOUNT_RELATION_NAME)[0]

    state_out = ctx.run(ctx.on.relation_broken(rel_in), state_in)
    assert state_out.unit_status == testing.BlockedStatus("waiting for mount provider")
