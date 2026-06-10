# Copyright 2024-2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manage machine mounts and dependencies."""

import contextlib
import json
import logging
import os
import pathlib
import platform
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from ipaddress import AddressValueError, IPv6Address

import ops
from charmed_hpc_libs.errors import SystemdError, UnknownVirtualizationStateError
from charmed_hpc_libs.ops import is_container, systemctl
from charmlibs import apt
from charms.filesystem_client.v0.filesystem_info import (
    CephfsInfo,
    FilesystemInfo,
    LustreInfo,
    NfsInfo,
)

from utils.constants import (
    BASE_PACKAGES,
    LUSTRE_LNET_CONF,
    LUSTRE_PACKAGES,
    LUSTRE_REPOSITORY_KEY,
    LUSTRE_REPOSITORY_URI,
)

_logger = logging.getLogger(__name__)


class Error(Exception):
    """Raise if Storage client manager encounters an error."""

    @property
    def name(self) -> str:
        """Get a string representation of the error plus class name."""
        return f"<{type(self).__module__}.{type(self).__name__}>"

    @property
    def message(self) -> str:
        """Return the message passed as an argument."""
        return self.args[0]

    def __repr__(self) -> str:
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


@dataclass
class _MountInfo:
    endpoint: str
    options: list[str]


class Mounts:
    """Collection of mounts that need to be managed by the `MountsManager`."""

    def __init__(self, enable_lustre: bool) -> None:
        self._mounts: dict[str, _MountInfo] = {}
        self._lustre = enable_lustre

    def add(
        self,
        info: FilesystemInfo,
        mountpoint: str | os.PathLike,
        options: list[str] | None = None,
    ) -> None:
        """Add a mount to the list of managed mounts.

        Args:
            info: Share information required to mount the share.
            enable_lustre: Enable support for mounting Lustre filesystems.
            mountpoint: System location to mount the share.
            options: Mount options to pass when mounting the share.

        Raises:
            Error: Raised if the mount operation fails.
        """
        if options is None:
            options = []

        endpoint, additional_opts = _get_endpoint_and_opts(info, self._lustre)
        options = sorted(options + additional_opts)

        self._mounts[str(mountpoint)] = _MountInfo(endpoint=endpoint, options=options)


