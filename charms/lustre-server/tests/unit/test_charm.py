# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Lustre charm unit tests."""

import importlib
from subprocess import CalledProcessError
from unittest.mock import MagicMock

import charm
import pytest
from charmlibs.apt import GPGKeyError, PackageError
from constants import LUSTRE_FSNAME, LUSTRE_PACKAGES
from errors import LustreFilesystemError, LustrePeerError
from lustre_peer import LustrePeerAppData
from ops import testing
from pytest_mock import MockerFixture

APP_NAME = "lustre-test"


@pytest.fixture(scope="function")
def ctx() -> testing.Context[charm.LustreCharm]:
    """Mock charm context."""
    return testing.Context(charm.LustreCharm, app_name=APP_NAME)


class TestCharmInstall:
    """Install handler tests."""

    @pytest.fixture(scope="function")
    def mock_apt(self, mocker: MockerFixture) -> MagicMock:
        """Mock apt module."""
        return mocker.patch("charm.apt", autospec=True)

    @pytest.fixture(scope="function")
    def mock_os_release(self, mocker: MockerFixture) -> MagicMock:
        """Mock platform.freedesktop_os_release."""
        return mocker.patch(
            "platform.freedesktop_os_release", return_value={"VERSION_CODENAME": "resolute"}
        )

    @pytest.fixture(scope="function")
    def mock_lustre_init(self, mocker: MockerFixture) -> MagicMock:
        """Mock lustre_fs.init."""
        return mocker.patch("charm.lustre_fs.init", autospec=True)

    def test_success(
        self,
        ctx: testing.Context[charm.LustreCharm],
        mocker: MockerFixture,
        mock_apt: MagicMock,
        mock_os_release: MagicMock,
        mock_lustre_init: MagicMock,
    ) -> None:
        """Successful install."""
        out = ctx.run(ctx.on.install(), testing.State())
        assert out.unit_status == testing.MaintenanceStatus(charm.CharmStatuses.PREPARING_SERVICES)

    def test_missing_version_codename(
        self,
        ctx: testing.Context[charm.LustreCharm],
        mocker: MockerFixture,
        mock_os_release: MagicMock,
        mock_lustre_init: MagicMock,
    ) -> None:
        """OS version codename retrieval fails."""
        mock_os_release.return_value = {}

        out = ctx.run(ctx.on.install(), testing.State())
        assert out.unit_status == testing.BlockedStatus(charm.CharmStatuses.FAILED_OS_CODENAME)

    def test_repo_gpg_error(
        self,
        ctx: testing.Context[charm.LustreCharm],
        mocker: MockerFixture,
        mock_os_release: MagicMock,
        mock_lustre_init: MagicMock,
    ) -> None:
        """GPG key import fails."""
        mocker.patch("charm.apt.RepositoryMapping")
        mocker.patch("charm.apt.update")

        mock_repo = mocker.patch("charm.apt.DebianRepository").return_value
        mock_repo.import_key.side_effect = GPGKeyError("bad key")

        out = ctx.run(ctx.on.install(), testing.State())
        assert out.unit_status == testing.BlockedStatus(charm.CharmStatuses.FAILED_IMPORT_GPG_KEY)

    def test_repo_update_error(
        self,
        ctx: testing.Context[charm.LustreCharm],
        mocker: MockerFixture,
        mock_apt: MagicMock,
        mock_os_release: MagicMock,
        mock_lustre_init: MagicMock,
    ) -> None:
        """Repository update fails."""
        mock_apt.update.side_effect = CalledProcessError(1, "bad cmd")

        out = ctx.run(ctx.on.install(), testing.State())
        assert out.unit_status == testing.BlockedStatus(charm.CharmStatuses.FAILED_ADD_REPO)

    def test_packages_error(
        self,
        ctx: testing.Context[charm.LustreCharm],
        mocker: MockerFixture,
        mock_os_release: MagicMock,
        mock_lustre_init: MagicMock,
    ) -> None:
        """Package installation fails."""
        mocker.patch("charm.apt.RepositoryMapping")
        mocker.patch("charm.apt.DebianRepository")
        mocker.patch("charm.apt.update")

        mocker.patch("charm.apt.add_package", side_effect=PackageError("bad package"))

        out = ctx.run(ctx.on.install(), testing.State())
        expected_message = charm.CharmStatuses.failed_install(LUSTRE_PACKAGES)
        assert out.unit_status == testing.BlockedStatus(expected_message)

    def test_lustre_init_error(
        self,
        ctx: testing.Context[charm.LustreCharm],
        mocker: MockerFixture,
        mock_apt: MagicMock,
        mock_os_release: MagicMock,
        mock_lustre_init: MagicMock,
    ) -> None:
        """Lustre init fails."""
        mock_lustre_init.side_effect = LustreFilesystemError("")

        out = ctx.run(ctx.on.install(), testing.State())
        assert out.unit_status == testing.BlockedStatus(charm.CharmStatuses.FAILED_LNET_INIT)


