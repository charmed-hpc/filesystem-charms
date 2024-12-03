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

""" """

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from ipaddress import AddressValueError, IPv6Address
from typing import Dict, List, Optional, TypeVar
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse, urlunsplit

import ops
from ops.charm import (
    CharmBase,
    CharmEvents,
    RelationChangedEvent,
    RelationDepartedEvent,
    RelationEvent,
    RelationJoinedEvent,
)
from ops.framework import EventSource, Object
from ops.model import Model, Relation

__all__ = [
    "FsInterfacesError",
    "ParseError",
    "Share",
    "ShareInfo",
    "NfsInfo",
    "MountShareEvent",
    "UmountShareEvent",
    "FSRequires",
    "FSProvides",
]

# The unique Charmhub library identifier, never change it
LIBID = "todo"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 1

_logger = logging.getLogger(__name__)


@dataclass
class _UriData:
    scheme: str
    user: str
    hosts: [str]
    path: str
    options: Dict[str, str]


def _parse_uri(uri: str) -> _UriData:
    _logger.debug(f"parsing `{uri}`")

    uri = urlparse(uri, allow_fragments=False)
    scheme = str(uri.scheme)
    if not scheme:
        raise ParseError("scheme cannot be empty")
    user = unquote(uri.username)
    hostname = unquote(uri.hostname)

    if not hostname or hostname[0] != "(" or hostname[-1] != ")":
        _logger.debug(f"parsing failed for hostname `{hostname}`")
        raise ParseError("invalid list of hosts for endpoint")

    hosts = hostname.split(",")
    if len(hosts) == 0:
        raise ParseError("list of hosts cannot be empty")
    path = uri.path
    if not path:
        raise ParseError("path cannot be empty")
    try:
        options = parse_qs(uri.query, strict_parsing=True)
    except ValueError:
        _logger.debug(f"parsing failed for query `{uri.query}`")
        raise ParseError("invalid options for endpoint info")

    return _UriData(scheme=scheme, user=user, hosts=hosts, path=path, options=options)


def _to_uri(scheme: str, hosts: [str], path: str, user="", options: Dict[str, str] = {}) -> str:
    user = quote(user)
    hostname = quote(",".join(hosts))
    netloc = f"{user}@" if user else "" + f"({hostname})"
    query = urlencode(options)

    return urlunsplit((scheme, netloc, path, query, None))


def _hostinfo(host: str) -> tuple[str, Optional[int]]:
    # IPv6
    if host.startswith("["):
        parts = iter(host[1:].split("]", maxsplit=1))
        host = next(parts)

        if (port := next(parts, None)) is None:
            raise ParseError("unclosed bracket for host")

        if not port:
            return host, None

        if not port.startswith(":"):
            raise ParseError("invalid syntax for host")

        try:
            port = int(port[1:])
        except ValueError:
            raise ParseError("invalid port on host")

        return host, port

    # IPv4 or hostname
    parts = iter(host.split(":", maxsplit=1))
    host = next(parts)
    if (port := next(parts, None)) is None:
        return host, None

    try:
        port = int(port)
    except ValueError:
        raise ParseError("invalid port on host")

    return host, port


class FsInterfacesError(Exception):
    """Exception raised when a filesystem operation failed."""


class ParseError(FsInterfacesError): ...


T = TypeVar("T", bound="ShareInfo")


class ShareInfo(ABC):
    @classmethod
    @abstractmethod
    def from_uri(cls: type[T], uri: str, model: Model) -> T: ...

    @abstractmethod
    def to_uri(self, model: Model) -> str: ...

    @classmethod
    @abstractmethod
    def fs_type(cls) -> str: ...


@dataclass(frozen=True)
class NfsInfo(ShareInfo):
    hostname: str
    port: Optional[int]
    path: str

    @classmethod
    def from_uri(cls, uri: str, _model: Model) -> "NfsInfo":
        info = _parse_uri(uri)

        if info.scheme != "nfs":
            raise ParseError(
                "could not parse `EndpointInfo` with incompatible scheme into `NfsInfo`"
            )

        if info.user:
            _logger.warning("ignoring user info on nfs endpoint info")

        if len(info.hosts) > 1:
            _logger.info("multiple hosts specified. selecting the first one")

        if info.options:
            _logger.warning("ignoring endpoint options on nfs endpoint info")

        hostname, port = _hostinfo(info.hosts[0])
        path = info.path
        return NfsInfo(hostname=hostname, port=port, path=path)

    def to_uri(self, _model: Model) -> str:
        try:
            IPv6Address(self.hostname)
            host = f"[{self.hostname}]"
        except AddressValueError:
            host = self.hostname

        hosts = [host + f":{self.port}" if self.port else ""]

        return _to_uri(scheme="nfs", hosts=hosts, path=self.path)

    @classmethod
    def fs_type(cls) -> str:
        return "nfs"


