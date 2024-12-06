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

"""Library to manage integrations between filesystem providers and consumers.

This library contains the FsProvides and FsRequires classes for managing an
integration between a filesystem server operator and a filesystem client operator.

## ShareInfo (filesystem mount data)

This abstract class defines the methods that a filesystem type must expose for providers and
consumers. Any subclass of this class will be compatible with the other methods exposed
by the interface library, but the server and the client are the ones responsible for deciding which
filesystems to support.

## FsRequires (filesystem client)

This class provides a uniform interface for charms that need to mount or unmount filesystem shares,
and convenience methods for consuming data sent by a filesystem server charm.

### Defined events

- `mount_share`: Event emitted when the filesystem is ready to be mounted.
- `umount_share`: Event emitted when the filesystem needs to be unmounted.

### Example

``python
import ops
from charms.storage_libs.v0.fs_interfaces import (
    FsRequires,
    MountShareEvent,
)


class StorageClientCharm(ops.CharmBase):
    # Application charm that needs to mount filesystem shares.

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        # Charm events defined in the FsRequires class.
        self.fs_share = FsRequires(self, "fs-share")
        self.framework.observe(
            self.fs_share.on.mount_share,
            self._on_mount_share,
        )

    def _on_server_connected(self, event: MountShareEvent) -> None:
        # Handle when new filesystem server is connected.

        share_info = event.share_info

        self.mount("/mnt", share_info)

        self.unit.status = ops.ActiveStatus("Mounted share at `/mnt`.")
```

## FsProvides (filesystem server)

This library provides a uniform interface for charms that expose filesystem shares.

> __Note:__ It is the responsibility of the filesystem Provider charm to provide
> the implementation for creating a new filesystem share. FsProvides just provides
> the interface for the integration.

### Example

```python
import ops
from charms.storage_client.v0.fs_interfaces import (
    FsProvides,
    NfsInfo,
)

class StorageServerCharm(ops.CharmBase):
    def __init__(self, framework: ops.Framework):
        super().__init__(framework)
        self._fs_share = FsProvides(self, "fs-share", "server-peers")
        framework.observe(self.on.start, self._on_start)

    def _on_start(self, event: ops.StartEvent):
        # Handle start event.
        self._fs_share.set_fs_info(NfsInfo("192.168.1.254", 65535, "/srv"))
        self.unit.status = ops.ActiveStatus()
```
"""

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
    "FsRequires",
    "FsProvides",
]

# The unique Charmhub library identifier, never change it
LIBID = "todo"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 1

_logger = logging.getLogger(__name__)

class FsInterfacesError(Exception):
    """Exception raised when a filesystem operation failed."""


class ParseError(FsInterfacesError):
    """Exception raised when a parse operation from an URI failed."""

