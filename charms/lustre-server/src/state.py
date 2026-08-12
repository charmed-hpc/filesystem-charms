#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Check the state of the Lustre charmed operator."""

import logging
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

import ops
from constants import (
    LUSTRE_MGS_MDT_MOUNTPOINT,
    LUSTRE_OST_DATASET_PREFIX,
    LUSTRE_OST_PARENT_DIRECTORY,
)
from errors import LustrePeerError, LustreStateError

if TYPE_CHECKING:
    from charm import LustreCharm
    from lustre_peer import LustrePeerAppData

_logger = logging.getLogger(__name__)


class CharmStatuses(StrEnum):
    """Charm status messages."""

    WAITING_PEER_DATA = "Waiting for MGS unit to publish NID"
    FAILED_PEER_DATA = "Failed to get peer relation app data"
    MGS_MDS_READY = "MGS+MDS ready"
    OSS_READY = "OSS ready"

    _MODULES_MISSING_TEMPLATE = "Kernel module(s) not loaded: {modules}"
    _MODULES_PATH_FAILURE_TEMPLATE = "Failed to access path: {modules_path}"
    _MOUNTPOINT_MISSING_TEMPLATE = "{mountpoint} does not exist"
    _MOUNTPOINT_NOT_MOUNTED_TEMPLATE = "{mountpoint} is not mounted"
    _OSTS_MISSING_TEMPLATE = "No OST mountpoints found in {mount_directory}"

    @classmethod
    def modules_missing(cls, modules: list[str]) -> str:
        """Format the kernel modules missing message."""
        return cls._MODULES_MISSING_TEMPLATE.format(modules=", ".join(modules))

    @classmethod
    def modules_path_failure(cls, modules_path: str) -> str:
        """Format the modules path failure message."""
        return cls._MODULES_PATH_FAILURE_TEMPLATE.format(modules_path=modules_path)

    @classmethod
    def mountpoint_missing(cls, mountpoint: Path) -> str:
        """Format the mountpoint missing message."""
        return cls._MOUNTPOINT_MISSING_TEMPLATE.format(mountpoint=mountpoint)

    @classmethod
    def mountpoint_not_mounted(cls, mountpoint: Path) -> str:
        """Format the mountpoint not mounted message."""
        return cls._MOUNTPOINT_NOT_MOUNTED_TEMPLATE.format(mountpoint=mountpoint)

    @classmethod
    def osts_missing(cls, mount_directory: str) -> str:
        """Format the OSTs missing message."""
        return cls._OSTS_MISSING_TEMPLATE.format(mount_directory=mount_directory)


def check_lustre(charm: "LustreCharm") -> ops.StatusBase:
    """Check health of Lustre services and update unit status.

    Args:
        charm: The LustreCharm instance.

    Returns:
        The updated unit status.

    Raises:
        LustreStateError: If any health check fails.
    """
    # If any health check fails and unit is already in BlockedStatus, return the existing status.
    # Avoids overwriting an existing error.
    existing_error_status = None
    if isinstance(charm.unit.status, ops.BlockedStatus):
        existing_error_status = charm.unit.status

    try:
        peer_app_data = charm.peers.get_app_data()
    except LustrePeerError as e:
        _logger.exception("failed to get peer relation app data: %s", e)
        return existing_error_status or ops.BlockedStatus(CharmStatuses.FAILED_PEER_DATA)

    # Must determine whether MGS+MDS or OSS-specific checks should be performed.
    if charm.model.unit.name == peer_app_data.mgs_unit_name:
        role_specific_check = _mgs_mds_check
        active_status = ops.ActiveStatus(CharmStatuses.MGS_MDS_READY)
    else:
        role_specific_check = _oss_check
        active_status = ops.ActiveStatus(CharmStatuses.OSS_READY)

    try:
        _common_check(peer_app_data)
        role_specific_check()
    except LustreStateError as e:
        return existing_error_status or e.status

    if existing_error_status:
        _logger.info("all health checks pass. clearing error status: %s", existing_error_status)

    return active_status


def _common_check(peer_app_data: "LustrePeerAppData") -> None:
    """Perform checks common to all Lustre units.

    Args:
        peer_app_data: The peer relation application data.
    """
    _peer_relation_app_data_check(peer_app_data)
    _kernel_modules_check()


def _peer_relation_app_data_check(data: "LustrePeerAppData") -> None:
    """Check if MGS peer app data has been published.

    Args:
        data: The peer relation application data.

    Raises:
        LustreStateError: If the MGS peer app data has not been published.
    """
    if data.mgs_unit_name is None or not data.mgs_nids:
        status = ops.WaitingStatus(CharmStatuses.WAITING_PEER_DATA)
        raise LustreStateError(status)


def _kernel_modules_check(modules_path: str = "/proc/modules") -> None:
    """Check if Lustre and LNet kernel modules are installed on the unit.

    Args:
        modules_path: The path to the file containing loaded kernel modules.

    Raises:
        LustreStateError: If any required kernel module is missing.
    """
    required_modules = {"lustre", "lnet"}
    loaded_modules = set()
    try:
        with open(modules_path, "r") as f:
            for line in f:
                # First item on each line is module name
                split_line = line.split()
                if split_line:
                    loaded_modules.add(split_line[0])
    except OSError as e:
        _logger.exception("OS error: %s", e)
        status = ops.BlockedStatus(CharmStatuses.modules_path_failure(modules_path))
        raise LustreStateError(status)

    missing = required_modules - loaded_modules
    if missing:
        # `sorted()` ensures consistent ordering of module names in the status message.
        status = ops.BlockedStatus(CharmStatuses.modules_missing(sorted(missing)))
        raise LustreStateError(status)


def _mountpoint_check(mountpoint: Path) -> None:
    """Check if a mountpoint is healthy.

    Args:
        mountpoint: The path to the mountpoint.

    Raises:
        LustreStateError: If the mountpoint is missing or not mounted.
    """
    if not mountpoint.exists():
        status = ops.BlockedStatus(CharmStatuses.mountpoint_missing(mountpoint))
        raise LustreStateError(status)
    if not mountpoint.is_mount():
        status = ops.BlockedStatus(CharmStatuses.mountpoint_not_mounted(mountpoint))
        raise LustreStateError(status)


def _mgs_mds_check() -> None:
    """Perform MGS+MDS unit checks."""
    _mountpoint_check(LUSTRE_MGS_MDT_MOUNTPOINT)


def _oss_check(mount_directory: str = LUSTRE_OST_PARENT_DIRECTORY) -> None:
    """Perform OSS unit checks.

    Args:
        mount_directory: The directory containing OST mountpoints.

    Raises:
        LustreStateError: If any OST mountpoint is missing or not mounted.
    """
    # Account for multiple OSTs. Example: /mnt/ost0, /mnt/ost1, etc.
    osts = list(Path(mount_directory).glob(f"{LUSTRE_OST_DATASET_PREFIX}[0-9]*"))
    if len(osts) == 0:
        _logger.error("no OST mountpoints found in %s", mount_directory)
        status = ops.BlockedStatus(CharmStatuses.osts_missing(mount_directory=mount_directory))
        raise LustreStateError(status)

    for mountpoint in osts:
        _mountpoint_check(mountpoint)
