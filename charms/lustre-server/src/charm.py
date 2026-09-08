#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm for the Lustre file system."""

import logging
from enum import StrEnum

import lustre_fs
import ops
from charmed_hpc_libs.ops import StopCharm, refresh
from charmlibs import apt
from charms.filesystem_client.v0.filesystem_info import FilesystemProvides
from config import LustreConfig
from constants import (
    FILESYSTEM_PEER_RELATION,
    FILESYSTEM_RELATION,
    LUSTRE_FSNAME,
    LUSTRE_PACKAGES,
    MGT_MDT_STORAGE,
    OST_STORAGE,
)
from errors import (
    LustreFilesystemError,
    LustrePeerDuplicateMgsError,
    LustrePeerError,
)
from lustre_ops import lnet, ppa
from lustre_ops.errors import LNetError, RepositoryError
from lustre_peer import LustrePeerAppData, LustrePeerObserver
from state import check_lustre

logger = logging.getLogger(__name__)
refresh_check_lustre = refresh(hook=check_lustre)


class _CharmStatus(StrEnum):
    """Charm status messages."""

    REPO_SETUP = "Setting up package repository"
    FAILED_REPO_SETUP = "Failed to set up Lustre package repository"
    PACKAGE_INSTALL = "Installing Lustre packages"
    LNET_INIT = "Initializing LNet"
    FAILED_LNET_INIT = "LNet initialization failed"
    PREPARING_SERVICES = "Preparing to start Lustre services"
    STARTING_SERVICES = "Starting Lustre services"
    FAILED_PEER_DATA = "Failed to get peer relation app data"
    FAILED_MGS_MDS_SETUP = "Failed to set up MGS+MDS"
    FAILED_OSS_SETUP = "Failed to set up OSS"
    MULTIPLE_MGS_UNITS = "Cluster error: multiple units have MGT+MDT storage attached"
    WAITING_FOR_STORAGE = "Waiting for storage to be provisioned"
    DUPLICATE_STORAGE_ERROR = (
        f"Storage '{MGT_MDT_STORAGE}' and '{OST_STORAGE}' cannot be attached to the same unit"
    )
    NO_STORAGE_ATTACHED = (
        f"No storage attached. Add '{MGT_MDT_STORAGE}' or '{OST_STORAGE}' to this unit"
    )

    _FAILED_INSTALL_TEMPLATE = "Failed to install packages: {packages}"

    @classmethod
    def failed_install(cls, packages: list[str]) -> str:
        """Format a package installation failure message.

        Args:
            packages: List of package names that failed installation.

        Returns:
            A formatted status string containing the failed packages.
        """
        return cls._FAILED_INSTALL_TEMPLATE.format(packages=packages)


