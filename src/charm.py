#!/usr/bin/env python3
# Copyright 2024 Jose Julian Espina
# See LICENSE file for licensing details.

"""Charm the application."""

import json
import logging
from collections import Counter
from contextlib import contextmanager
from typing import Any, Optional, Generator

import charms.operator_libs_linux.v0.apt as apt
import ops
from charms.storage_client.v0.fs_interfaces import FSRequires, Share
from jsonschema import ValidationError, validate

from utils.manager import MountManager

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

class StorageClientCharm(ops.CharmBase):
    """Charm the application."""

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)

        self._fs_share = FSRequires(self, "fs-share")
        self._mount_manager = MountManager()
        framework.observe(self.on.upgrade_charm, self._handle_event)
        framework.observe(self.on.update_status, self._handle_event)
        framework.observe(self.on.config_changed, self._handle_event)
        framework.observe(self._fs_share.on.mount_share, self._handle_event)
        framework.observe(self._fs_share.on.umount_share, self._handle_event)

    def _handle_event(self, event: ops.EventBase) -> None:
        self.unit.status = ops.MaintenanceStatus("Updating status.")

        if not self._mount_manager.installed:
            self.unit.status = ops.MaintenanceStatus("Installing required packages.")
            self._mount_manager.ensure(apt.PackageState.Present)

        try:
            config: dict[str, dict[str, str | bool]] = json.loads(self.config.get("mountinfo"))
            validate(config, CONFIG_SCHEMA)
            for (fs, opts) in config.items():
                for opt in ["noexec", "nosuid", "nodev", "read-only"]:
                    opts[opt] = opts.get(opt, False)
        except (json.JSONDecodeError, ValidationError) as e:
            self.app.status = ops.BlockedStatus(
                f"Invalid configuration for option `mountinfo`. Reason: {e}"
            )
            return

        shares = self._fs_share.shares
        active_filesystems = set()
        for fs_type, count in Counter([share.info.fs_type() for share in shares]).items():
            if count > 1:
                self.app.status = ops.BlockedStatus(f"Too many relations for mount type `{fs_type}`.")
                return
            active_filesystems.add(fs_type)

        with self.mounts() as mounts:
            # Cleanup and unmount all the mounts that are not available.
            for fs_type in list(mounts.keys()):
                if fs_type not in active_filesystems:
                    self._mount_manager.umount(mounts[fs_type]["mountpoint"])
                    del mounts[fs_type]

            for share in shares:
                fs_type = share.info.fs_type()
                if not (options := config.get(fs_type)):
                    self.app.status = ops.BlockedStatus(f"Missing configuration for mount type `{fs_type}.")
                    return

                options["uri"] = share.uri

                mountpoint = options["mountpoint"]

                opts = list()
                opts.append("noexec" if options.get("noexec") else "exec")
                opts.append("nosuid" if options.get("nosuid") else "suid")
                opts.append("nodev" if options.get("nodev") else "dev")
                opts.append("ro" if options.get("read-only") else "rw")

                self.unit.status = ops.MaintenanceStatus(f"Mounting `{mountpoint}`")

                if not (mount := mounts.get(fs_type)) or mount != options:
                    # Just in case, unmount the previously mounted  
                    if mount:
                        self._mount_manager.umount(mount["mountpoint"])
                    self._mount_manager.mount(share, mountpoint, options=opts)
                    mounts[fs_type] = options

        self.unit.status = ops.ActiveStatus("Mounted shares.")

    @property
    def peers(self) -> Optional[ops.Relation]:
        """Fetch the peer relation."""
        return self.model.get_relation(PEER_NAME)
    
    @contextmanager
    def mounts(self) -> Generator[dict[str, dict[str, str | bool]], None, None]:
        mounts = self.get_state("mounts")
        yield mounts
        # Don't set the state if the program throws an error.
        # This guarantees we're in a clean state after the charm unblocks.
        self.set_state("mounts", mounts)

    def set_state(self, key: str, data: Any) -> None:
        """Insert a value into the global state."""
        self.peers.data[self.app][key] = json.dumps(data)

    def get_state(self, key: str) -> dict[Any, Any]:
        """Get a value from the global state."""
        if not self.peers:
            return {}

        data = self.peers.data[self.app].get(key, "{}")
        return json.loads(data)


if __name__ == "__main__":  # pragma: nocover
    ops.main(StorageClientCharm)  # type: ignore
