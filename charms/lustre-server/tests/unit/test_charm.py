# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Lustre charm unit tests."""

import importlib
from unittest.mock import MagicMock

import charm
import pytest
from charmlibs.apt import PackageError
from constants import LUSTRE_FSNAME, LUSTRE_PACKAGES
from errors import LustreFilesystemError, LustrePeerError
from lustre_ops.errors import LNetError, RepositoryError
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
    def mock_repo_setup(self, mocker: MockerFixture) -> MagicMock:
        """Mock lustre-ops PPA setup."""
        return mocker.patch("charm.ppa.setup_lustre_repository", autospec=True)

    @pytest.fixture(scope="function")
    def mock_lnet_init(self, mocker: MockerFixture) -> MagicMock:
        """Mock lustre-ops LNet init."""
        return mocker.patch("charm.lnet.init", autospec=True)

    def test_success(
        self,
        ctx: testing.Context[charm.LustreCharm],
        mocker: MockerFixture,
        mock_repo_setup: MagicMock,
        mock_apt: MagicMock,
        mock_lnet_init: MagicMock,
    ) -> None:
        """Successful install."""
        out = ctx.run(ctx.on.install(), testing.State())
        assert out.unit_status == testing.MaintenanceStatus(charm._CharmStatus.PREPARING_SERVICES)

    def test_lnet_networks_config_forwarded(
        self,
        ctx: testing.Context[charm.LustreCharm],
        mock_repo_setup: MagicMock,
        mock_apt: MagicMock,
        mock_lnet_init: MagicMock,
    ) -> None:
        """The lnet-networks config is parsed and forwarded to lnet.init."""
        ctx.run(
            ctx.on.install(),
            testing.State(config={"lnet-networks": "o2ib0=ib0,ib1"}),
        )

        mock_lnet_init.assert_called_once()
        _, kwargs = mock_lnet_init.call_args
        networks = kwargs["networks"]
        assert networks == {"o2ib": ["ib0", "ib1"]}

    def test_empty_lnet_config_auto_detects(
        self,
        ctx: testing.Context[charm.LustreCharm],
        mock_repo_setup: MagicMock,
        mock_apt: MagicMock,
        mock_lnet_init: MagicMock,
    ) -> None:
        """An empty lnet-networks config triggers auto-detection (networks=None)."""
        ctx.run(ctx.on.install(), testing.State())

        mock_lnet_init.assert_called_once_with(networks=None)

    def test_repo_setup_fails(
        self,
        ctx: testing.Context[charm.LustreCharm],
        mock_repo_setup: MagicMock,
    ) -> None:
        """Repository setup failure blocks the unit."""
        mock_repo_setup.side_effect = RepositoryError("failed to set up PPA")

        out = ctx.run(ctx.on.install(), testing.State())
        assert out.unit_status == testing.BlockedStatus(charm._CharmStatus.FAILED_REPO_SETUP)

    def test_packages_error(
        self,
        ctx: testing.Context[charm.LustreCharm],
        mocker: MockerFixture,
        mock_repo_setup: MagicMock,
        mock_lnet_init: MagicMock,
    ) -> None:
        """Package installation fails."""
        mocker.patch("charm.apt.add_package", side_effect=PackageError("bad package"))

        out = ctx.run(ctx.on.install(), testing.State())
        expected_message = charm._CharmStatus.failed_install(LUSTRE_PACKAGES)
        assert out.unit_status == testing.BlockedStatus(expected_message)

    def test_lustre_init_error(
        self,
        ctx: testing.Context[charm.LustreCharm],
        mocker: MockerFixture,
        mock_repo_setup: MagicMock,
        mock_apt: MagicMock,
        mock_lnet_init: MagicMock,
    ) -> None:
        """Lustre init fails."""
        mock_lnet_init.side_effect = LNetError("")

        out = ctx.run(ctx.on.install(), testing.State())
        assert out.unit_status == testing.BlockedStatus(charm._CharmStatus.FAILED_LNET_INIT)


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
        nids = ["10.0.0.1@tcp"]
        mock_peer_observer.return_value.get_app_data.return_value = LustrePeerAppData()
        mock_peer_observer.return_value.mgs_nids_published.return_value = nids

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
        app_data = LustrePeerAppData(mgs_nids=["10.0.0.1@tcp"], mgs_unit_name=f"{APP_NAME}/0")
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
        nids = ["10.0.0.1@tcp"]
        app_data = LustrePeerAppData(mgs_nids=nids, mgs_unit_name=f"{APP_NAME}/1")
        mock_peer_observer.return_value.get_app_data.return_value = app_data

        ctx.run(ctx.on.start(), testing.State(leader=True))

        mock_oss_setup.assert_called_once_with(LUSTRE_FSNAME, f"{APP_NAME}/0", nids)

    def test_peer_app_data_error(
        self, ctx: testing.Context[charm.LustreCharm], mock_peer_observer: MagicMock
    ) -> None:
        """Fails to retrieve peer relation application data."""
        mock_peer_observer.return_value.get_app_data.side_effect = LustrePeerError("get failed")

        out = ctx.run(ctx.on.start(), testing.State(leader=True))

        assert out.unit_status == testing.BlockedStatus(charm._CharmStatus.FAILED_PEER_DATA)

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

        assert out.unit_status == testing.BlockedStatus(charm._CharmStatus.FAILED_MGS_MDS_SETUP)

    def test_leader_initial_deployment_mgs_nids_published_error(
        self,
        ctx: testing.Context[charm.LustreCharm],
        mocker: MockerFixture,
        mock_mgs_mds_setup: MagicMock,
        mock_peer_observer: MagicMock,
    ) -> None:
        """Leader initial deployment: mgs_nids_published fails."""
        mock_peer_observer.return_value.get_app_data.return_value = LustrePeerAppData()
        mock_peer_observer.return_value.mgs_nids_published.side_effect = LustrePeerError(
            "NID failed"
        )

        out = ctx.run(ctx.on.start(), testing.State(leader=True))

        assert out.unit_status == testing.BlockedStatus(charm._CharmStatus.FAILED_MGS_MDS_SETUP)

    def test_restart_mgs_unit_setup_error(
        self,
        ctx: testing.Context[charm.LustreCharm],
        mock_mgs_mds_setup: MagicMock,
        mock_peer_observer: MagicMock,
    ) -> None:
        """MGS unit restart: mgs_mds_setup fails."""
        app_data = LustrePeerAppData(mgs_nids=["10.0.0.1@tcp"], mgs_unit_name=f"{APP_NAME}/0")
        mock_peer_observer.return_value.get_app_data.return_value = app_data
        mock_mgs_mds_setup.side_effect = LustreFilesystemError("mount failed")

        out = ctx.run(ctx.on.start(), testing.State(leader=True))

        assert out.unit_status == testing.BlockedStatus(charm._CharmStatus.FAILED_SERVICE_SETUP)

    def test_restart_oss_unit_setup_error(
        self,
        ctx: testing.Context[charm.LustreCharm],
        mock_oss_setup: MagicMock,
        mock_peer_observer: MagicMock,
    ) -> None:
        """OSS unit restart: oss_setup fails."""
        nids = ["10.0.0.1@tcp"]
        app_data = LustrePeerAppData(mgs_nids=nids, mgs_unit_name=f"{APP_NAME}/1")
        mock_peer_observer.return_value.get_app_data.return_value = app_data
        mock_oss_setup.side_effect = LustreFilesystemError("zpool failed")

        out = ctx.run(ctx.on.start(), testing.State(leader=True))

        assert out.unit_status == testing.BlockedStatus(charm._CharmStatus.FAILED_SERVICE_SETUP)
