# Copyright 2025 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Library to manage relations between a filesystem client and a mount requirer.

This library contains the MountProvides and MountRequires classes for managing a
relation between a filesystem client operator and a mount requirer.

## MountInfo (mount information)

This class defines information about the mount provided by the filesystem client.

## MountRequires (filesystem client)

This class provides a uniform interface for charms that require a mounted shared filesystems,
and convenience methods for consuming data sent by the filesystem client charm.

### Defined events

- `connected`: Event emitted when the filesystem client is ready to receive mount information.
- `mounted`: Event emitted when the filesystem is mounted in the system.
- `unmounted`: Event emitted when the filesystem is unmounted from the system.
- `disconnected`: Event emitted when the filesystem client has disconnected from the mount requirer.

### Example

```python
import ops
from charms.filesystem_client.v0.mount_info import MountRequires, MountInfo

class MountClient(ops.CharmBase):

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._mount = MountRequires(self, "mount")
        self.framework.observe(self._mount.on.mount_provider_connected, self._on_mount_provider_connected)
        self.framework.observe(self._mount.on.mounted_filesystem, self._on_mounted_filesystem)
        self.framework.observe(self._mount.on.unmounted_filesystem, self._on_unmounted_filesystem)

    def _on_mount_provider_connected(self, event: EventBase) -> None:
        for relation in self._mount.relations:
            self._mount.set_mount_info(relation.id, MountInfo(mountpoint="/srv"))

        self.unit.status = ops.ActiveStatus()

    def _on_mounted_filesystem(self, _) -> None:
        self.write_files()

    def _on_unmounted_filesystem(self, _) -> None:
        self.recover_files()
```

## MountProvides (filesystem client)

This library provides an interface for the filesystem client operator to receive
mount share requests.

### Defined events

- `requested`: Event emitted when a mount requirer requests mounting a filesystem.
- `unrequested`: Event emitted when the mount requirer withdraws its mount request.

