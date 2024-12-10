#!/usr/bin/env python3
# Copyright 2024 Jose Julian Espina
# See LICENSE file for licensing details.

"""Charm the application."""

import json
import logging
from collections import Counter
from contextlib import contextmanager
from typing import Any, Generator, Optional

import charms.operator_libs_linux.v0.apt as apt
import ops
from charms.filesystem_client.v0.interfaces import FsRequires
from jsonschema import ValidationError, validate

from utils.manager import MountsManager

logger = logging.getLogger(__name__)

CONFIG_SCHEMA = {
    "$schema": "http://json-schema.org/draft-04/schema#",
    "type": "object",
    "additionalProperties": {
        "type": "object",
        "required": ["mountpoint"],
        "properties": {
            "mountpoint": {"type": "string"},
            "noexec": {"type": "boolean"},
            "nosuid": {"type": "boolean"},
            "nodev": {"type": "boolean"},
            "read-only": {"type": "boolean"},
        },
    },
}

PEER_NAME = "storage-peers"


class FilesystemClientCharm(ops.CharmBase):
    """Charm the application."""

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)

        self._fs_share = FsRequires(self, "fs-share")
        self._mounts_manager = MountsManager()
        framework.observe(self.on.upgrade_charm, self._handle_event)
        framework.observe(self.on.update_status, self._handle_event)
        framework.observe(self.on.config_changed, self._handle_event)
        framework.observe(self._fs_share.on.mount_fs, self._handle_event)
        framework.observe(self._fs_share.on.umount_fs, self._handle_event)

    def _handle_event(self, event: ops.EventBase) -> None:  # noqa: C901
        self.unit.status = ops.MaintenanceStatus("Updating status.")

        if not self._mounts_manager.installed:
            self.unit.status = ops.MaintenanceStatus("Installing required packages.")
            self._mounts_manager.ensure(apt.PackageState.Present)

        try:
            config = json.loads(self.config.get("mountinfo"))
            validate(config, CONFIG_SCHEMA)
            config: dict[str, dict[str, str | bool]] = config
            for fs, opts in config.items():
                for opt in ["noexec", "nosuid", "nodev", "read-only"]:
                    opts[opt] = opts.get(opt, False)
        except (json.JSONDecodeError, ValidationError) as e:
            self.app.status = ops.BlockedStatus(
                f"Invalid configuration for option `mountinfo`. Reason: {e}"
            )
            return

        shares = self._fs_share.endpoints
        active_filesystems = set()
        for fs_type, count in Counter([share.fs_info.fs_type() for share in shares]).items():
            if count > 1:
                self.app.status = ops.BlockedStatus(
                    f"Too many relations for mount type `{fs_type}`."
                )
                return
            active_filesystems.add(fs_type)

        with self.mounts() as mounts:
            # Cleanup and unmount all the mounts that are not available.
            for fs_type in list(mounts.keys()):
                if fs_type not in active_filesystems:
                    self._mounts_manager.umount(mounts[fs_type]["mountpoint"])
                    del mounts[fs_type]

            for share in shares:
                fs_type = share.fs_info.fs_type()
                if not (options := config.get(fs_type)):
                    self.app.status = ops.BlockedStatus(
                        f"Missing configuration for mount type `{fs_type}."
                    )
                    return

                options["uri"] = share.uri

                mountpoint = options["mountpoint"]

                opts = []
                opts.append("noexec" if options.get("noexec") else "exec")
                opts.append("nosuid" if options.get("nosuid") else "suid")
                opts.append("nodev" if options.get("nodev") else "dev")
                opts.append("ro" if options.get("read-only") else "rw")

                self.unit.status = ops.MaintenanceStatus(f"Mounting `{mountpoint}`")

                if not (mount := mounts.get(fs_type)) or mount != options:
                    # Just in case, unmount the previously mounted share
                    if mount:
                        self._mounts_manager.umount(mount["mountpoint"])
                    self._mounts_manager.mount(share.fs_info, mountpoint, options=opts)
                    mounts[fs_type] = options

        self.unit.status = ops.ActiveStatus("Mounted shares.")

    @property
    def peers(self) -> Optional[ops.Relation]:
        """Fetch the peer relation."""
        return self.model.get_relation(PEER_NAME)

    @contextmanager
    def mounts(self) -> Generator[dict[str, dict[str, str | bool]], None, None]:
        """Get the mounted filesystems."""
        mounts = self.get_state("mounts")
        yield mounts
        # Don't set the state if the program throws an error.
        # This guarantees we're in a clean state after the charm unblocks.
        self.set_state("mounts", mounts)

    def set_state(self, key: str, data: Any) -> None:
        """Insert a value into the global state."""
        if not self.peers:
            raise RuntimeError(
                "Peer relation can only be written to after the relation is established"
            )
        self.peers.data[self.app][key] = json.dumps(data)

    def get_state(self, key: str) -> dict[Any, Any]:
        """Get a value from the global state."""
        if not self.peers:
            return {}

        data = self.peers.data[self.app].get(key, "{}")
        return json.loads(data)


if __name__ == "__main__":  # pragma: nocover
    ops.main(FilesystemClientCharm)  # type: ignore
