#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Lustre filesystem operations."""

import logging
import subprocess
from pathlib import Path

from constants import (
    LUSTRE_MGS_MDT_DATASET_PREFIX,
    LUSTRE_MGS_MDT_MOUNTPOINT,
    LUSTRE_OST_DATASET_PREFIX,
    LUSTRE_OST_PARENT_DIRECTORY,
    MKFS_LUSTRE_EXECUTABLE,
    MOUNT_EXECUTABLE,
    TRUNCATE_EXECUTABLE,
    ZFS_EXECUTABLE,
    ZPOOL_EXECUTABLE,
)
from errors import LustreFilesystemError

_logger = logging.getLogger(__name__)


def mgs_mds_setup(fsname: str) -> None:
    """Set up the MGT and MDT on this unit. Idempotent.

    Args:
        fsname: Lustre filesystem name.

    Raises:
        LustreFilesystemError: If MGS+MDS setup fails.
    """
    dataset = f"{LUSTRE_MGS_MDT_DATASET_PREFIX}0"
    pool = f"{fsname}-{dataset}-pool"

    _logger.info(
        "Ensuring this unit is running MGS+MDS on pool '%s' and dataset '%s'", pool, dataset
    )

    devices = _detect_devices(pool)

    try:
        _mgt_mdt_zpool(pool, devices)
    except ValueError as e:
        raise LustreFilesystemError(
            f"Failed to create MGS+MDS zpool '{pool}' with devices {devices}"
        ) from e

    _lustre_target(fsname, pool, dataset, 0, mkfs_flags=["--mgs", "--mdt"])
    _mount(pool, dataset, LUSTRE_MGS_MDT_MOUNTPOINT)

    _logger.info("MGS+MDS on pool '%s' and dataset '%s' ready", pool, dataset)


def oss_setup(fsname: str, unit_name: str, mgs_nids: list[str]) -> None:
    """Set up an OSS on this unit. Idempotent.

    Args:
        fsname: Lustre filesystem name.
        unit_name: Name of this unit.
        mgs_nids: MGS NIDs to use for this OSS.

    Raises:
        LustreFilesystemError: If OSS setup fails.
    """
    # Derive OST index from unit name and a fixed stride.
    # TODO confirm appropriate number of disks per vdev, vdev type(s) (mirror/RAIDZ2), vdevs per
    # pool, and pools per OSS (zpools and OSTs are 1:1). See:
    # https://wiki.lustre.org/ZFS_System_Design
    max_osts_per_oss = 1
    try:
        unit_num = int(unit_name.split("/")[1])
    except (IndexError, ValueError) as e:
        raise LustreFilesystemError(
            f"Failed to parse unit number from unit name '{unit_name}'"
        ) from e
    ost_index = unit_num * max_osts_per_oss  # + ost_num

    dataset = f"{LUSTRE_OST_DATASET_PREFIX}{ost_index}"
    pool = f"{fsname}-{dataset}-pool"

    mgs_nids_str = ",".join(mgs_nids)
    _logger.info(
        "Ensuring this unit is running OSS on pool '%s' and dataset '%s' with MGS NIDs: '%s'",
        pool,
        dataset,
        mgs_nids_str,
    )

    devices = _detect_devices(pool)

    try:
        _ost_zpool(pool, devices)
    except ValueError as e:
        raise LustreFilesystemError(
            f"Failed to create OST zpool '{pool}' with devices {devices}"
        ) from e

    _lustre_target(
        fsname, pool, dataset, ost_index, mkfs_flags=["--ost", f"--mgsnode={mgs_nids_str}"]
    )
    _mount(pool, dataset, Path(LUSTRE_OST_PARENT_DIRECTORY) / dataset)

    _logger.info("OST index '%s' for MGS NIDs '%s' ready", ost_index, mgs_nids_str)


def _detect_devices(owner: str) -> list[str]:
    """Detect available block devices for use in pools. Placeholder for actual device detection logic.

    Args:
        owner: The owner ZFS pool name, used to generate file names for the temporary image files
        used as block devices.

    Returns:
        A list of device path strings to be used for zpool creation.

    Raises:
        LustreFilesystemError: If device detection fails.
    """
    # TODO: For MVP, return a fixed list of image files as block devices. Replace with actual device detection in production.
    devices = []
    for num in range(4):
        image = Path(f"/root/{owner}-disk{num}.img")
        if not image.exists():
            try:
                subprocess.run([TRUNCATE_EXECUTABLE, "-s", "1G", image], check=True)
            except (subprocess.CalledProcessError, FileNotFoundError) as e:
                raise LustreFilesystemError(f"Failed to create image file {image}") from e
        devices.append(str(image))

    return devices