"""

__all__ = [
    "MountInfoError",
    "MountInfo",
    "MountProviderConnectedEvent",
    "MountedFilesystemEvent",
    "UnmountedFilesystemEvent",
    "MountProviderDisconnectedEvent",
    "MountRequiresEvents",
    "MountRequires",
    "MountRequestedEvent",
    "MountUnrequestedEvent",
    "MountProvidesEvents",
    "MountProvides",
]

import logging
from dataclasses import asdict, dataclass

from ops.charm import (
    CharmBase,
    CharmEvents,
    RelationBrokenEvent,
    RelationChangedEvent,
    RelationCreatedEvent,
    RelationEvent,
)
from ops.framework import EventSource, Object
from ops.model import Relation

# The unique Charmhub library identifier, never change it
LIBID = "48a4ca63d3b14351b9b1b2298bcf8c84"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 1

_logger = logging.getLogger(__name__)


class MountInfoError(Exception):
    """Exception raised when an operation failed."""


@dataclass(frozen=True)
class MountInfo:
    """Information required to mount a filesystem."""

    mountpoint: str
    """Location to mount the filesystem on the machine."""
    noexec: bool = False
    """Block execution of binaries on the filesystem."""
    nosuid: bool = False
    """Do not honor suid and sgid bits on the filesystem."""
    nodev: bool = False
    """Blocking interpretation of character and/or block devices on the filesystem."""
    read_only: bool = False
    """Mount filesystem as read-only."""


class _BaseInterface(Object):
    """Base methods required for mount relation interfaces."""

    def __init__(self, charm: CharmBase, relation_name) -> None:
        super().__init__(charm, relation_name)
        self.charm = charm
        self.app = charm.model.app
        self.unit = charm.unit
        self.relation_name = relation_name

    @property
    def relations(self) -> list[Relation]:
        """Get list of active relations associated with the relation name."""
        result = []
        for relation in self.charm.model.relations[self.relation_name]:
            try:
                _ = repr(relation.data)
                result.append(relation)
            except RuntimeError:
                pass
        return result


class MountProviderConnectedEvent(RelationEvent):
    """Emit when a mount provider is waiting for the mount info."""


class MountedFilesystemEvent(RelationEvent):
    """Emit when a mount provider has mounted the filesystem."""


class UnmountedFilesystemEvent(RelationEvent):
    """Emit when a mount provider has unmounted the filesystem."""


class MountProviderDisconnectedEvent(RelationEvent):
    """Emit when a mount provider has left the charm relation."""


class MountRequiresEvents(CharmEvents):
    """Events that mount providers can emit."""

    mount_provider_connected = EventSource(MountProviderConnectedEvent)
    mounted_filesystem = EventSource(MountedFilesystemEvent)
    unmounted_filesystem = EventSource(UnmountedFilesystemEvent)
    mount_provider_disconnected = EventSource(MountProviderDisconnectedEvent)


class MountRequires(_BaseInterface):
    """Consumer-side interface of mount integrations."""

    on = MountRequiresEvents()

    def __init__(self, charm: CharmBase, relation_name: str = "mount") -> None:
        super().__init__(charm, relation_name)
        self.framework.observe(charm.on[relation_name].relation_created, self._on_relation_created)
        self.framework.observe(charm.on[relation_name].relation_changed, self._on_relation_changed)
        self.framework.observe(charm.on[relation_name].relation_broken, self._on_relation_broken)

    def _on_relation_created(self, event: RelationCreatedEvent) -> None:
        if not self.unit.is_leader():
            return

        _logger.debug(
            "emitting `%s` from `RelationCreated` hook", MountProviderConnectedEvent.__name__
        )
        self.on.mount_provider_connected.emit(event.relation, app=event.app, unit=event.unit)

    def _on_relation_changed(self, event: RelationChangedEvent) -> None:
        mounted = event.relation.data[event.unit].get("mounted")

        if mounted is None:
            # Relation is not ready. Don't emit any events.
            return

        if mounted == "true":
            _logger.debug(
                "emitting `%s` from `RelationChanged` hook", MountedFilesystemEvent.__name__
            )
            self.on.mounted_filesystem.emit(event.relation, app=event.app, unit=event.unit)
        else:
            _logger.debug(
                "emitting `%s` from `RelationChanged` hook", UnmountedFilesystemEvent.__name__
            )
            self.on.unmounted_filesystem.emit(event.relation, app=event.app, unit=event.unit)

    def _on_relation_broken(self, event: RelationBrokenEvent) -> None:
        if not self.unit.is_leader():
            return

        _logger.debug(
            "emitting `%s` from `RelationBroken` hook", MountProviderDisconnectedEvent.__name__
        )
        self.on.mount_provider_disconnected.emit(event.relation, app=event.app, unit=event.unit)

    def set_mount_info(self, relation_id: int, info: MountInfo) -> None:
        """Set mount info for mounting the filesystem.

        Args:
            relation_id: Identifier for the specific relation.
            info: Information to mount the filesystem.

        Notes:
            Only the application leader unit can set the mount info.
        """
        if not self.unit.is_leader():
            return

        _logger.debug(f"requesting a mounted filesystem with info `{asdict(info)}`")
        relation = self.charm.model.get_relation(self.relation_name, relation_id)
        relation.save(info, self.app)


class MountRequestedEvent(RelationEvent):
    """Emit when a mount requirer has provided the mount info."""


class MountUnrequestedEvent(RelationEvent):
    """Emit when a mount requirer has removed its mount info."""


class MountProvidesEvents(CharmEvents):
    """Events that mount requirers can emit."""

    mount_requested = EventSource(MountRequestedEvent)
    mount_unrequested = EventSource(MountUnrequestedEvent)


class MountProvides(_BaseInterface):
    """Provider-side interface of mount integrations."""

    on = MountProvidesEvents()

    def __init__(self, charm: CharmBase, relation_name: str = "mount") -> None:
        super().__init__(charm, relation_name)
        self.framework.observe(charm.on[relation_name].relation_changed, self._on_relation_changed)

    def _on_relation_changed(self, event: RelationChangedEvent) -> None:
        if event.relation.data[event.app].get("mountpoint"):
            _logger.debug(
                "emitting `%s` from `RelationChanged` hook", MountRequestedEvent.__name__
            )
            self.on.mount_requested.emit(event.relation, app=event.app, unit=event.unit)
        else:
            _logger.debug(
                "emitting `%s` from `RelationChanged` hook", MountUnrequestedEvent.__name__
            )
            self.on.mount_unrequested.emit(event.relation, app=event.app, unit=event.unit)

    def mount_info(self, relation_id: int) -> MountInfo | None:
        """Fetch the mount information of a relation.

        Notes:
            Method cannot be used in `*-relation-broken` events and will raise an exception.

        Returns:
            MountInfo: information to mount a filesystem. None if the relation has not provided
                the required information.
        """
        relation = self.charm.model.get_relation(self.relation_name, relation_id)
        if relation.data[relation.app].get("mountpoint"):
            return relation.load(MountInfo, relation.app)
        else:
            return None

    def set_mount_status(self, mounted: bool) -> None:
        """Set the current mount status of the filesystem.

        Args:
            mounted: boolean representing the mount status of the filesystem.
        """
        for relation in self.relations:
            relation.data[self.unit]["mounted"] = "true" if mounted else "false"
