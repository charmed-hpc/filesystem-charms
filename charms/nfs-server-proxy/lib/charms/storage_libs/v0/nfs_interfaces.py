# Copyright 2023 Canonical Ltd.
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

"""Library to manage integrations between NFS share providers and consumers.

This library contains the NFSProvides and NFSRequires classes for managing an
integration between an NFS server operator and NFS client operator.

## Requires Charm (NFS Client)

This library provides a uniform interface for charms that need to mount, unmount,
or request an NFS share, and convenience methods for consuming data sent by an
NFS server charm. Here is an example of using a `ServerConnectedEvent` to request
a new NFS share:

```python
from charms.storage_libs.v0.nfs_interfaces import (
    NFSRequires,
    ServerConnectedEvent,
)


class ApplicationCharm(CharmBase):
    # Application charm that needs to mount NFS shares.

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        # Charm events defined in the NFSRequires class.
        self.nfs_share = NFSRequires(self, "nfs-share")
        self.framework.observe(
            self.nfs_share.on.server_connected,
            self._on_server_connected,
        )

    def _on_server_connected(self, event: ServerConnectedEvent) -> None:
        # Handle when new NFS server is connected.
        self._nfs_share.request_share(
            event.relation.id,
            name="/data",
            size=100,
            allowlist="0.0.0.0/0",
        )

        self.unit.status = WaitingStatus("Waiting for NFS share to be created.")
```

The NFSRequires class provides a few custom events to handle specific situations
related to mounting an NFS share. They are all listed below:

- `server_connected`: Event emitted when the NFS client is connected to the NFS server.
    Here is where NFS clients will commonly request the NFS share they need created.
- `mount_share`: Event emitted when NFS share is ready to be mounted.
- `umount_share`: Event emitted when NFS share is ready or needs to be unmounted.

> __Note:__ This charm library only supports one NFS server being integrated with the
> NFS client at a time. This is to prevent the NFS client from having to manage and
> request multiple NFS shares, and ensure that NFS clients are creating unique mount points.

## Provides Charm (NFS Server)

This library provides a uniform interface for charms that need to process NFS share
requests, and convenience methods for consuming data sent by an NFS client charm.
Here is an example of using a `ShareRequestedEvent` to create a new NFS share:

```python
from charms.storage_libs.v0.nfs_interfaces import (
    NFSProvides,
    ShareRequestedEvent,
)


class ApplicationCharm(CharmBase):
    # Application charm that exports mountable NFS shares.

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        # Charm events defined in the NFSProvides class.
        self.nfs_share = NFSProvides(self, "nfs-share")
        self.framework.observe(
            self.nfs_share.on.share_requested,
            self._on_share_requested,
        )

    def _on_share_requested(self, event: ShareRequestedEvent) -> None:
        # Handle when NFS client requests NFS share.
        if not self.unit.is_leader():
            return

        share = pathlib.Path(f"/var/local/{event.name}")
        if not share.exists()
            storage = pathlib.Path(f"/var/local/storage/{event.name}")
            storage.touch()
            subprocess.run(["truncate", "-s", f"{event.size}G", share])
            subprocess.run(["mke2fs", "-t", "ext4", "-F", share])
            share.mkdir()
            subprocess.run(["mount", storage, share])
            with open("/etc/exports", "at") as fout:
                fout.write(f"{share} *(rw,sync,no_subtree_check,no_root_squash)")
            subprocess.run(["exportfs", "-a"])
            systemd.service_restart("nfs-kernel-server")

        hostname = subprocess.run(["hostname", "-I"], text=True).stdout
        self.nfs_share.set_endpoint(event.relation.id, f"{hostname}:{share}")
        self.unit.status = ActiveStatus("Exporting shares")
```

The NFSProvides class only provides one custom event:

- `share_requested`: Event emitted when NFS client requests an NFS share.

> __Note:__ It is the responsibility of the NFS Provider charm to provide
> the implementation for creating a new NFS share. NFSProvides just provides
> the interface for the integration.
"""

import json
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Union

from ops.charm import (
    CharmBase,
    CharmEvents,
    RelationChangedEvent,
    RelationDepartedEvent,
    RelationEvent,
    RelationJoinedEvent,
)
from ops.framework import EventSource, Object
from ops.model import Relation

