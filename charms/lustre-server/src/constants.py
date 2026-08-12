#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Constants used within the charm."""

from pathlib import Path

FILESYSTEM_RELATION = "filesystem"
FILESYSTEM_PEER_RELATION = "filesystem-peer"

LUSTRE_FSNAME = "lustrefs"
LUSTRE_MGS_MDT_DATASET_PREFIX = "mgsmdt"
LUSTRE_MGS_MDT_MOUNTPOINT = Path("/mnt/mgs_mdt")
LUSTRE_OST_DATASET_PREFIX = "ost"
LUSTRE_OST_PARENT_DIRECTORY = "/mnt"

# zfsutils-linux is needed but is not an explicit dependency of the Lustre debs.
LUSTRE_PACKAGES = ["lustre-server-modules-dkms", "lustre-server-utils", "zfsutils-linux"]

MKFS_LUSTRE_EXECUTABLE = "/usr/sbin/mkfs.lustre"
MOUNT_EXECUTABLE = "/usr/bin/mount"
TRUNCATE_EXECUTABLE = "/usr/bin/truncate"
ZFS_EXECUTABLE = "/usr/sbin/zfs"
ZPOOL_EXECUTABLE = "/usr/sbin/zpool"
