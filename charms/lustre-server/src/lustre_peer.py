#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Peer relation observer for the Lustre charm."""

import logging
from enum import StrEnum
from typing import TYPE_CHECKING

import lustre_fs
import ops
import pydantic
from charms.filesystem_client.v0.filesystem_info import LustreInfo
from constants import LUSTRE_FSNAME, OST_STORAGE
from errors import LustreFilesystemError, LustrePeerDuplicateMgsError, LustrePeerError
from lustre_ops import lnet
from lustre_ops.errors import LNetError
from state import check_lustre

if TYPE_CHECKING:
    from charm import LustreCharm

_logger = logging.getLogger(__name__)

PEER_RELATION = "lustre-peer"


class _LustrePeerStatus(StrEnum):
    """Charm status messages for the Lustre peer observer."""

    FAILED_OSS_SETUP = "Failed to set up OSS"
    FAILED_PUBLISH_FILESYSTEM_INFO = "Failed to publish filesystem info to peer relation"
    FAILED_SET_UNIT_READY = "Failed to set unit ready in peer relation"
    MULTIPLE_MGS_UNITS = "Cluster error: multiple units have MGT+MDT storage attached"


class LustrePeerAppData(pydantic.BaseModel):
    """App-level data written by the leader to the peer relation databag.

    Attributes:
        mgs_nids: The LNet NIDs of the MGS unit. Example: ["10.0.0.5@tcp"].
        mgs_unit_name: The Juju name for the MGS unit. Example: "lustre/0".
    """

    mgs_nids: list[str] = pydantic.Field(
        default_factory=list, description="LNet NIDs of the MGS unit. Example: ['10.0.0.5@tcp']."
    )
    mgs_unit_name: str | None = pydantic.Field(
        default=None, description="Juju name for the MGS unit. Example: 'lustre/0'."
    )


class LustrePeerUnitData(pydantic.BaseModel):
    """Unit-level data written by each unit to the peer relation databag.

    Attributes:
        ready: Whether this unit has completed Lustre service setup.
        mgs_nids: LNet NIDs of this unit, if it is running as the MGS. To be
        promoted to app data by the leader.
    """

    ready: bool = pydantic.Field(
        default=False, description="Whether this unit has completed Lustre service setup."
    )
    mgs_nids: list[str] = pydantic.Field(
        default_factory=list,
        description="LNet NIDs of this unit, if it is running as the MGS. Example: ['10.0.0.5@tcp'].",
    )


