# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manage machine mounts and dependencies."""

import logging
import os
import pathlib
import shutil
import subprocess
from dataclasses import dataclass
from ipaddress import AddressValueError, IPv6Address
from typing import Iterator, List, Optional, Union

import charms.operator_libs_linux.v0.apt as apt
import charms.operator_libs_linux.v1.systemd as systemd
from charms.filesystem_client.v0.interfaces import CephfsInfo, FsInfo, NfsInfo

_logger = logging.getLogger(__name__)


class Error(Exception):
    """Raise if Storage client manager encounters an error."""

    @property
    def name(self):
        """Get a string representation of the error plus class name."""
        return f"<{type(self).__module__}.{type(self).__name__}>"

    @property
    def message(self):
        """Return the message passed as an argument."""
        return self.args[0]

    def __repr__(self):
        """Return the string representation of the error."""
        return f"<{type(self).__module__}.{type(self).__name__} {self.args}>"


@dataclass(frozen=True)
class MountInfo:
    """Mount information.

    Notes:
        See `man fstab` for description of field types.
    """

    endpoint: str
    mountpoint: str
    fstype: str
    options: str
    freq: str
    passno: str


class MountsManager:
    """Manager for mounted filesystems in the current system."""

    def __init__(self):
        # Lazily initialized
        self._pkgs = None

    @property
    def _packages(self) -> List[apt.DebianPackage]:
        if not self._pkgs:
            self._pkgs = [
                apt.DebianPackage.from_system(pkg)
                for pkg in ["ceph-common", "nfs-common", "autofs"]
            ]
        return self._pkgs

    @property
    def installed(self) -> bool:
        """Check if the required packages are installed."""
        for pkg in self._packages:
            if not pkg.present:
                return False
        return True

    def ensure(self, state: apt.PackageState) -> None:
        """Ensure that the mount packages are in the specified state.

        Raises:
            Error: Raised if this failed to change the state of any of the required packages.
        """
        try:
            for pkg in self._packages:
                pkg.ensure(state)
        except (apt.PackageError, apt.PackageNotFoundError) as e:
            _logger.error(
                f"failed to change the state of the required packages. Reason:\n{e.message}"
            )
            raise Error(e.message)

    def supported(self) -> bool:
        """Check if underlying base supports mounting shares."""
        try:
            result = subprocess.run(
                ["systemd-detect-virt"], stdout=subprocess.PIPE, check=True, text=True
            )
            if "lxc" in result.stdout:
                # Cannot mount shares inside LXD containers.
                return False
            else:
                return True
        except subprocess.CalledProcessError:
            _logger.warning("Could not detect execution in virtualized environment")
            return True

    def fetch(self, target: str) -> Optional[MountInfo]:
        """Fetch information about a mount.

        Args:
            target: share mountpoint information to fetch.

        Returns:
            Optional[MountInfo]: Mount information. None if share is not mounted.
        """
        # We need to trigger an automount for the mounts that are of type `autofs`,
        # since those could contain an unlisted mount.
        _trigger_autofs()

        for mount in _mounts():
            if mount.mountpoint == target:
                return mount

        return None

    def mounts(self) -> List[MountInfo]:
        """Get all mounts on a machine.

        Returns:
            List[MountInfo]: All current mounts on machine.
        """
        _trigger_autofs()

        return list(_mounts("autofs"))

    def mounted(self, target: str) -> bool:
        """Determine if mountpoint is mounted.

        Args:
            target: share mountpoint to check.
        """
        return self.fetch(target) is not None

    def mount(
        self,
        share_info: FsInfo,
        mountpoint: Union[str, os.PathLike],
        options: Optional[List[str]] = None,
    ) -> None:
        """Mount a share.

        Args:
            share_info: Share information required to mount the share.
            mountpoint: System location to mount the share.
            options: Mount options to pass when mounting the share.

        Raises:
            Error: Raised if the mount operation fails.
        """
        if options is None:
            options = []
        # Try to create the mountpoint without checking if it exists to avoid TOCTOU.
        target = pathlib.Path(mountpoint)
        try:
            target.mkdir()
            _logger.debug(f"Created mountpoint {mountpoint}.")
        except FileExistsError:
            _logger.warning(f"Mountpoint {mountpoint} already exists.")

        endpoint, additional_opts = _get_endpoint_and_opts(share_info)
        options = options + additional_opts

        _logger.debug(f"Mounting share {endpoint} at {target}")
        autofs_id = _mountpoint_to_autofs_id(target)
        pathlib.Path(f"/etc/auto.master.d/{autofs_id}.autofs").write_text(
            f"/- /etc/auto.{autofs_id}"
        )
        pathlib.Path(f"/etc/auto.{autofs_id}").write_text(
            f"{target} -{','.join(options)} {endpoint}"
        )

        try:
            systemd.service_reload("autofs", restart_on_failure=True)
        except systemd.SystemdError as e:
            _logger.error(f"Failed to mount {endpoint} at {target}. Reason:\n{e}")
            if "Operation not permitted" in str(e) and not self.supported():
                raise Error("Mounting shares not supported on LXD containers")
            raise Error(f"Failed to mount {endpoint} at {target}")

    def umount(self, mountpoint: Union[str, os.PathLike]) -> None:
        """Unmount a share.

        Args:
            mountpoint: share mountpoint to unmount.

        Raises:
            Error: Raised if the unmount operation fails.
        """
        _logger.debug(f"Unmounting share at mountpoint {mountpoint}")
        autofs_id = _mountpoint_to_autofs_id(mountpoint)
        pathlib.Path(f"/etc/auto.{autofs_id}").unlink(missing_ok=True)
        pathlib.Path(f"/etc/auto.master.d/{autofs_id}.autofs").unlink(missing_ok=True)

        try:
            systemd.service_reload("autofs", restart_on_failure=True)
        except systemd.SystemdError as e:
            _logger.error(f"Failed to unmount {mountpoint}. Reason:\n{e}")
            raise Error(f"Failed to unmount {mountpoint}")

        shutil.rmtree(mountpoint, ignore_errors=True)