# The unique Charmhub library identifier, never change it
LIBID = "e70144cb4196455ca4436bec5d1e6a36"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 3

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Transaction:
    """Store transaction information between to data mappings."""

    added: Set
    changed: Set
    deleted: Set


def _eval(event: RelationChangedEvent, bucket: str) -> _Transaction:
    """Evaluate the difference between data in an integration changed databag.

    Args:
        event: Integration changed event.
        bucket: Bucket of the databag. Can be application or unit databag.

    Returns:
        _Transaction:
            Transaction info containing the added, deleted, and changed
            keys from the event integration databag.
    """
    # Retrieve the old data from the data key in the application integration databag.
    old_data = json.loads(event.relation.data[bucket].get("cache", "{}"))
    # Retrieve the new data from the event integration databag.
    new_data = {
        key: value for key, value in event.relation.data[event.app].items() if key != "cache"
    }
    # These are the keys that were added to the databag and triggered this event.
    added = new_data.keys() - old_data.keys()
    # These are the keys that were removed from the databag and triggered this event.
    deleted = old_data.keys() - new_data.keys()
    # These are the keys that already existed in the databag, but had their values changed.
    changed = {key for key in old_data.keys() & new_data.keys() if old_data[key] != new_data[key]}
    # Convert the new_data to a serializable format and save it for a next diff check.
    event.relation.data[bucket].update({"cache": json.dumps(new_data)})

    # Return the transaction with all possible changes.
    return _Transaction(added, changed, deleted)


class ServerConnectedEvent(RelationEvent):
    """Emit when an NFS server is integrated with NFS client."""


class _MountEvent(RelationEvent):
    """Base event for mount-related events."""

    @property
    def endpoint(self) -> Optional[str]:
        """Get NFS share endpoint."""
        return self.relation.data[self.relation.app].get("endpoint")


class MountShareEvent(_MountEvent):
    """Emit when NFS share is ready to be mounted."""


class UmountShareEvent(_MountEvent):
    """Emit when NFS share needs to be unmounted."""


class _NFSRequiresEvents(CharmEvents):
    """Events that NFS servers can emit."""

    server_connected = EventSource(ServerConnectedEvent)
    mount_share = EventSource(MountShareEvent)
    umount_share = EventSource(UmountShareEvent)


class ShareRequestedEvent(RelationEvent):
    """Emit when a consumer requests a new NFS share be created by the provider."""

    @property
    def name(self) -> Optional[str]:
        """Get name of requested NFS share."""
        return self.relation.data[self.relation.app].get("name")

    @property
    def allowlist(self) -> Optional[str]:
        """Comma-separated list of addresses grant access to NFS share."""
        return self.relation.data[self.relation.app].get("allowlist")

    @property
    def size(self) -> Optional[str]:
        """Size in gigabytes of the NFS share.

        If unset, the NFS share will not be restricted in size.
        """
        return self.relation.data[self.relation.app].get("size")


class _NFSProvidesEvents(CharmEvents):
    """Events that NFS clients can emit."""

    share_requested = EventSource(ShareRequestedEvent)


class _BaseInterface(Object):
    """Base methods required for NFS share integration interfaces."""

    def __init__(self, charm: CharmBase, integration_name) -> None:
        super().__init__(charm, integration_name)
        self.charm = charm
        self.app = charm.model.app
        self.unit = charm.unit
        self.integration_name = integration_name

    @property
    def integrations(self) -> List[Relation]:
        """Get list of active integrations associated with the integration name."""
        result = []
        for integration in self.charm.model.relations[self.integration_name]:
            try:
                _ = repr(integration.data)
                result.append(integration)
            except RuntimeError:
                pass
        return result

    def fetch_data(self) -> Dict:
        """Fetch integration data.

        Notes:
            Method cannot be used in `*-relation-broken` events and will raise an exception.

        Returns:
            Dict:
                Values stored in the integration data bag for all integration instances.
                Values are indexed by the integration ID.
        """
        result = {}
        for integration in self.integrations:
            result[integration.id] = {
                k: v for k, v in integration.data[integration.app].items() if k != "cache"
            }
        return result

    def _update_data(self, integration_id: int, data: Dict) -> None:
        """Updates a set of key-value pairs in integration.

        Args:
            integration_id: Identifier of particular integration.
            data: Key-value pairs that should be updated in integration data bucket.

        Notes:
            Only the application leader unit can update the
            integration data bucket using this method.
        """
        if self.unit.is_leader():
            integration = self.charm.model.get_relation(self.integration_name, integration_id)
            integration.data[self.app].update(data)