class LustrePeerObserver(ops.Object):
    """Manages the Lustre peer relation."""

    def __init__(self, charm: "LustreCharm"):
        super().__init__(charm, PEER_RELATION)
        self._charm = charm
        charm.framework.observe(
            charm.on[PEER_RELATION].relation_changed, self._on_relation_changed
        )

    def mgs_nids_published(self) -> list[str]:
        """Publish this unit's MGS NIDs to its unit databag.

        The leader promotes these to app data when it observes the relation-changed
        event triggered by this write. If this unit is itself the leader, no such
        event will arrive (a unit does not receive relation-changed for writes to
        its own databag), so it promotes itself immediately.

        Returns:
            The published MGS NID strings.

        Raises:
            LustrePeerError: If an error occurs publishing the MGS NIDs.
        """
        try:
            mgs_nids = lnet.get_nids()
        except LNetError as e:
            raise LustrePeerError("Failed to determine MGS NID") from e

        if not mgs_nids:
            raise LustrePeerError("No LNet NIDs configured on this unit")

        data = self.get_unit_data()
        data.mgs_nids = mgs_nids
        self.set_unit_data(data)

        if self.model.unit.is_leader():
            # A relation-changed event is not triggered on the unit that writes to
            # its own unit data, so the leader must ready itself rather than
            # wait for an event that will never arrive.
            self._promote_mgs_nids(self.model.unit.name, mgs_nids)
            self.set_unit_ready()
            self._try_publish_filesystem_info(mgs_nids, LUSTRE_FSNAME)

        return mgs_nids

    def get_app_data(self) -> LustrePeerAppData:
        """Return the application data in the peer relation databag.

        Returns:
            The application data, or a default instance if none is set.
        """
        rel = self._get_relation_checked()
        return rel.load(LustrePeerAppData, rel.app) or LustrePeerAppData()

    def set_app_data(self, data: LustrePeerAppData) -> None:
        """Set the application data in the peer relation databag.

        Args:
            data: The data to write.
        """
        rel = self._get_relation_checked()
        rel.save(data, rel.app)

    def get_unit_data(self, unit: ops.Unit | None = None) -> LustrePeerUnitData:
        """Return the unit data in the peer relation databag.

        Args:
            unit: The unit whose data to read. Defaults to this unit.

        Returns:
            The unit's data, or a default instance if none is set.
        """
        rel = self._get_relation_checked()
        unit = unit or self.model.unit
        return rel.load(LustrePeerUnitData, unit) or LustrePeerUnitData()

    def set_unit_data(self, data: LustrePeerUnitData, unit: ops.Unit | None = None) -> None:
        """Set the unit data in the peer relation databag.

        Args:
            data: The data to write.
            unit: The unit whose data to write. Defaults to this unit.
        """
        rel = self._get_relation_checked()
        unit = unit or self.model.unit
        rel.save(data, unit)

    def set_unit_ready(self) -> None:
        """Set calling unit as ready in its unit data."""
        data = self.get_unit_data()
        data.ready = True
        self.set_unit_data(data)

    def _on_relation_changed(self, event: ops.RelationChangedEvent) -> None:
        """Handle the peer relation changed event."""
        if not self._try_promote_mgs(event):
            return

        try:
            data = self.get_app_data()
        except LustrePeerError as e:
            _logger.warning("failed to get peer relation data: %s", e)
            return

        if data.mgs_unit_name is None or not data.mgs_nids:
            _logger.warning("MGS data not yet published. cannot configure Lustre services.")
            return

        if not self._try_oss_setup(data):
            return

        try:
            self.set_unit_ready()
        except LustrePeerError as e:
            _logger.exception("failed to set unit ready: %s", e)
            self.model.unit.status = ops.BlockedStatus(_LustrePeerStatus.FAILED_SET_UNIT_READY)
            return

        if self.model.unit.is_leader():
            # This call to `_try_publish_filesystem_info` must occur after the call to
            # `_set_unit_ready` above.
            #
            # Filesystem info is published only after every unit has reported ready by calling
            # `_set_unit_ready`. This writes a value to the peer relation unit data, which
            # triggers a relation-changed event on *other* units, meaning the leader repeatedly
            # retries the publish here as each unit reports ready.
            #
            # A relation-changed event is *not* triggered on the unit that writes to its own unit
            # data. In the case where the leader is an OSS and the last unit to become ready, no
            # further event will arrive to trigger the publish. This case is addressed by ensuring
            # the publish attempt occurs after the unit sets itself ready, so no further event is
            # needed.
            try:
                self._try_publish_filesystem_info(data.mgs_nids, LUSTRE_FSNAME)
            except LustrePeerError as e:
                _logger.exception("failed to publish filesystem info: %s", e)
                self.model.unit.status = ops.BlockedStatus(
                    _LustrePeerStatus.FAILED_PUBLISH_FILESYSTEM_INFO
                )
                return

        # FIXME: Cannot use @refresh decorator here due to `AttributeError: 'LustrePeer' object
        # has no attribute 'unit'`. Set status directly for now.
        self.model.unit.status = check_lustre(self._charm)

    def _all_units_ready(self) -> bool:
        """Check whether every planned unit has reported ready.

        Returns:
            True if the number of ready units meets or exceeds planned unit count for the app.
            False otherwise.
        """
        rel = self._get_relation_checked()
        planned = self.model.app.planned_units()

        ready = 0
        # self unit is not in rel.units. Include here to ensure all units are counted
        for unit in (self.model.unit, *rel.units):
            if self.get_unit_data(unit).ready:
                ready += 1

        _logger.debug("ready units: %d, planned units: %d", ready, planned)
        return ready >= planned

    def _get_relation_checked(self) -> ops.Relation:
        """Return the peer relation, ensuring it exists.

        Raises:
            LustrePeerError: If the peer relation does not exist.
        """
        rel = self.model.get_relation(PEER_RELATION)
        if rel is None:
            raise LustrePeerError("Peer relation not yet created")
        return rel

    def _promote_mgs_nids(self, unit_name: str, mgs_nids: list[str]) -> None:
        """Promote MGS NIDs from unit data to app data. Leader-only.

        Never overwrites: the original MGS unit must remain stable across
        leader re-elections.

        Args:
            unit_name: Name of the unit running the MGS.
            mgs_nids: The MGS NIDs published by that unit.

        Raises:
            LustrePeerDuplicateMgsError: If another unit is already the assigned MGS.
            LustrePeerError: If this unit's NIDs have changed since promotion.
        """
        data = self.get_app_data()
        if data.mgs_unit_name and data.mgs_nids:
            if data.mgs_unit_name == unit_name:
                if sorted(data.mgs_nids) != sorted(mgs_nids):
                    raise LustrePeerError(
                        f"MGS NIDs changed for unit {unit_name}: {data.mgs_nids} -> {mgs_nids}"
                    )
                return  # Same unit re-publishing. Idempotent.
            raise LustrePeerDuplicateMgsError(
                f"Unit {unit_name} attempted to publish MGS NIDs. Unit {data.mgs_unit_name} is already the MGS. Multiple MGSes are not supported."
            )

        data.mgs_nids = mgs_nids
        data.mgs_unit_name = unit_name
        self.set_app_data(data)
        _logger.info("promoted MGS NIDs %s from unit %s to app data", mgs_nids, unit_name)

    def _try_oss_setup(self, data: LustrePeerAppData) -> bool:
        """Set up OSS services on this unit if it is not the MGS+MDS unit.

        Returns:
            True to continue processing the event, False to stop.
        """
        # OSS service must not be enabled on MGS+MDS unit
        if self.model.unit.name == data.mgs_unit_name:
            return True

        try:
            devices = sorted([str(s.location) for s in self.model.storages[OST_STORAGE]])
        except ops.model.ModelError as e:
            # Device not provisioned yet, such as after a reboot.
            _logger.warning("OST storage not yet provisioned: %s", e)
            return False

        if not devices:
            _logger.warning("no OST storage attached. cannot configure OSS services.")
            return False

        try:
            lustre_fs.oss_setup(LUSTRE_FSNAME, self.model.unit.name, data.mgs_nids, devices)
        except LustreFilesystemError as e:
            _logger.exception("failed to set up OSS: %s", e)
            self.model.unit.status = ops.BlockedStatus(_LustrePeerStatus.FAILED_OSS_SETUP)
            return False

        return True

    def _try_promote_mgs(self, event: ops.RelationChangedEvent) -> bool:
        """Promote MGS NIDs to app data if leader and the event came from a peer unit.

        Returns:
            True to continue processing the event, False to stop.
        """
        if not (self.model.unit.is_leader() and event.unit is not None):
            # event.unit is None when the application databag changed (e.g. the
            # leader's own promotion write), in which case there is nothing to promote.
            return True

        try:
            unit_data = self.get_unit_data(event.unit)
            if unit_data.mgs_nids:
                self._promote_mgs_nids(event.unit.name, unit_data.mgs_nids)
        except LustrePeerDuplicateMgsError as e:
            _logger.exception("multiple units attempting to run MGS+MDS: %s", e)
            self.model.unit.status = ops.BlockedStatus(_LustrePeerStatus.MULTIPLE_MGS_UNITS)
            return False
        except LustrePeerError as e:
            _logger.exception("failed to promote MGS NIDs: %s", e)
            return False

        return True

    def _try_publish_filesystem_info(self, mgs_nids: list[str], fs_name: str) -> None:
        """Publish Lustre info to the filesystem relation only if all units in the cluster are ready."""
        if not self._all_units_ready():
            _logger.debug("not all units ready yet, waiting to set filesystem info")
            return

        _logger.info("all units report ready, publishing filesystem info")
        self._charm.filesystem.set_info(LustreInfo(mgs_ids=mgs_nids, fs_name=fs_name))
