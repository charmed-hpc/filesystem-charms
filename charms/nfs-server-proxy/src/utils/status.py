# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Track statuses of charm."""

import heapq
import json
import logging
from dataclasses import dataclass
from types import MappingProxyType
from typing import Callable, Dict, List, Literal, Optional, Tuple

from ops.charm import CharmBase
from ops.framework import Handle, Object, StoredStateData
from ops.model import ActiveStatus, StatusBase, UnknownStatus, WaitingStatus
from ops.storage import NoSnapshotError

_logger = logging.getLogger(__name__)
_status_priority = MappingProxyType(
    {
        "error": 6,
        "blocked": 5,
        "waiting": 4,
        "maintenance": 3,
        "active": 2,
        "unknown": 1,
    }
)


@dataclass
class Status:
    """A wrapper around `ops` StatusBase class."""

    label: str
    # Higher number is higher priority
    _priority: int = 0
    never_set: bool = True
    # The actual status of this Status object.
    # Use `self.set(...)` to update it.
    status: StatusBase = UnknownStatus()
    # If on_update is set, it will be called as a function with
    # no arguments whenever the status is set.
    on_update: Optional[Callable[[], None]] = None

    @property
    def priority(self) -> Tuple[int, int]:
        """Get global and local priority of status message."""
        return _status_priority[self.status.name], self._priority

    @property
    def message(self) -> Optional[str]:
        """Get status message."""
        if self.status.name == "unknown":
            return ""
        return self.status.message

    def set(self, status: StatusBase) -> None:
        """Set the status.

        Notes:
            Will also run the on_update hook if available
            Should be set by the status pool so the pool knows when it should update.
        """
        self.status = status
        self.never_set = False
        if self.on_update is not None:
            self.on_update()

    def dict(self) -> dict:
        """Convert Status to a dictionary."""
        return {"status": self.status.name, "message": self.message}


class StatusPool(Object):
    """Track current status of the NFS client charm."""

    def __init__(self, charm: CharmBase) -> None:
        """Init the status pool and restore from stored state if available.

        Notes:
            Instantiating more than one StatusPool object is not supported
            due to hardcoded framework stored data IDs.
        """
        super().__init__(charm, "status_pool")
        self._pool: Dict[str, Status] = {}
        self._charm = charm

        # Restore info from the charm's state.
        # This needs to be done on initialization to retain previous statuses that were set.
        charm.framework.register_type(StoredStateData, self, StoredStateData.handle_kind)
        stored_handle = Handle(self, StoredStateData.handle_kind, "_status_pool")

        try:
            self._state = charm.framework.load_snapshot(stored_handle)
            status_state = json.loads(self._state["statuses"])
        except NoSnapshotError:
            self._state = StoredStateData(self, "_status_pool")
            status_state = {}
        self._status_state = status_state

        charm.framework.observe(charm.framework.on.commit, self._on_commit)

    def add(self, status: Status) -> None:
        """Add a Status object to status pool."""
        if (
            status.never_set
            and status.label in self._status_state
            and status.label not in self._pool
        ):
            # If this status hasn't been seen or set yet, and there is a saved state for it,
            # then reconstitute it. This allows statuses to be retained across hook invocations.
            saved = self._status_state[status.label]
            if status.message != saved["message"]:
                status.status = StatusBase.from_name(saved["status"], saved["message"])

        self._pool[status.label] = status
        status.on_update = self.update
        self.update()

    def get(
        self,
        type_: Literal[
            "active", "blocked", "error", "maintenance", "waiting", "unknown", "all"
        ] = "all",
    ) -> List[Status]:
        """Get list of known statuses in pool based on type.

        Args:
            type_: Status types to retrieve. Default: "all".
        """
        heapq.nlargest(len(self._pool.values()), self._pool.values(), key=lambda x: x.priority)
        if type_ == "all":
            return list(self._pool.values())
        return [status for status in self._pool.values() if status.status.name == type_]

    def update(self) -> None:
        """Update the unit status by setting the highest priority status as the current status.

        Notes:
            Use as a hook to run whenever a status is updated in the pool.
        """
        if self._pool:
            status = heapq.nlargest(1, self._pool.values(), key=lambda x: x.priority)[0]
        else:
            status = None

        if status is None or status.status.name == "unknown":
            self._charm.unit.status = WaitingStatus("No status set")
        elif status.status.name == "active" and not status.message():
            self._charm.unit.status = ActiveStatus("")
        else:
            self._charm.unit.status = StatusBase.from_name(
                status.status.name, status.message if status.message else ""
            )

    def _on_commit(self, _) -> None:
        """Store the current state of statuses."""
        self._state["statuses"] = json.dumps(
            {status.label: status.dict() for status in self._pool.values()}
        )
        self._charm.framework.save_snapshot(self._state)
        self._charm.framework._storage.commit()