class NFSRequires(_BaseInterface):
    """Consumer-side interface of NFS share integrations."""

    on = _NFSRequiresEvents()

    def __init__(self, charm: CharmBase, integration_name: str) -> None:
        super().__init__(charm, integration_name)
        self.framework.observe(
            charm.on[integration_name].relation_joined, self._on_relation_joined
        )
        self.framework.observe(
            charm.on[integration_name].relation_changed, self._on_relation_changed
        )
        self.framework.observe(
            charm.on[integration_name].relation_departed, self._on_relation_departed
        )

    def _on_relation_joined(self, event: RelationJoinedEvent) -> None:
        """Handle when client and server are first integrated."""
        if self.unit.is_leader():
            _logger.debug("Emitting `ServerConnected` event from `RelationJoined` hook")
            self.on.server_connected.emit(event.relation, app=event.app, unit=event.unit)

    def _on_relation_changed(self, event: RelationChangedEvent) -> None:
        """Handle when the databag between client and server has been updated."""
        transaction = _eval(event, self.unit)
        if "endpoint" in transaction.added:
            _logger.debug("Emitting `MountShare` event from `RelationChanged` hook")
            self.on.mount_share.emit(event.relation, app=event.app, unit=event.unit)

    def _on_relation_departed(self, event: RelationDepartedEvent) -> None:
        """Handle when server departs integration."""
        _logger.debug("Emitting `UmountShare` event from `RelationDeparted` hook")
        self.on.umount_share.emit(event.relation, app=event.app, unit=event.unit)

    def request_share(
        self,
        integration_id: int,
        name: str,
        allowlist: Optional[Union[str, List[str]]] = None,
        size: Optional[int] = None,
    ) -> None:
        """Request access to an NFS share.

        Args:
            integration_id: Identifier for specific integration.
            name: Name of NFS share.
            allowlist: List of IP address to grant r/w access to on NFS share.
            size: Size, in gigabytes, of NFS share.

        Notes:
            Only application leader unit can request an NFS share.
        """
        if self.unit.is_leader():
            if type(allowlist) == str:
                _allowlist = [allowlist]
            elif type(allowlist) == list:
                _allowlist = allowlist
            else:
                _allowlist = ["0.0.0.0"]

            if type(size) == int:
                _size = size
            else:
                _size = -1

            params = {"name": name, "allowlist": ",".join(_allowlist), "size": str(_size)}
            _logger.debug(f"Requesting NFS share with parameters {params}")
            self._update_data(integration_id, params)


class NFSProvides(_BaseInterface):
    """Provider-side interface of NFS share integrations."""

    on = _NFSProvidesEvents()

    def __init__(self, charm: CharmBase, integration_name: str) -> None:
        super().__init__(charm, integration_name)
        self.framework.observe(
            charm.on[integration_name].relation_changed, self._on_relation_changed
        )

    def _on_relation_changed(self, event: RelationChangedEvent) -> None:
        """Handle when the databag between client and server has been updated."""
        if self.unit.is_leader():
            transaction = _eval(event, self.unit)
            if "name" in transaction.added:
                _logger.debug("Emitting `RequestShare` event from `RelationChanged` hook")
                self.on.share_requested.emit(event.relation, app=event.app, unit=event.unit)

    def set_endpoint(self, integration_id: int, endpoint: str) -> None:
        """Set endpoint for mounting NFS share.

        Args:
            integration_id: Identifier for specific integration.
            endpoint: NFS share endpoint.

        Notes:
            Only application leader unit can set the NFS share endpoint.
        """
        if self.unit.is_leader():
            _logger.debug(f"Exporting NFS share {endpoint}")
            self._update_data(integration_id, {"endpoint": endpoint})