def _uri_to_share_info(uri: str, model: Model) -> ShareInfo:
    match uri.split("://", maxsplit=1)[0]:
        case "nfs":
            return NfsInfo.from_uri(uri, model)
        case _:
            raise FsInterfacesError("unsupported share type")


class _MountEvent(RelationEvent):
    """Base event for mount-related events."""

    @property
    def share_info(self) -> Optional[ShareInfo]:
        """Get mount info."""
        if not (uri := self.relation.data[self.relation.app].get("endpoint")):
            return
        return _uri_to_share_info(uri, self.framework.model)


class MountShareEvent(_MountEvent):
    """Emit when FS share is ready to be mounted."""


class UmountShareEvent(_MountEvent):
    """Emit when FS share needs to be unmounted."""


class _FSRequiresEvents(CharmEvents):
    """Events that FS servers can emit."""

    mount_share = EventSource(MountShareEvent)
    umount_share = EventSource(UmountShareEvent)

@dataclass
class Share:
    info: ShareInfo
    uri: str

class _BaseInterface(Object):
    """Base methods required for FS share integration interfaces."""

    def __init__(self, charm: CharmBase, relation_name) -> None:
        super().__init__(charm, relation_name)
        self.charm = charm
        self.app = charm.model.app
        self.unit = charm.unit
        self.relation_name = relation_name

    @property
    def relations(self) -> List[Relation]:
        """Get list of active relations associated with the relation name."""
        result = []
        for relation in self.charm.model.relations[self.relation_name]:
            try:
                _ = repr(relation.data)
                result.append(relation)
            except RuntimeError:
                pass
        return result


class FSRequires(_BaseInterface):
    """Consumer-side interface of FS share integrations."""

    on = _FSRequiresEvents()

    def __init__(self, charm: CharmBase, relation_name: str) -> None:
        super().__init__(charm, relation_name)
        self.framework.observe(charm.on[relation_name].relation_changed, self._on_relation_changed)
        self.framework.observe(
            charm.on[relation_name].relation_departed, self._on_relation_departed
        )

    def _on_relation_changed(self, event: RelationChangedEvent) -> None:
        """Handle when the databag between client and server has been updated."""
        _logger.debug("Emitting `MountShare` event from `RelationChanged` hook")
        self.on.mount_share.emit(event.relation, app=event.app, unit=event.unit)

    def _on_relation_departed(self, event: RelationDepartedEvent) -> None:
        """Handle when server departs integration."""
        _logger.debug("Emitting `UmountShare` event from `RelationDeparted` hook")
        self.on.umount_share.emit(event.relation, app=event.app, unit=event.unit)

    @property
    def shares(self) -> List[Share]:
        result = []
        for relation in self.relations:
            if not (uri := relation.data[relation.app].get("endpoint")):
                pass
            result.append(Share(info=_uri_to_share_info(uri, self.model), uri=uri))
        return result


class FSProvides(_BaseInterface):
    """Provider-side interface of FS share integrations."""

    def __init__(self, charm: CharmBase, relation_name: str, peer_relation_name: str) -> None:
        super().__init__(charm, relation_name)
        self._peer_relation_name = peer_relation_name
        self.framework.observe(charm.on[relation_name].relation_joined, self._update_relation)

    def set_share(self, share_info: ShareInfo) -> None:
        """Set info for mounting a FS share.

        Args:
            share_info: Information required to mount the FS share.

        Notes:
            Only the application leader unit can set the FS share data.
        """
        if not self.unit.is_leader():
            return

        uri = share_info.to_uri(self.charm.model)

        self._endpoint = uri

        for relation in self.relations:
            relation.data[self.app]["endpoint"] = uri

    def _update_relation(self, event: RelationJoinedEvent) -> None:
        if not (endpoint := self._endpoint):
            return

        event.relation.data[self.app]["endpoint"] = endpoint

    @property
    def _peers(self) -> Optional[ops.Relation]:
        """Fetch the peer relation."""
        return self.model.get_relation(self._peer_relation_name)

    @property
    def _endpoint(self) -> str:
        endpoint = self._get_state("endpoint")
        return "" if endpoint is None else endpoint

    @_endpoint.setter
    def _endpoint(self, endpoint: str) -> None:
        self._set_state("endpoint", endpoint)

    def _get_state(self, key: str) -> Optional[str]:
        """Get a value from the global state."""
        if not self._peers:
            return None

        return self._peers.data[self.app].get(key)

    def _set_state(self, key: str, data: str) -> None:
        """Insert a value into the global state."""
        if not self._peers:
            raise FsInterfacesError(
                "Peer relation can only be accessed after the relation is established"
            )

        self._peers.data[self.app][key] = data