def _trigger_autofs() -> None:
    """Triggers a mount on all filesystems handled by autofs.

    This function is useful to make autofs-managed mounts appear on the
    `/proc/mount` file, since they could be unmounted when reading the file.
    """
    for fs in _mounts("autofs"):
        _logger.info(f"triggering automount for `{fs.mountpoint}`")
        try:
            os.scandir(fs.mountpoint).close()
        except OSError as e:
            # Not critical since it could also be caused by unrelated mounts,
            # but should be good to log it in case this causes problems.
            _logger.warning(f"Could not trigger automount for `{fs.mountpoint}`. Reason:\n{e}")


def _mountpoint_to_autofs_id(mountpoint: Union[str, os.PathLike]) -> str:
    """Get the autofs id of a mountpoint path.

    Args:
        mountpoint: share mountpoint.
    """
    path = pathlib.Path(mountpoint).resolve()
    return str(path).lstrip("/").replace("/", "-")


def _mounts(fstype: str = "") -> Iterator[MountInfo]:
    """Get an iterator of all mounts in the system that have the requested fstype.

    Returns:
        Iterator[MountInfo]: All the mounts with a valid fstype.
    """
    with pathlib.Path("/proc/mounts").open("rt") as mounts:
        for mount in mounts:
            # Lines in /proc/mounts follow the standard format
            # <endpoint> <mountpoint> <fstype> <options> <freq> <passno>
            m = MountInfo(*mount.split())
            if fstype and not m.fstype.startswith(fstype):
                continue

            yield m


def _get_endpoint_and_opts(info: FsInfo) -> tuple[str, [str]]:
    match info:
        case NfsInfo(hostname=hostname, port=port, path=path):
            try:
                IPv6Address(hostname)
                # Need to add brackets if the hostname is IPv6
                hostname = f"[{hostname}]"
            except AddressValueError:
                pass

            endpoint = f"{hostname}:{path}"
            options = [f"port={port}"] if port else []
        case CephfsInfo(
            fsid=fsid, name=name, path=path, monitor_hosts=mons, user=user, key=secret
        ):
            mon_addr = "/".join(mons)
            endpoint = f"{user}@{fsid}.{name}={path}"
            options = [
                "fstype=ceph",
                f"mon_addr={mon_addr}",
                f"secret={secret}",
            ]
        case _:
            raise Error(f"unsupported filesystem type `{info.fs_type()}`")

    return endpoint, options
