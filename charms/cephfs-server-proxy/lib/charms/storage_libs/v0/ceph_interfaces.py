# Copyright 2024 Canonical Ltd.
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

import json
import logging
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Set, Union, Iterable

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
LIBID = "UNPUBLISHED"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 1

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
    """Emit when a CephFS server is integrated with CephFS client."""

@dataclass(init=False)
class CephFSShareInfo:
    """Information about a shared CephFS."""
    fsid: str
    """Id of the Ceph cluster."""
    name: str
    """Name of the exported CephFS."""
    path: str
    """Exported path of the CephFS."""
    monitor_hosts: [str]
    """Address list of the available Ceph MON nodes."""
    auth_id: str
    """Secret ID for the auth info to authenticate against the CephFS share."""

    dict = asdict

    def __init__(self, fsid: str, name: str, path: str, monitor_hosts: Iterable[str], auth_id: str):
        self.fsid = fsid
        self.name = name
        self.path = path
        # Manual conversion to avoid storing a `op.StoredList` on the info.
        self.monitor_hosts = list(monitor_hosts)
        self.auth_id = auth_id



class _MountEvent(RelationEvent):
    """Base event for mount-related events."""

    @property
    def share_info(self) -> Optional[CephFSShareInfo]:
        """Get CephFS share info."""
        if not (share_info := self.relation.data[self.relation.app].get("share_info")):
            return
        return CephFSShareInfo(**json.loads(share_info))


class MountShareEvent(_MountEvent):
    """Emit when CephFS share is ready to be mounted."""


class UmountShareEvent(_MountEvent):
    """Emit when CephFS share needs to be unmounted."""


class _CephFSRequiresEvents(CharmEvents):
    """Events that CephFS servers can emit."""

    server_connected = EventSource(ServerConnectedEvent)
    mount_share = EventSource(MountShareEvent)
    umount_share = EventSource(UmountShareEvent)


class ShareRequestedEvent(RelationEvent):
    """Emit when a consumer requests a new CephFS share be created by the provider."""

    @property
    def name(self) -> Optional[str]:
        """Get name of requested CephFS share."""
        return self.relation.data[self.relation.app].get("name")


class _CephFSProvidesEvents(CharmEvents):
    """Events that CephFS clients can emit."""

    share_requested = EventSource(ShareRequestedEvent)


class _BaseInterface(Object):
    """Base methods required for CephFS share integration interfaces."""

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


class CephFSRequires(_BaseInterface):
    """Consumer-side interface of CephFS share integrations."""

    on = _CephFSRequiresEvents()

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
        if "share_info" in transaction.added:
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
    ) -> None:
        """Request access to a CephFS share.

        Args:
            integration_id: Identifier for specific integration.
            name: Name of the CephFS share.

        Notes:
            Only application leader unit can request a CephFS share.
        """
        if self.unit.is_leader():

            params = {"name": name}
            _logger.debug(f"Requesting CephFS share with parameters {params}")
            self._update_data(integration_id, params)


class CephFSProvides(_BaseInterface):
    """Provider-side interface of CephFS share integrations."""

    on = _CephFSProvidesEvents()

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

    def set_share(self, integration_id: int, share_info: CephFSShareInfo) -> None:
        """Set share info for mounting a CephFS share.

        Args:
            integration_id: Identifier for specific integration.
            share_info: Information required to mount the CephFS share.

        Notes:
            Only application leader unit can set the CephFS share data.
        """
        if self.unit.is_leader():
            share_info = json.dumps(share_info.dict())
            _logger.debug(f"Exporting CephFS share with info {share_info}")
            self._update_data(integration_id, {"share_info": share_info})