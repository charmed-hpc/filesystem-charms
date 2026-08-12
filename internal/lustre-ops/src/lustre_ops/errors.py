# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details

"""Exceptions raised by the `lustre_ops` package."""


class LustreOpsError(Exception):
    """Base class for errors raised by the `lustre_ops` package."""


class LNetError(LustreOpsError):
    """Raised when an LNet operation fails."""


class LNetAddInterfaceError(LNetError):
    """Raised when an LNet interface addition operation fails."""


class LNetAddNetworkError(LNetError):
    """Raised when an LNet network addition operation fails."""


class LNetAutodetectError(LNetError):
    """Raised when an LNet auto-detection operation fails."""


class LNetConfigExportError(LNetError):
    """Raised when an LNet configuration export operation fails."""


class LNetParseError(LNetError):
    """Raised when an LNet parsing operation fails."""


class LNetRemoveInterfaceError(LNetError):
    """Raised when an LNet interface removal operation fails."""


class LNetQueryError(LNetError):
    """Raised when an LNet query operation fails."""


class RepositoryError(LNetError):
    """Raised when a Lustre package repository operation fails."""


class RepositoryCodenameError(RepositoryError):
    """Raised when a Lustre package repository codename determination fails."""


class RepositoryGPGKeyError(RepositoryError):
    """Raised when a Lustre package repository GPG key operation fails."""


class RepositorySyncError(RepositoryError):
    """Raised when a Lustre package repository synchronization operation fails."""