# Design-wise, this class represents the grammar that relations use to
# share data between providers and requirers:
#
# key = 1*( unreserved )
# value = 1*( unreserved / ":" / "/" / "?" / "#" / "[" / "]" / "@" / "!" / "$"
#       / "'" / "(" / ")" / "*" / "+" / "," / ";" )
# options = key "=" value ["&" options]
# host-port = host [":" port]
# hosts = host-port [',' hosts]
# authority = [userinfo "@"] "(" hosts ")"
# URI = scheme "://" authority path-absolute ["?" options]
#
# Unspecified grammar rules are given by [RFC 3986](https://datatracker.ietf.org/doc/html/rfc3986#appendix-A).
# 
# This essentially leaves 5 components that the library can use to share data:
# - scheme: representing the type of filesystem.
# - hosts: representing the list of hosts where the filesystem lives. For NFS it should be a single element,
#   but CephFS and Lustre use more than one endpoint.
# - user: Any kind of authentication user that the client must specify to mount the filesystem.
# - path: The internally exported path of each filesystem. Could be optional if a filesystem exports its
#   whole tree, but at the very least NFS, CephFS and Lustre require an export path.
# - options: Some filesystems will require additional options for its specific mount command (e.g. Ceph).
#
# Putting all together, this allows sharing the required data using simple URI strings:
# ```
# <scheme>://<user>@(<host>,*)/<path>/?<options>
#
# nfs://(192.168.1.1:65535)/export
# ceph://fsuser@(192.168.1.1,192.168.1.2,192.168.1.3)/export?fsid=asdf1234&auth=plain:QWERTY1234&filesystem=fs_name
# ceph://fsuser@(192.168.1.1,192.168.1.2,192.168.1.3)/export?fsid=asdf1234&auth=secret:YXNkZnF3ZXJhc2RmcXdlcmFzZGZxd2Vy&filesystem=fs_name
# lustre://(192.168.227.11%40tcp1,192.168.227.12%40tcp1)/export
# ```
#
# Note how in the Lustre URI we needed to escape the `@` symbol on the hosts to conform with the URI syntax.
@dataclass(init=False, frozen=True)
class _UriData:
    """Raw data from the endpoint URI of a relation."""
    scheme: str
    """Scheme used to identify a filesystem.

    This will mostly correspond to the option `fstype` for the `mount` command.
    """

    hosts: [str]
    """List of hosts where the filesystem is deployed on."""

    user: str
    """User to connect to the filesystem."""

    path: str
    """Path exported by the filesystem."""

    options: dict[str, str]
    """Additional options that could be required to mount the filesystem."""

    def __init__(self, scheme: str, hosts: [str], user: str = "", path: str = "", options: dict[str, str] = {}):
        if not scheme:
            raise FsInterfacesError("scheme cannot be empty")
        if len(hosts) == 0:
            raise FsInterfacesError("list of hosts cannot be empty")

        # Strictly convert to the required types to avoid passing through weird data.
        self.scheme = str(scheme)
        self.hosts = [str(host) for host in hosts]
        self.user = str(user) if user else ""
        self.path = str(path) if path else "/"
        self.options = { str(k): str(v) for k,v in options.items() } if options else {}

    @classmethod
    def from_uri(uri: str) -> _UriData:
        """Convert an URI string into a `_UriData`."""

        _logger.debug(f"parsing `{uri}`")

        uri = urlparse(uri, allow_fragments=False)
        scheme = str(uri.scheme)
        user = unquote(uri.username)
        hostname = unquote(uri.hostname)

        if not hostname or hostname[0] != "(" or hostname[-1] != ")":
            _logger.debug(f"parsing failed for hostname `{hostname}`")
            raise ParseError("invalid list of hosts for endpoint")

        hosts = hostname.split(",")
        path = uri.path
        try:
            options = parse_qs(uri.query, strict_parsing=True)
        except ValueError:
            _logger.debug(f"parsing failed for query `{uri.query}`")
            raise ParseError("invalid options for endpoint info")
        try:
            return _UriData(scheme=scheme, user=user, hosts=hosts, path=path, options=options)
        except FsInterfacesError as e:
            raise ParseError(*e.args)

    def __str__(self) -> str:
        user = quote(self.user)
        hostname = quote(",".join(self.hosts))
        netloc = f"{user}@" if user else "" + f"({self.hostname})"
        query = urlencode(self.options)
        return urlunsplit((self.scheme, netloc, self.path, query, None))


def _hostinfo(host: str) -> tuple[str, Optional[int]]:
    """Parse a host string into the hostname and the port."""
    if len(host) == 0:
        raise ParseError("invalid empty host")

    pos = 0
    if host[pos] == "[":
        # IPv6
        pos = host.find(']', pos)
        if pos == -1:
            raise ParseError("unclosed bracket for host")
        hostname = host[1:pos]
        pos = pos + 1
    else:
        # IPv4 or DN
        pos = host.find(':', pos)
        if pos == -1:
            pos = len(host)
        hostname = host[:pos]
    
    if pos == len(host):
        return hostname, None

    # more characters after the hostname <==> port
    
    if hostname[pos] != ":":
        raise ParseError("expected `:` after IPv6 address")
    try:
        port = int(host[pos + 1:])
    except ValueError:
        raise ParseError("expected int after `:` in host")


