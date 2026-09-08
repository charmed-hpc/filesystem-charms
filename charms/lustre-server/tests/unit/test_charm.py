# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Lustre charm unit tests."""

import importlib
from unittest.mock import MagicMock

import charm
import ops
import pytest
from charmlibs.apt import PackageError
from constants import LUSTRE_FSNAME, LUSTRE_PACKAGES
from errors import LustreFilesystemError, LustrePeerDuplicateMgsError, LustrePeerError
from lustre_ops.errors import LNetError, RepositoryError
from lustre_peer import LustrePeerAppData
from ops import testing
from pytest_mock import MockerFixture

APP_NAME = "lustre-test"
MGT_MDT_DEVICES = ["/dev/sda", "/dev/sdb"]
OST_DEVICES = ["/dev/sda", "/dev/sdb", "/dev/sdc"]
EXPECTED_FORWARDED_DEVICES = ["/dev/sdb", "/dev/sdc", "/dev/sdd"]


class _UnprovisionedStorage:
    """Mock storage object whose .location raises, simulating unprovisioned storage."""

    @property
    def location(self):
        raise ops.model.ModelError("storage not provisioned")


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
        """An empty lnet-networks config triggers auto-detection."""
        ctx.run(ctx.on.install(), testing.State())

        mock_lnet_init.assert_called_once_with(networks={})

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

    @pytest.fixture(scope="function", autouse=True)
    def mock_is_lustre_installed(self, mocker: MockerFixture) -> MagicMock:
        """Mock lustre_fs.is_lustre_installed to return True."""
        return mocker.patch("charm.lustre_fs.is_lustre_installed", return_value=True)

    @pytest.fixture(scope="function")
    def mock_storage_devices(self, mocker: MockerFixture) -> dict[str, list[MagicMock]]:
        """Mock ops.model.Model.storages; per-test storage can be attached by updating the dict."""
        storages: dict[str, list[MagicMock]] = {"mgt-mdt": [], "ost": []}
        mocker.patch.object(ops.model.Model, "storages", property(lambda self: storages))
        return storages

    @staticmethod
    def _attach(storages: dict[str, list[MagicMock]], name: str, devices: list[str]) -> None:
        """Attach block devices to a mocked storage pool."""
        storages[name] = [MagicMock(location=d) for d in devices]

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
        mock_mgs_mds_setup: MagicMock,
        mock_peer_observer: MagicMock,
        mock_storage_devices: dict[str, list[MagicMock]],
    ) -> None:
        """Leader with no MGS published: MGS+MDS successful start."""
        self._attach(mock_storage_devices, "mgt-mdt", MGT_MDT_DEVICES)
        nids = ["10.0.0.1@tcp"]
        mock_peer_observer.return_value.get_app_data.return_value = LustrePeerAppData()
        mock_peer_observer.return_value.mgs_nids_published.return_value = nids

        ctx.run(ctx.on.start(), testing.State(leader=True))

        mock_mgs_mds_setup.assert_called_once_with(LUSTRE_FSNAME, MGT_MDT_DEVICES)

    def test_start_before_install(
        self,
        ctx: testing.Context[charm.LustreCharm],
        mocker: MockerFixture,
        mock_is_lustre_installed: MagicMock,
        mock_mgs_mds_setup: MagicMock,
        mock_oss_setup: MagicMock,
        mock_storage_devices: dict[str, list[MagicMock]],
    ) -> None:
        """Start before Lustre packages are installed is a no-op."""
        mock_is_lustre_installed.return_value = False
        self._attach(mock_storage_devices, "mgt-mdt", MGT_MDT_DEVICES)

        ctx.run(ctx.on.start(), testing.State(leader=True))

        mock_mgs_mds_setup.assert_not_called()
        mock_oss_setup.assert_not_called()

    def test_storage_not_provisioned(
        self,
        ctx: testing.Context[charm.LustreCharm],
        mock_mgs_mds_setup: MagicMock,
        mock_oss_setup: MagicMock,
        mock_peer_observer: MagicMock,
        mock_storage_devices: dict[str, list[MagicMock]],
    ) -> None:
        """Start is deferred when a storage device is not yet provisioned, e.g. after a reboot."""
        mock_storage_devices["mgt-mdt"].append(_UnprovisionedStorage())

        ctx.run(ctx.on.start(), testing.State(leader=True))

        # NOTE: the @refresh_check_lustre decorator overwrites the maintenance status
        # set by the handler, so only assert that no setup path was entered.
        mock_mgs_mds_setup.assert_not_called()
        mock_oss_setup.assert_not_called()

    def test_duplicate_storage_error(
        self,
        ctx: testing.Context[charm.LustreCharm],
        mock_mgs_mds_setup: MagicMock,
        mock_oss_setup: MagicMock,
        mock_peer_observer: MagicMock,
        mock_storage_devices: dict[str, list[MagicMock]],
    ) -> None:
        """Unit with both MGT+MDT and OST storage attached is blocked."""
        self._attach(mock_storage_devices, "mgt-mdt", MGT_MDT_DEVICES)
        self._attach(mock_storage_devices, "ost", OST_DEVICES)

        out = ctx.run(ctx.on.start(), testing.State(leader=True))

        assert out.unit_status == testing.BlockedStatus(charm._CharmStatus.DUPLICATE_STORAGE_ERROR)
        mock_mgs_mds_setup.assert_not_called()
        mock_oss_setup.assert_not_called()

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
        mock_mgs_mds_setup: MagicMock,
        mock_peer_observer: MagicMock,
        mock_storage_devices: MagicMock,
    ) -> None:
        """MGS already published. This unit is the MGS."""
        self._attach(mock_storage_devices, "mgt-mdt", MGT_MDT_DEVICES)
        app_data = LustrePeerAppData(mgs_nids=["10.0.0.1@tcp"], mgs_unit_name=f"{APP_NAME}/0")
        mock_peer_observer.return_value.get_app_data.return_value = app_data

        ctx.run(ctx.on.start(), testing.State(leader=True))

        mock_mgs_mds_setup.assert_called_once_with(LUSTRE_FSNAME, MGT_MDT_DEVICES)

    def test_restart_oss_unit(
        self,
        ctx: testing.Context[charm.LustreCharm],
        mock_oss_setup: MagicMock,
        mock_peer_observer: MagicMock,
        mock_storage_devices: MagicMock,
    ) -> None:
        """MGS already published. This unit is an OSS."""
        self._attach(mock_storage_devices, "ost", OST_DEVICES)
        nids = ["10.0.0.1@tcp"]
        app_data = LustrePeerAppData(mgs_nids=nids, mgs_unit_name=f"{APP_NAME}/1")
        mock_peer_observer.return_value.get_app_data.return_value = app_data

        ctx.run(ctx.on.start(), testing.State(leader=True))

        mock_oss_setup.assert_called_once_with(LUSTRE_FSNAME, f"{APP_NAME}/0", nids, OST_DEVICES)

    def test_storage_devices_forwarded(
        self,
        ctx: testing.Context[charm.LustreCharm],
        mock_oss_setup: MagicMock,
        mock_peer_observer: MagicMock,
        mock_storage_devices: dict[str, list[MagicMock]],
    ) -> None:
        """Device paths are read from Juju storage and forwarded to lustre_fs."""
        # Attach reversed to verify devices are sorted before being forwarded.
        self._attach(mock_storage_devices, "ost", list(reversed(EXPECTED_FORWARDED_DEVICES)))
        nids = ["10.0.0.1@tcp"]
        app_data = LustrePeerAppData(mgs_nids=nids, mgs_unit_name=f"{APP_NAME}/1")
        mock_peer_observer.return_value.get_app_data.return_value = app_data

        ctx.run(ctx.on.start(), testing.State(leader=True))

        mock_oss_setup.assert_called_once_with(
            LUSTRE_FSNAME, f"{APP_NAME}/0", nids, EXPECTED_FORWARDED_DEVICES
        )

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
        mock_storage_devices: dict[str, list[MagicMock]],
    ) -> None:
        """Leader initial deployment: mgs_mds_setup fails."""
        self._attach(mock_storage_devices, "mgt-mdt", MGT_MDT_DEVICES)
        mock_peer_observer.return_value.get_app_data.return_value = LustrePeerAppData()
        mock_mgs_mds_setup.side_effect = LustreFilesystemError("zpool failed")

        out = ctx.run(ctx.on.start(), testing.State(leader=True))

        assert out.unit_status == testing.BlockedStatus(charm._CharmStatus.FAILED_MGS_MDS_SETUP)

    def test_leader_initial_deployment_duplicate_mgs_error(
        self,
        ctx: testing.Context[charm.LustreCharm],
        mocker: MockerFixture,
        mock_mgs_mds_setup: MagicMock,
        mock_peer_observer: MagicMock,
        mock_storage_devices: dict[str, list[MagicMock]],
    ) -> None:
        """Leader initial deployment: another unit is already the MGS."""
        self._attach(mock_storage_devices, "mgt-mdt", MGT_MDT_DEVICES)
        mock_peer_observer.return_value.get_app_data.return_value = LustrePeerAppData()
        mock_peer_observer.return_value.mgs_nids_published.side_effect = (
            LustrePeerDuplicateMgsError("duplicate MGS")
        )

        out = ctx.run(ctx.on.start(), testing.State(leader=True))

        assert out.unit_status == testing.BlockedStatus(charm._CharmStatus.MULTIPLE_MGS_UNITS)

    def test_leader_initial_deployment_mgs_nids_published_error(
        self,
        ctx: testing.Context[charm.LustreCharm],
        mocker: MockerFixture,
        mock_mgs_mds_setup: MagicMock,
        mock_peer_observer: MagicMock,
        mock_storage_devices: dict[str, list[MagicMock]],
    ) -> None:
        """Leader initial deployment: mgs_nids_published fails."""
        self._attach(mock_storage_devices, "mgt-mdt", MGT_MDT_DEVICES)
        mock_peer_observer.return_value.get_app_data.return_value = LustrePeerAppData()
        mock_peer_observer.return_value.mgs_nids_published.side_effect = LustrePeerError(
            "NID failed"
        )

        out = ctx.run(ctx.on.start(), testing.State(leader=True))

        assert out.unit_status == testing.BlockedStatus(charm._CharmStatus.FAILED_MGS_MDS_SETUP)

    def test_oss_waits_for_mgs(
        self,
        ctx: testing.Context[charm.LustreCharm],
        mock_oss_setup: MagicMock,
        mock_peer_observer: MagicMock,
        mock_storage_devices: dict[str, list[MagicMock]],
    ) -> None:
        """OSS unit with OST storage waits when no MGS has been published yet."""
        self._attach(mock_storage_devices, "ost", OST_DEVICES)
        mock_peer_observer.return_value.get_app_data.return_value = LustrePeerAppData()

        ctx.run(ctx.on.start(), testing.State(leader=True))

        mock_oss_setup.assert_not_called()
        mock_peer_observer.return_value.set_unit_ready.assert_not_called()

    def test_restart_mgs_unit_setup_error(
        self,
        ctx: testing.Context[charm.LustreCharm],
        mock_mgs_mds_setup: MagicMock,
        mock_peer_observer: MagicMock,
        mock_storage_devices: dict[str, list[MagicMock]],
    ) -> None:
        """MGS unit restart: mgs_mds_setup fails."""
        self._attach(mock_storage_devices, "mgt-mdt", MGT_MDT_DEVICES)
        app_data = LustrePeerAppData(mgs_nids=["10.0.0.1@tcp"], mgs_unit_name=f"{APP_NAME}/0")
        mock_peer_observer.return_value.get_app_data.return_value = app_data
        mock_mgs_mds_setup.side_effect = LustreFilesystemError("mount failed")

        out = ctx.run(ctx.on.start(), testing.State(leader=True))

        assert out.unit_status == testing.BlockedStatus(charm._CharmStatus.FAILED_MGS_MDS_SETUP)

    def test_restart_oss_unit_setup_error(
        self,
        ctx: testing.Context[charm.LustreCharm],
        mock_oss_setup: MagicMock,
        mock_peer_observer: MagicMock,
        mock_storage_devices: dict[str, list[MagicMock]],
    ) -> None:
        """OSS unit restart: oss_setup fails."""
        self._attach(mock_storage_devices, "ost", OST_DEVICES)
        nids = ["10.0.0.1@tcp"]
        app_data = LustrePeerAppData(mgs_nids=nids, mgs_unit_name=f"{APP_NAME}/1")
        mock_peer_observer.return_value.get_app_data.return_value = app_data
        mock_oss_setup.side_effect = LustreFilesystemError("zpool failed")

        out = ctx.run(ctx.on.start(), testing.State(leader=True))

        assert out.unit_status == testing.BlockedStatus(charm._CharmStatus.FAILED_OSS_SETUP)
