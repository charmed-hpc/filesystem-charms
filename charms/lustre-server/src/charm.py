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
)
from errors import LustreFilesystemError, LustrePeerError
from lustre_ops import lnet, ppa
from lustre_ops.errors import LNetError, RepositoryError
from lustre_peer import LustrePeerObserver
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
    FAILED_SERVICE_SETUP = "Failed to start Lustre services"

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
    def _on_start(self, _: ops.StartEvent):
        """Set up Lustre services."""
        self.unit.status = ops.MaintenanceStatus(_CharmStatus.STARTING_SERVICES)

        try:
            data = self.peers.get_app_data()
        except LustrePeerError as e:
            logger.exception("failed to read peer relation data: %s", e)
            raise StopCharm(ops.BlockedStatus(_CharmStatus.FAILED_PEER_DATA))

        mgs_unit = data.mgs_unit_name
        mgs_nids = data.mgs_nids

        if mgs_unit is None or not mgs_nids:
            # No MGS has been published yet. This is initial deployment.
            if self.unit.is_leader():
                # Initial leader is MGS+MDS for lifetime of deployment.
                try:
                    lustre_fs.mgs_mds_setup(LUSTRE_FSNAME)
                    self.peers.mgs_nids_published()
                except (LustrePeerError, LustreFilesystemError) as e:
                    logger.exception("failed to set up MGS+MDS: %s", e)
                    raise StopCharm(ops.BlockedStatus(_CharmStatus.FAILED_MGS_MDS_SETUP))

            # Initial non-leaders are OSSes and must wait for leader to publish MGS info in the peer
            # relation before starting.
            return

        # MGS is already published. This is a restart or a slow OSS initial deployment.
        try:
            if self.model.unit.name == mgs_unit:
                lustre_fs.mgs_mds_setup(LUSTRE_FSNAME)
            else:
                # If this is a slow initial deployment, OSS will still need to wait for the peer
                # relation event that marks itself ready before any filesystem info is published.
                lustre_fs.oss_setup(LUSTRE_FSNAME, self.model.unit.name, mgs_nids)
        except LustreFilesystemError as e:
            logger.exception("failed to set up Lustre services: %s", e)
            raise StopCharm(ops.BlockedStatus(_CharmStatus.FAILED_SERVICE_SETUP))

    @refresh_check_lustre
    def _on_update_status(self, _: ops.UpdateStatusEvent) -> None:
        """Check the health of Lustre services and update unit status."""


if __name__ == "__main__":  # pragma: nocover
    ops.main(LustreCharm)