class TestCharmStart:
    """Start handler tests."""

    @pytest.fixture(scope="function", autouse=True)
    def mock_refresh(self, mocker: MockerFixture) -> MagicMock:
        """Mock hook for refresh decorator."""
        mocked = mocker.patch("state.check_lustre", autospec=True)
        mocked.return_value = testing.ActiveStatus("test status")
        # Decorators applied at import time so module must be reloaded after mocking refresh hook.
        importlib.reload(charm)
        return mocked

    @pytest.fixture(scope="function")
    def mock_mgs_mds_setup(self, mocker: MockerFixture) -> MagicMock:
        """Mock mgs_mds_setup."""
        return mocker.patch("charm.lustre_fs.mgs_mds_setup", autospec=True)

    @pytest.fixture(scope="function")
    def mock_oss_setup(self, mocker: MockerFixture) -> MagicMock:
        """Mock lustre_fs.oss_setup."""
        return mocker.patch("charm.lustre_fs.oss_setup", autospec=True)

    @pytest.fixture(scope="function")
    def mock_peer_observer(self, mocker: MockerFixture) -> MagicMock:
        """Mock LustrePeerObserver."""
        return mocker.patch("charm.LustrePeerObserver", autospec=True)

    def test_leader_initial_deployment(
        self,
        ctx: testing.Context[charm.LustreCharm],
        mocker: MockerFixture,
        mock_mgs_mds_setup: MagicMock,
        mock_peer_observer: MagicMock,
    ) -> None:
        """Leader with no MGS published: MGS+MDS successful start."""
        nid = "10.0.0.1@tcp"
        mock_peer_observer.return_value.get_app_data.return_value = LustrePeerAppData()
        mock_peer_observer.return_value.mgs_nid_published.return_value = nid

        ctx.run(ctx.on.start(), testing.State(leader=True))

        mock_mgs_mds_setup.assert_called_once_with(LUSTRE_FSNAME)

    def test_non_leader_initial_deployment(
        self,
        ctx: testing.Context[charm.LustreCharm],
        mock_mgs_mds_setup: MagicMock,
        mock_oss_setup: MagicMock,
        mock_peer_observer: MagicMock,
    ) -> None:
        """Non-leader with no MGS published: OSS waits."""
        mock_peer_observer.return_value.get_app_data.return_value = LustrePeerAppData()

        ctx.run(ctx.on.start(), testing.State(leader=False))

        # No action should be taken.
        mock_mgs_mds_setup.assert_not_called()
        mock_oss_setup.assert_not_called()

    def test_restart_mgs_unit(
        self,
        ctx: testing.Context[charm.LustreCharm],
        mocker: MockerFixture,
        mock_mgs_mds_setup: MagicMock,
        mock_peer_observer: MagicMock,
    ) -> None:
        """MGS already published. This unit is the MGS."""
        app_data = LustrePeerAppData(mgs_nid="10.0.0.1@tcp", mgs_unit_name=f"{APP_NAME}/0")
        mock_peer_observer.return_value.get_app_data.return_value = app_data

        ctx.run(ctx.on.start(), testing.State(leader=True))

        mock_mgs_mds_setup.assert_called_once_with(LUSTRE_FSNAME)

    def test_restart_oss_unit(
        self,
        ctx: testing.Context[charm.LustreCharm],
        mock_oss_setup: MagicMock,
        mock_peer_observer: MagicMock,
    ) -> None:
        """MGS already published. This unit is an OSS."""
        nid = "10.0.0.1@tcp"
        app_data = LustrePeerAppData(mgs_nid=nid, mgs_unit_name=f"{APP_NAME}/1")
        mock_peer_observer.return_value.get_app_data.return_value = app_data

        ctx.run(ctx.on.start(), testing.State(leader=True))

        mock_oss_setup.assert_called_once_with(LUSTRE_FSNAME, f"{APP_NAME}/0", nid)

    def test_peer_app_data_error(
        self, ctx: testing.Context[charm.LustreCharm], mock_peer_observer: MagicMock
    ) -> None:
        """Fails to retrieve peer relation application data."""
        mock_peer_observer.return_value.get_app_data.side_effect = LustrePeerError("get failed")

        out = ctx.run(ctx.on.start(), testing.State(leader=True))

        assert out.unit_status == testing.BlockedStatus(charm.CharmStatuses.FAILED_PEER_DATA)

    def test_leader_initial_deployment_mgs_mds_setup_error(
        self,
        ctx: testing.Context[charm.LustreCharm],
        mocker: MockerFixture,
        mock_mgs_mds_setup: MagicMock,
        mock_peer_observer: MagicMock,
    ) -> None:
        """Leader initial deployment: mgs_mds_setup fails."""
        mock_peer_observer.return_value.get_app_data.return_value = LustrePeerAppData()
        mock_mgs_mds_setup.side_effect = LustreFilesystemError("zpool failed")

        out = ctx.run(ctx.on.start(), testing.State(leader=True))

        assert out.unit_status == testing.BlockedStatus(charm.CharmStatuses.FAILED_MGS_MDS_SETUP)

    def test_leader_initial_deployment_mgs_nid_published_error(
        self,
        ctx: testing.Context[charm.LustreCharm],
        mocker: MockerFixture,
        mock_mgs_mds_setup: MagicMock,
        mock_peer_observer: MagicMock,
    ) -> None:
        """Leader initial deployment: mgs_nid_published fails."""
        mock_peer_observer.return_value.get_app_data.return_value = LustrePeerAppData()
        mock_peer_observer.return_value.mgs_nid_published.side_effect = LustrePeerError(
            "NID failed"
        )

        out = ctx.run(ctx.on.start(), testing.State(leader=True))

        assert out.unit_status == testing.BlockedStatus(charm.CharmStatuses.FAILED_MGS_MDS_SETUP)

    def test_restart_mgs_unit_setup_error(
        self,
        ctx: testing.Context[charm.LustreCharm],
        mock_mgs_mds_setup: MagicMock,
        mock_peer_observer: MagicMock,
    ) -> None:
        """MGS unit restart: mgs_mds_setup fails."""
        app_data = LustrePeerAppData(mgs_nid="10.0.0.1@tcp", mgs_unit_name=f"{APP_NAME}/0")
        mock_peer_observer.return_value.get_app_data.return_value = app_data
        mock_mgs_mds_setup.side_effect = LustreFilesystemError("mount failed")

        out = ctx.run(ctx.on.start(), testing.State(leader=True))

        assert out.unit_status == testing.BlockedStatus(charm.CharmStatuses.FAILED_SERVICE_SETUP)

    def test_restart_oss_unit_setup_error(
        self,
        ctx: testing.Context[charm.LustreCharm],
        mock_oss_setup: MagicMock,
        mock_peer_observer: MagicMock,
    ) -> None:
        """OSS unit restart: oss_setup fails."""
        nid = "10.0.0.1@tcp"
        app_data = LustrePeerAppData(mgs_nid=nid, mgs_unit_name=f"{APP_NAME}/1")
        mock_peer_observer.return_value.get_app_data.return_value = app_data
        mock_oss_setup.side_effect = LustreFilesystemError("zpool failed")

        out = ctx.run(ctx.on.start(), testing.State(leader=True))

        assert out.unit_status == testing.BlockedStatus(charm.CharmStatuses.FAILED_SERVICE_SETUP)
