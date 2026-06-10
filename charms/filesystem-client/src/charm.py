#!/usr/bin/env python3
# Copyright 2024-2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm for the filesystem client."""

import logging
from typing import cast

import ops
from charmed_hpc_libs.ops import StopCharm, refresh
from charms.filesystem_client.v0.filesystem_info import FilesystemRequires
from charms.filesystem_client.v0.mount_info import MountInfo, MountProvides
from utils.manager import MountsManager

_logger = logging.getLogger(__name__)


# Trying to use a delta charm (one method per event) proved to be a bit unwieldy, since
# we would have to handle multiple updates at once:
# - mount requests
# - umount requests
# - config changes
#
# Additionally, we would need to wait until the correct configuration
# was provided, so we would have to somehow keep track of the pending
# mount requests.
#
# A holistic charm (one method for all events) was a lot easier to deal with,
# simplifying the code to handle all the events.
class FilesystemClientCharm(ops.CharmBase):
    """Charm the application."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        self._filesystem = FilesystemRequires(self, "filesystem")
        self._mount = MountProvides(self, "mount")
        self._mounts_manager = MountsManager(self)
        self.framework.observe(self.on.upgrade_charm, self._handle_event)
        self.framework.observe(self.on.update_status, self._handle_event)
        self.framework.observe(self.on.config_changed, self._handle_event)
        self.framework.observe(self._filesystem.on.mount_filesystem, self._handle_event)
        self.framework.observe(self._filesystem.on.umount_filesystem, self._handle_event)
        self.framework.observe(self._mount.on.mount_requested, self._handle_event)
        self.framework.observe(self._mount.on.mount_unrequested, self._handle_event)

    @refresh()
    def _handle_event(self, event: ops.EventBase) -> None:
        """Handle a Juju event."""
        self._mount.set_mount_status(mounted=False)
        self.unit.status = ops.MaintenanceStatus("Updating status")
        self._mounts_manager.enable_lustre = cast(bool, self.config.get("enable-lustre"))

        try:
            if not self._mounts_manager.supported():
                raise StopCharm(ops.BlockedStatus("Cannot mount filesystems on LXD containers"))

            self._ensure_setup()

            with self._mounts_manager.mounts() as mounts:
                config = self._get_config()
                endpoints = self._filesystem.endpoints
                if not endpoints:
                    raise StopCharm(
                        ops.BlockedStatus("Waiting for an integration with a filesystem provider"),
                        set_app_status=True,
                    )

                # This is limited to 1 relation.
                endpoint = endpoints[0]

                if self.unit.is_leader():
                    self.app.status = ops.ActiveStatus(
                        f"Integrated with `{endpoint.info.filesystem_type()}` provider"
                    )

                self.unit.status = ops.MaintenanceStatus("Mounting filesystem")

                opts = []
                opts.append("noexec" if config.noexec else "exec")
                opts.append("nosuid" if config.nosuid else "suid")
                opts.append("nodev" if config.nodev else "dev")
                opts.append("ro" if config.read_only else "rw")
                mounts.add(info=endpoint.info, mountpoint=config.mountpoint, options=opts)

                self.unit.status = ops.ActiveStatus(f"Mounted filesystem at `{config.mountpoint}`")

            self._mount.set_mount_status(mounted=True)
        except StopCharm:
            raise
        except Exception:
            _logger.error("unexpected error while handling event", exc_info=True)
            raise StopCharm(
                ops.BlockedStatus("Failed to mount filesystems. See `juju debug-log` for details")
            )

    def _ensure_setup(self) -> None:
        """Ensure the manager is set up to mount filesystems."""
        if not self._mounts_manager.is_setup():
            self.unit.status = ops.MaintenanceStatus("Setting up the system")
            self._mounts_manager.setup()

    def _get_config(self) -> MountInfo:
        """Get and validate the configuration of the charm."""
        relations = iter(self._mount.relations)
        mountpoint = cast(str, self.config.get("mountpoint"))

        if not mountpoint:
            relation = next(relations, None)
            if not relation:
                raise StopCharm(
                    ops.BlockedStatus("Missing `mountpoint` config or `mount` integration"),
                    set_app_status=True,
                )

            if next(relations, None):
                raise StopCharm(
                    ops.BlockedStatus(
                        "Cannot mount using more than one relation at the same time"
                    ),
                    set_app_status=True,
                )

            mount_info = self._mount.mount_info(relation.id)
            if not mount_info:
                raise StopCharm(
                    ops.WaitingStatus("Waiting for mountpoint from `mount` integration")
                )

            return mount_info

        if next(relations, None):
            raise StopCharm(
                ops.BlockedStatus(
                    "Cannot mount using both the `mountpoint` config and the `mount` integration"
                ),
                set_app_status=True,
            )

        return MountInfo(
            mountpoint=mountpoint,
            noexec=cast(bool, self.config.get("noexec")),
            nosuid=cast(bool, self.config.get("nosuid")),
            nodev=cast(bool, self.config.get("nodev")),
            read_only=cast(bool, self.config.get("read-only")),
        )


if __name__ == "__main__":  # pragma: nocover
    ops.main(FilesystemClientCharm)  # type: ignore