class MountsManager:
    """Manager for mounted filesystems in the current system."""

    def __init__(self, charm: ops.CharmBase) -> None:
        unit_id = charm.unit.name.replace("/", "-")
        # Lazily initialized
        self._pkgs = None
        self._master_file = pathlib.Path(f"/etc/auto.master.d/{unit_id}.autofs")
        self._autofs_file = pathlib.Path(f"/etc/auto.{unit_id}")
        self.enable_lustre = False

    def _packages(self) -> list[apt.DebianPackage]:
        """List of packages required by the client."""
        if not self.enable_lustre:
            if self._pkgs:
                return self._pkgs

            self._pkgs = [apt.DebianPackage.from_system(pkg) for pkg in BASE_PACKAGES]

            return self._pkgs

        if self._pkgs and any(pkg.name in LUSTRE_PACKAGES for pkg in self._pkgs):
            return self._pkgs

        repositories = apt.RepositoryMapping()

        try:
            release = platform.freedesktop_os_release()["VERSION_CODENAME"]
        except KeyError as e:
            _logger.error(
                "failed to determine Ubuntu version codename to configure Lustre repository",
                exc_info=e,
            )
            raise Error(str(e))

        try:
            repo = apt.DebianRepository(
                enabled=True,
                repotype="deb",
                uri=LUSTRE_REPOSITORY_URI,
                release=release,
                groups=["main"],
                filename="lustre-repo",
            )
            repo.import_key(LUSTRE_REPOSITORY_KEY)
            # adding the debian repository should have idempotent semantics.
            repositories.add(repo)
            apt.update()
        except (apt.GPGKeyError, subprocess.CalledProcessError) as e:
            _logger.error("failed to add %s package repository", LUSTRE_REPOSITORY_URI, exc_info=e)
            raise Error(str(e))

        self._pkgs = [
            apt.DebianPackage.from_system(pkg) for pkg in BASE_PACKAGES + LUSTRE_PACKAGES
        ]

        return self._pkgs

    def is_setup(self) -> bool:
        """Check if the system is set up ."""
        for pkg in self._packages():
            if not pkg.present:
                return False

        if not self._master_file.exists() or not self._autofs_file.exists():
            return False

        if not self.enable_lustre:
            return True

        return LUSTRE_LNET_CONF.exists()

    def setup(self) -> None:
        """Set up the system to mount filesystems.

        Raises:
            Error: Raised if this failed to set up the system.
        """
        try:
            for pkg in self._packages():
                pkg.ensure(apt.PackageState.Present)
        except (apt.PackageError, apt.PackageNotFoundError) as e:
            _logger.error("failed to change the state of the required packages", exc_info=e)
            raise Error(e.message)

        try:
            self._master_file.touch(mode=0o600)
            self._autofs_file.touch(mode=0o600)
            self._master_file.write_text(f"/- {self._autofs_file}")
        except IOError as e:
            _logger.error("failed to create the required autofs files", exc_info=e)
            raise Error(str(e))

        if not self.enable_lustre:
            return

        # Enable LNet on default network interface if not already enabled.
        interface = _get_default_interface()
        # TODO: Add InfiniBand support. MVP is scoped to TCP for now.
        try:
            result = subprocess.run(["lnetctl", "net", "show", "--net", "tcp"])
            if result.returncode != 0:
                # Command failed to get the tcp network. Need to create it.
                subprocess.run(
                    ["lnetctl", "net", "add", "--net", "tcp", "--if", interface], check=True
                )

            # TODO: might want to check if the tcp network is assigned to the
            # correct interface?
        except subprocess.CalledProcessError as e:
            _logger.error("failed to setup lnet on the system", exc_info=e)
            raise Error(str(e))

        try:
            # Persist changes.
            result = subprocess.check_output(["lnetctl", "export", "--backup"], text=True)
            LUSTRE_LNET_CONF.write_text(result)
        except (subprocess.CalledProcessError, IOError) as e:
            _logger.error("failed to write lnet configuration to the system", exc_info=e)
            raise Error(str(e))

    def supported(self) -> bool:
        """Check if underlying base supports mounting shares."""
        try:
            return not is_container()
        except UnknownVirtualizationStateError:
            _logger.warning("could not detect execution in virtualized environment")
            return True

    @contextlib.contextmanager
    def mounts(self, force_mount=False) -> Iterator[Mounts]:
        """Get the list of `Mounts` that need to be managed by the `MountsManager`.

        It will initially contain no mounts, and any mount that is added to
        `Mounts` will be mounted by the manager. Mounts that were
        added on previous executions will get removed if they're not added again
        to the `Mounts` object.
        """
        mounts = Mounts(self.enable_lustre)
        yield mounts
        # This will not resume if the caller raised an exception, which
        # should be enough to ensure the file is not written if the charm entered
        # an error state.
        new_autofs = "\n".join(
            (
                f"{mountpoint} -{','.join(info.options)} {info.endpoint}"
                for mountpoint, info in sorted(mounts._mounts.items())
            )
        )

        old_autofs = self._autofs_file.read_text()

        # Avoid restarting autofs if the config didn't change.
        if not force_mount and new_autofs == old_autofs:
            return

        try:
            for mount in mounts._mounts.keys():
                pathlib.Path(mount).mkdir(parents=True, exist_ok=True)
            self._autofs_file.write_text(new_autofs)
            systemctl("reload-or-restart", "autofs", check=True)
        except SystemdError as e:
            _logger.error("failed to mount filesystems", exc_info=e)
            raise Error(str(e))


def _get_endpoint_and_opts(info: FilesystemInfo, enable_lustre: bool) -> tuple[str, list[str]]:
    match info:
        case NfsInfo(hostname=hostname, port=port, path=path):
            try:
                IPv6Address(hostname)
                # Need to add brackets if the hostname is IPv6
                hostname = f"[{hostname}]"
            except AddressValueError:
                pass

            endpoint = f"{hostname}:{path}"
            options = ["fstype=nfs"]
            if port:
                options.append(f"port={port}")
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
        case LustreInfo(mgs_ids=mgs_ids, fs_name=fs_name) if enable_lustre:
            mgs_ids = ":".join(mgs_ids)
            endpoint = f"{mgs_ids}:/{fs_name}"
            options = ["fstype=lustre"]
        case LustreInfo():
            raise Error(
                "mounting a lustre filesystem requires setting the `enable-lustre` config to true"
            )
        case _:
            raise Error(f"unsupported filesystem type `{info.filesystem_type()}`")

    return endpoint, options


def _get_default_interface():
    """Return the default network interface name for this unit."""
    try:
        result = subprocess.check_output(["ip", "-json", "route", "show", "default"], text=True)
        routes = json.loads(result)
        return routes[0]["dev"]
    except (json.JSONDecodeError, TypeError, subprocess.CalledProcessError) as e:
        _logger.error("failed to get the default network interface", exc_info=e)
        raise Error(str(e))
    except (KeyError, IndexError) as e:
        _logger.error("could not determine the default network interface name", exc_info=e)
        raise Error(str(e))