T = TypeVar("T", bound="ShareInfo")


class FsInfo(ABC):
    """Information to mount a filesystem.

    This is an abstract class that exposes a set of required methods. All filesystems that
    can be handled by this library must derive this abstract class. 
    """
    @classmethod
    @abstractmethod
    def from_uri(cls: type[T], uri: str, model: Model) -> T:
        """Convert an URI string into a `FsInfo` object."""

    @abstractmethod
    def to_uri(self, model: Model) -> str:
        """Convert this `FsInfo` object into an URI string."""

    @abstractmethod
    def grant(self, model: Model, relation: ops.Relation):
        """Grant permissions for a certain relation to any secrets that this `FsInfo` has.

        This is an optional method because not all filesystems will require secrets to
        be mounted on the client.
        """
        return

    @classmethod
    @abstractmethod
    def fs_type(cls) -> str:
        """Get the string identifier of this filesystem type."""

@dataclass(frozen=True)
class NfsInfo(FsInfo):
    """Information required to mount an NFS share."""

    hostname: str
    """Hostname where the NFS server can be reached."""

    port: Optional[int]
    """Port where the NFS server can be reached."""

    path: str
    """Path exported by the NFS server."""

    @classmethod
    def from_uri(cls, uri: str, _model: Model) -> "NfsInfo":
        info = _UriData.from_uri(uri)

        if info.scheme != cls.fs_type():
            raise ParseError(
                "could not parse `EndpointInfo` with incompatible scheme into `NfsInfo`"
            )
        
        path = info.path

        if info.user:
            _logger.warning("ignoring user info on nfs endpoint info")

        if len(info.hosts) > 1:
            _logger.info("multiple hosts specified. selecting the first one")

        if info.options:
            _logger.warning("ignoring endpoint options on nfs endpoint info")

        hostname, port = _hostinfo(info.hosts[0])
        return NfsInfo(hostname=hostname, port=port, path=path)

    def to_uri(self, _model: Model) -> str:
        try:
            IPv6Address(self.hostname)
            host = f"[{self.hostname}]"
        except AddressValueError:
            host = self.hostname

        hosts = [host + f":{self.port}" if self.port else ""]

        return str(_UriData(scheme=self.fs_type(), hosts=hosts, path=self.path))

    @classmethod
    def fs_type(cls) -> str:
        return "nfs"

@dataclass(frozen=True)
class CephfsInfo(FsInfo):
    """Information required to mount a CephFS share."""
    
    fsid: str
    """Cluster identifier."""

    name: str
    """Name of the exported filesystem."""

    path: str
    """Path exported within the filesystem."""

    monitor_hosts: [str]
    """List of reachable monitor hosts."""

    user: str
    """Ceph user authorized to access the filesystem."""

    key: str
    """Cephx key for the authorized user."""
    
    @classmethod
    def from_uri(cls, uri: str, model: Model) -> "CephfsInfo":
        info = _parse_uri(uri)

        if info.scheme != cls.fs_type():
            raise ParseError(
                "could not parse `EndpointInfo` with incompatible scheme into `CephfsInfo`"
            )

        path = info.path
        
        if not (user := info.user):
            raise ParseError(
                "missing user in uri for `CephfsInfo" 
            )
        
        if not (name := info.options.get("name")):
            raise ParseError(
                "missing name in uri for `CephfsInfo`"
            )
        
        if not (fsid := info.options.get("fsid")):
            raise ParseError(
                "missing fsid in uri for `CephfsInfo`"
            )
        
        monitor_hosts = info.hosts

        if not (auth := info.options.get("auth")):
            raise ParseError(
                "missing auth info in uri for `CephsInfo`"
            )

        try:
            kind, data = auth.split(":", 1)
        except ValueError:
            raise ParseError("Could not get the kind of auth info")
        
        if kind == "secret":
            key = model.get_secret(id=auth).get_content(refresh=True)["key"]
        elif kind == "plain":
            key = data
        else:
            raise ParseError("Invalid kind for auth info")
        
        return CephfsInfo(fsid=fsid, name=name, path=path, monitor_hosts=monitor_hosts, user=user, key=key)

    def to_uri(self, model: Model) -> str:
        secret = self._get_or_create_auth_secret(model)

        options = {
            "fsid": self.fsid,
            "name": self.name,
            "auth": secret.id,
            "auth-rev": str(secret.get_info().revision),
        }

        return str(_UriData(scheme=self.fs_type(), hosts=self.monitor_hosts, path=self.path, user=self.user, options=options))
    
    @abstractmethod
    def grant(self, model: Model, relation: Relation):
        self._get_or_create_auth_secret(model)

        secret.grant(relation)

    @classmethod
    def fs_type(cls) -> str:
        return "ceph"
    
    def _get_or_create_auth_secret(self, model: Model) -> ops.Secret:
        try:
            secret = model.get_secret(label="auth")
            secret.set_content({"key": self.key})
        except ops.SecretNotFoundError:
            secret = model.app.add_secret(
                self.key,
                label="auth",
                description="Cephx key to authenticate against the CephFS share"
            )
        return secret