class LustreCharm(ops.CharmBase):
    """Charm for the Lustre file system."""

    def __init__(self, framework: ops.Framework):
        """Initialize the Lustre charm and event observers."""
        super().__init__(framework)
        self.typed_config = self.load_config(LustreConfig, errors="blocked")
        self.filesystem = FilesystemProvides(self, FILESYSTEM_RELATION, FILESYSTEM_PEER_RELATION)
        self.peers = LustrePeerObserver(self)
        framework.observe(self.on.install, self._on_install)
        framework.observe(self.on.start, self._on_start)
        framework.observe(self.on.update_status, self._on_update_status)
        for name in (MGT_MDT_STORAGE, OST_STORAGE):
            framework.observe(self.on[name].storage_attached, self._on_start)

    def _on_install(self, _: ops.InstallEvent):
        """Install Lustre packages."""
        # Lustre packages are not in the Ubuntu archive. Add an external repository.
        self.unit.status = ops.MaintenanceStatus(_CharmStatus.REPO_SETUP)
        try:
            ppa.setup_lustre_repository()
        except RepositoryError as e:
            logger.exception("failed to set up Lustre package repository: %s", e)
            self.unit.status = ops.BlockedStatus(_CharmStatus.FAILED_REPO_SETUP)
            return

        self.unit.status = ops.MaintenanceStatus(_CharmStatus.PACKAGE_INSTALL)
        try:
            apt.add_package(LUSTRE_PACKAGES)
        except (apt.PackageNotFoundError, apt.PackageError) as e:
            logger.exception("failed to install packages: %s. reason: %s", LUSTRE_PACKAGES, e)
            self.unit.status = ops.BlockedStatus(_CharmStatus.failed_install(LUSTRE_PACKAGES))
            return

        self.unit.status = ops.MaintenanceStatus(_CharmStatus.LNET_INIT)
        try:
            networks = lnet.parse_network_config(self.typed_config.lnet_networks)
            lnet.init(networks=networks)
        except LNetError as e:
            logger.exception("failed to initialize LNet: %s", e)
            self.unit.status = ops.BlockedStatus(_CharmStatus.FAILED_LNET_INIT)
            return

        self.unit.status = ops.MaintenanceStatus(_CharmStatus.PREPARING_SERVICES)

    @refresh_check_lustre
    def _on_start(self, _: ops.StartEvent | ops.StorageAttachedEvent) -> None:
        """Set up Lustre services."""
        if not lustre_fs.is_lustre_installed():
            logger.warning("attempted to start services before Lustre packages installed")
            return

        self.unit.status = ops.MaintenanceStatus(_CharmStatus.STARTING_SERVICES)

        try:
            data = self.peers.get_app_data()
        except LustrePeerError as e:
            logger.exception("failed to read peer relation data: %s", e)
            raise StopCharm(ops.BlockedStatus(_CharmStatus.FAILED_PEER_DATA))

        try:
            mgt_mdt_devices = sorted(
                [str(s.location) for s in self.model.storages[MGT_MDT_STORAGE]]
            )
            ost_devices = sorted([str(s.location) for s in self.model.storages[OST_STORAGE]])
        except ops.model.ModelError as e:
            # Storage is registered in the model is not provisioned yet. Can
            # occur when block devices are not yet re-attached after a reboot.
            logger.warning("storage not yet provisioned: %s", e)
            self.unit.status = ops.MaintenanceStatus(_CharmStatus.WAITING_FOR_STORAGE)
            return

        if mgt_mdt_devices and ost_devices:
            raise StopCharm(ops.BlockedStatus(_CharmStatus.DUPLICATE_STORAGE_ERROR))

        if mgt_mdt_devices:
            self._become_mgs_mds(mgt_mdt_devices)
        elif ost_devices:
            self._become_oss(ost_devices, data)
        else:
            raise StopCharm(ops.BlockedStatus(_CharmStatus.NO_STORAGE_ATTACHED))

    def _become_mgs_mds(self, devices: list[str]) -> None:
        """Set up this unit as MGS+MDS. Idempotent."""
        try:
            lustre_fs.mgs_mds_setup(LUSTRE_FSNAME, devices)
            _ = self.peers.mgs_nids_published()
        except LustrePeerDuplicateMgsError as e:
            logger.exception("multiple units attempting to run MGS+MDS: %s", e)
            raise StopCharm(ops.BlockedStatus(_CharmStatus.MULTIPLE_MGS_UNITS))
        except (LustrePeerError, LustreFilesystemError) as e:
            logger.exception("failed to set up MGS+MDS: %s", e)
            raise StopCharm(ops.BlockedStatus(_CharmStatus.FAILED_MGS_MDS_SETUP))

    def _become_oss(self, devices: list[str], data: LustrePeerAppData) -> None:
        """Set up this unit as an OSS. Idempotent."""
        if data.mgs_unit_name is None or not data.mgs_nids:
            # No MGS to configure against yet. Wait for the peer relation to change.
            return

        try:
            lustre_fs.oss_setup(LUSTRE_FSNAME, self.model.unit.name, data.mgs_nids, devices)
            self.peers.set_unit_ready(data.mgs_nids, LUSTRE_FSNAME)
        except (LustrePeerError, LustreFilesystemError) as e:
            logger.exception("failed to set up OSS: %s", e)
            raise StopCharm(ops.BlockedStatus(_CharmStatus.FAILED_OSS_SETUP))

    @refresh_check_lustre
    def _on_update_status(self, _: ops.UpdateStatusEvent) -> None:
        """Check the health of Lustre services and update unit status."""


if __name__ == "__main__":  # pragma: nocover
    ops.main(LustreCharm)
