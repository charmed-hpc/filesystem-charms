#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Peer relation observer for the Lustre charm."""

import json
import logging
from enum import StrEnum
from typing import TYPE_CHECKING

import lustre_fs
import ops
import pydantic
from charms.filesystem_client.v0.filesystem_info import LustreInfo
from constants import LUSTRE_FSNAME
from errors import LustreFilesystemError, LustrePeerError
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
    """

    ready: bool = pydantic.Field(
        default=False, description="Whether this unit has completed Lustre service setup."
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
        """Publish this unit as the MGS if no MGS has been assigned yet.

        Returns:
            The published MGS NID strings.

        Raises:
            LustrePeerError: If an error occurs publishing the MGS NIDs.
        """
        if not self.model.unit.is_leader():
            raise LustrePeerError("Non-leader attempted to publish MGS NID")

        # Never overwrite. The original MGS unit must remain stable across leader re-elections.
        data = self.get_app_data()
        if data.mgs_unit_name and data.mgs_nids:
            _logger.info(
                "MGS already active on %s with NIDs %s. skipping publication",
                data.mgs_unit_name,
                data.mgs_nids,
            )
            return data.mgs_nids

        try:
            mgs_nids = lnet.get_nids()
        except LNetError as e:
            raise LustrePeerError("Failed to determine MGS NID") from e

        if not mgs_nids:
            raise LustrePeerError("No LNet NIDs configured on this unit")

        data.mgs_nids = mgs_nids
        data.mgs_unit_name = self.model.unit.name

        self._set_unit_ready()
        self.set_app_data(data)
        self._try_publish_filesystem_info(mgs_nids, LUSTRE_FSNAME)
        _logger.info("Published MGS NIDs %s for unit %s", data.mgs_nids, data.mgs_unit_name)
        return data.mgs_nids

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

        # Workaround for https://github.com/canonical/operator/issues/2591
        # Custom decoder needed to prevent rel.load() from raising JSONDecodeError attempting to
        # decode IP addresses included by default in unit data, example: {'ingress-address':
        # '10.200.245.189'}.
        def _decoder(value: str) -> str:
            if not (value.startswith('"') and value.endswith('"')):
                value = f'"{value}"'
            return json.loads(value)

        return rel.load(LustrePeerUnitData, unit, decoder=_decoder) or LustrePeerUnitData()

    def set_unit_data(self, data: LustrePeerUnitData, unit: ops.Unit | None = None) -> None:
        """Set the unit data in the peer relation databag.

        Args:
            data: The data to write.
            unit: The unit whose data to write. Defaults to this unit.
        """
        rel = self._get_relation_checked()
        unit = unit or self.model.unit
        rel.save(data, unit)

    def _on_relation_changed(self, _: ops.RelationChangedEvent) -> None:
        """Handle the peer relation changed event."""
        try:
            data = self.get_app_data()
        except LustrePeerError as e:
            _logger.warning("Failed to get peer relation data: %s", e)
            return

        if data.mgs_unit_name is None or not data.mgs_nids:
            _logger.warning("MGS data not yet published. cannot configure Lustre services.")
            return

        # OSS service must not be enabled on MGS+MDS unit
        if self.model.unit.name != data.mgs_unit_name:
            try:
                lustre_fs.oss_setup(LUSTRE_FSNAME, self.model.unit.name, data.mgs_nids)
            except LustreFilesystemError as e:
                _logger.exception("failed to set up OSS: %s", e)
                self.model.unit.status = ops.BlockedStatus(_LustrePeerStatus.FAILED_OSS_SETUP)
                return

        try:
            self._set_unit_ready()
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

    def _set_unit_ready(self) -> None:
        """Set calling unit as ready in its unit data."""
        data = self.get_unit_data()
        data.ready = True
        self.set_unit_data(data)

    def _try_publish_filesystem_info(self, mgs_nids: list[str], fs_name: str) -> None:
        """Publish Lustre info to the filesystem relation only if all units in the cluster are ready."""
        if not self._all_units_ready():
            _logger.debug("not all units ready yet, waiting to set filesystem info")
            return

        _logger.info("all units report ready, publishing filesystem info")
        self._charm.filesystem.set_info(LustreInfo(mgs_ids=mgs_nids, fs_name=fs_name))