def _mgt_mdt_zpool(pool: str, devices: list[str]) -> None:
    """Create a zpool composed of mirror vdevs for the MGT and MDT. Idempotent.

    Args:
        pool: Name of the zpool to create.
        devices: List of device paths to use for the zpool.

    Raises:
        LustreFilesystemError: If zpool creation fails.
    """
    if _pool_exists(pool):
        _logger.info("ZFS pool '%s' already exists. Skipping creation.", pool)
        return

    if len(devices) < 2:
        raise ValueError("MGT/MDT mirror pool requires at least 2 devices.")
    if len(devices) % 2 != 0:
        raise ValueError("MGT/MDT mirror pool requires an even number of devices for mirroring.")

    cmd = [ZPOOL_EXECUTABLE, "create", "-O", "canmount=off", pool]

    # Break devices into mirror pairs. Example: ["/dev/sda", "/dev/sdb", "/dev/sdc", "/dev/sdd"]
    # becomes ["mirror", "/dev/sda", "/dev/sdb", "mirror", "/dev/sdc", "/dev/sdd"]
    for i in range(0, len(devices), 2):
        pair = devices[i : i + 2]
        cmd.extend(["mirror", pair[0], pair[1]])

    _logger.info("Creating MGT/MDT zpool with command: %s", " ".join(cmd))
    try:
        subprocess.run(cmd, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        raise LustreFilesystemError(f"Failed to create MGT/MDT zpool '{pool}'") from e


def _ost_zpool(pool: str, devices: list[str]) -> None:
    """Create a zpool composed of a RAIDZ2 vdev for OSTs. Idempotent.

    Args:
        pool: Name of the zpool to create.
        devices: List of device paths to use for the zpool.

    Raises:
        LustreFilesystemError: If zpool creation fails.
    """
    if _pool_exists(pool):
        _logger.info("ZFS pool '%s' already exists. Skipping creation.", pool)
        return

    if len(devices) < 3:
        raise ValueError("OST pool requires at least 3 devices for RAIDZ2.")

    cmd = [ZPOOL_EXECUTABLE, "create", "-O", "canmount=off", pool, "raidz2"] + devices

    _logger.info("Creating OST zpool with command: %s", " ".join(cmd))
    try:
        subprocess.run(cmd, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        raise LustreFilesystemError(f"Failed to create OST zpool '{pool}'") from e


def _lustre_target(
    fsname: str,
    pool: str,
    dataset: str,
    index: int,
    mkfs_flags: list[str],
) -> None:
    """Format a Lustre target on top of an existing ZFS pool. Idempotent.

    Args:
        fsname: Name of the Lustre filesystem.
        pool: Name of the ZFS pool.
        dataset: Name of the dataset within the pool.
        index: Index of the target.
        mkfs_flags: List of flags to pass to mkfs.lustre.

    Raises:
        LustreFilesystemError: If formatting the Lustre target fails.
    """
    full_dataset_name = f"{pool}/{dataset}"

    if _target_exists(full_dataset_name):
        _logger.info("Target %s already exists, skipping creation", full_dataset_name)
        return

    _logger.info("Formatting Lustre target: %s", full_dataset_name)

    flags = [
        *mkfs_flags,
        "--backfstype=zfs",
        f"--fsname={fsname}",
        f"--index={index}",
    ]
    try:
        subprocess.run([MKFS_LUSTRE_EXECUTABLE, *flags, full_dataset_name], check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        raise LustreFilesystemError(f"Failed to format Lustre target {full_dataset_name}") from e


def _mount(pool: str, dataset: str, mountpoint: Path) -> None:
    """Mount a ZFS Lustre target at mountpoint. Idempotent.

    Args:
        pool: Name of the ZFS pool.
        dataset: Name of the dataset within the pool.
        mountpoint: Path to mount the dataset.

    Raises:
        LustreFilesystemError: If mounting the Lustre target fails.
    """
    if mountpoint.is_mount():
        _logger.info("%s already mounted, skipping mount attempt", mountpoint)
        return

    full_dataset_name = f"{pool}/{dataset}"
    mountpoint.mkdir(parents=True, exist_ok=True)
    _logger.info("Mounting %s at %s", full_dataset_name, mountpoint)
    # TODO: determine whether a timeout should be set here.
    # See: https://wiki.lustre.org/images/5/59/LUG2025-Lustre_Timeout_Hierarchy-Horn.pdf
    try:
        subprocess.run(
            [MOUNT_EXECUTABLE, "-t", "lustre", full_dataset_name, str(mountpoint)], check=True
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        raise LustreFilesystemError(
            f"Failed to mount Lustre target {full_dataset_name} at {mountpoint}"
        ) from e


def _pool_exists(pool: str) -> bool:
    """Return True if a zpool with the given name already exists, False otherwise.

    Args:
        pool: Name of the zpool to check.

    Raises:
        LustreFilesystemError: If checking the zpool existence fails.
    """
    try:
        return (
            subprocess.run([ZPOOL_EXECUTABLE, "list", pool], capture_output=True).returncode == 0
        )
    except FileNotFoundError as e:
        raise LustreFilesystemError(f"Failed to check zpool '{pool}' existence") from e


def _target_exists(full_dataset_name: str) -> bool:
    """Return True if a ZFS dataset with the given name already exists.

    Args:
        full_dataset_name: Name of the ZFS dataset to check.

    Raises:
        LustreFilesystemError: If checking the zpool existence fails.
    """
    try:
        result = subprocess.run(
            [ZFS_EXECUTABLE, "get", "-H", "-o", "value", "lustre:fsname", full_dataset_name],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0 and result.stdout.strip() != "-"
    except FileNotFoundError as e:
        raise LustreFilesystemError(
            f"Failed to check ZFS dataset '{full_dataset_name}' existence"
        ) from e
