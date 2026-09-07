#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Exceptions used within the charm."""

import ops


class LustreError(Exception):
    """Base class for Lustre-related errors."""


class LustreFilesystemError(LustreError):
    """Raised when a Lustre file system operation fails."""


class LustrePeerError(LustreError):
    """Raised when a Lustre peer relation operation fails."""


class LustrePeerDuplicateMgsError(LustrePeerError):
    """Raised when more than one unit attempts to run the MGS+MDS services."""


class LustreStateError(LustreError):
    """Raised when a Lustre status check fails, carrying the resulting unit status."""

    def __init__(self, status: ops.StatusBase):
        super().__init__(status.message)
        self.status = status
