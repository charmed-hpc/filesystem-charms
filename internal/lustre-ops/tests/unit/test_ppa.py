# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details

"""Unit tests for `lustre_ops.ppa`."""

from subprocess import CalledProcessError
from unittest.mock import MagicMock

import pytest
from charmlibs import apt
from lustre_ops import ppa
from lustre_ops.errors import RepositoryCodenameError, RepositoryGPGKeyError, RepositorySyncError
from pytest_mock import MockerFixture


@pytest.fixture(scope="function", autouse=True)
def mock_os_release(mocker: MockerFixture) -> MagicMock:
    """Mock platform.freedesktop_os_release."""
    return mocker.patch.object(
        ppa.platform,
        "freedesktop_os_release",
        return_value={"VERSION_CODENAME": "resolute"},
    )


class TestSetupLustreRepository:
    """setup_lustre_repository() tests."""

    def test_success(self, mocker: MockerFixture) -> None:
        """Repository is configured and the APT index refreshed."""
        mock_repo = mocker.patch.object(ppa.apt, "DebianRepository")
        mock_mapping = mocker.patch.object(ppa.apt, "RepositoryMapping").return_value
        mock_update = mocker.patch.object(ppa.apt, "update")

        ppa.setup_lustre_repository()

        mock_repo.assert_called_once()
        mock_repo.return_value.import_key.assert_called_once()
        mock_mapping.add.assert_called_once_with(mock_repo.return_value)
        mock_update.assert_called_once()

    def test_missing_version_codename(
        self, mocker: MockerFixture, mock_os_release: MagicMock
    ) -> None:
        """Failure to retrieve the OS codename raises error."""
        mock_os_release.return_value = {}
        mocker.patch.object(ppa.apt, "DebianRepository")

        with pytest.raises(RepositoryCodenameError):
            ppa.setup_lustre_repository()

    def test_gpg_key_error(self, mocker: MockerFixture) -> None:
        """GPG key import failure raises error."""
        mock_repo = mocker.patch.object(ppa.apt, "DebianRepository").return_value
        mock_repo.import_key.side_effect = apt.GPGKeyError("bad key")

        with pytest.raises(RepositoryGPGKeyError):
            ppa.setup_lustre_repository()

    def test_repo_add_error(self, mocker: MockerFixture) -> None:
        """Repository add/update failure raises error."""
        mocker.patch.object(ppa.apt, "DebianRepository")
        mock_mapping = mocker.patch.object(ppa.apt, "RepositoryMapping").return_value
        mock_mapping.add.side_effect = CalledProcessError(1, "apt")

        with pytest.raises(RepositorySyncError):
            ppa.setup_lustre_repository()

    def test_update_error(self, mocker: MockerFixture) -> None:
        """apt.update() failure raises error."""
        mocker.patch.object(ppa.apt, "DebianRepository")
        mocker.patch.object(ppa.apt, "RepositoryMapping")
        mocker.patch.object(ppa.apt, "update", side_effect=CalledProcessError(1, "apt update"))

        with pytest.raises(RepositorySyncError):
            ppa.setup_lustre_repository()