@dataclass
class Endpoint:
    """Endpoint data exposed by a filesystem server."""
    
    fs_info: FsInfo
    """Filesystem information required to mount this endpoint."""

    uri: str
    """Raw URI exposed by this endpoint."""

def _uri_to_share_info(uri: str, model: Model) -> FsInfo:
    match uri.split("://", maxsplit=1)[0]:
        case "nfs":
            return NfsInfo.from_uri(uri, model)
        case "ceph":
            return CephfsInfo.from_uri(uri, model)
        case _:
            raise FsInterfacesError("unsupported share type")



class _MountEvent(RelationEvent):
    """Base event for mount-related events."""

    @property
    def endpoint(self) -> Optional[Endpoint]:
        """Get endpoint info."""
        if not (uri := self.relation.data[self.relation.app].get("endpoint")):
            return
        return Endpoint(_uri_to_share_info(uri, self.framework.model), uri)


class MountShareEvent(_MountEvent):
    """Emit when FS share is ready to be mounted."""


class UmountShareEvent(_MountEvent):
    """Emit when FS share needs to be unmounted."""


class _FsRequiresEvents(CharmEvents):
    """Events that FS servers can emit."""

    mount_share = EventSource(MountShareEvent)
    umount_share = EventSource(UmountShareEvent)



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


class FsRequires(_BaseInterface):
    """Consumer-side interface of filesystem integrations."""

    on = _FsRequiresEvents()

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
    def endpoints(self) -> List[Endpoint]:
        """List of endpoints exposed by all the relations of this charm."""
        result = []
        for relation in self.relations:
            if not (uri := relation.data[relation.app].get("endpoint")):
                pass
            result.append(Endpoint(fs_info=_uri_to_share_info(uri, self.model), uri=uri))
        return result


class FsProvides(_BaseInterface):
    """Provider-side interface of filesystem integrations."""

    def __init__(self, charm: CharmBase, relation_name: str, peer_relation_name: str) -> None:
        super().__init__(charm, relation_name)
        self._peer_relation_name = peer_relation_name
        self.framework.observe(charm.on[relation_name].relation_joined, self._update_relation)

    def set_fs_info(self, fs_info: FsInfo) -> None:
        """Set information to mount a filesystem.

        Args:
            share_info: Information required to mount the filesystem.

        Notes:
            Only the application leader unit can set the filesystem data.
        """
        if not self.unit.is_leader():
            return

        uri = fs_info.to_uri(self.model)

        self._endpoint = uri

        for relation in self.relations:
            fs_info.grant(self.model, relation)
            relation.data[self.app]["endpoint"] = uri

    def _update_relation(self, event: RelationJoinedEvent) -> None:
        if not self.unit.is_leader() or not (endpoint := self._endpoint):
            return

        share_info = _uri_to_share_info(endpoint, self.model)
        share_info.grant(self.model, event.relation)

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
