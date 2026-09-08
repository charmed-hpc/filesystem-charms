# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Lustre peer relation observer unit tests."""

from unittest.mock import MagicMock

import lustre_peer
import ops
import pytest
from constants import LUSTRE_FSNAME
from errors import LustreFilesystemError, LustrePeerDuplicateMgsError
from lustre_ops.errors import LNetError
from pytest_mock import MockerFixture

MGS_UNIT_NAME = "lustre/0"
MGS_NIDS = ["10.0.0.5@tcp", "10.0.0.6@o2ib0"]
OSS_UNIT_NAME = "lustre/1"
OST_DEVICES = ["/dev/sdb", "/dev/sdc", "/dev/sdd"]


@pytest.fixture(scope="function")
def mock_model(mocker: MockerFixture) -> MagicMock:
    """Mock LustrePeerObserver.model."""
    model = mocker.MagicMock()
    mocker.patch.object(
        lustre_peer.LustrePeerObserver,
        "model",
        new_callable=mocker.PropertyMock,
        return_value=model,
    )
    return model


@pytest.fixture(scope="function")
def mock_model_with_relation(
    mock_model: MagicMock, mocker: MockerFixture
) -> tuple[MagicMock, MagicMock]:
    """_model with a mocked get_relation."""
    rel = mock_model.get_relation.return_value = mocker.MagicMock()
    return mock_model, rel


class TestMgsNidsPublished:
    """mgs_nids_published() tests."""

    def test_leader_publishes_and_promotes(
        self, mocker: MockerFixture, mock_model: MagicMock
    ) -> None:
        """Leader MGS unit publishes NIDs to unit data and promotes itself to app data."""
        mock_model.unit.is_leader.return_value = True
        mock_model.unit.name = MGS_UNIT_NAME
        mocker.patch("lustre_peer.lnet.get_nids", return_value=MGS_NIDS)
        mocker.patch(
            "lustre_peer.LustrePeerObserver.get_unit_data",
            return_value=lustre_peer.LustrePeerUnitData(),
        )
        mocker.patch(
            "lustre_peer.LustrePeerObserver.get_app_data",
            return_value=lustre_peer.LustrePeerAppData(),
        )
        set_unit_data = mocker.patch("lustre_peer.LustrePeerObserver.set_unit_data")
        set_app_data = mocker.patch("lustre_peer.LustrePeerObserver.set_app_data")
        set_ready = mocker.patch("lustre_peer.LustrePeerObserver.set_unit_ready")

        observer = lustre_peer.LustrePeerObserver(mocker.MagicMock())
        result = observer.mgs_nids_published()

        assert result == MGS_NIDS

        unit_data = set_unit_data.call_args[0][0]
        assert unit_data.mgs_nids == MGS_NIDS

        app_data = set_app_data.call_args[0][0]
        assert app_data.mgs_nids == MGS_NIDS
        assert app_data.mgs_unit_name == MGS_UNIT_NAME

        # A relation-changed event is not triggered on the unit that writes to
        # its own unit data, so the leader must ready itself here. The publish
        # attempt is included in set_unit_ready.
        set_ready.assert_called_once_with(MGS_NIDS, LUSTRE_FSNAME)

    def test_non_leader_publishes_unit_data_only(
        self, mocker: MockerFixture, mock_model: MagicMock
    ) -> None:
        """Non-leader MGS unit publishes NIDs to unit data but not app data."""
        mock_model.unit.is_leader.return_value = False
        mock_model.unit.name = MGS_UNIT_NAME
        mocker.patch("lustre_peer.lnet.get_nids", return_value=MGS_NIDS)
        mocker.patch(
            "lustre_peer.LustrePeerObserver.get_unit_data",
            return_value=lustre_peer.LustrePeerUnitData(),
        )
        set_unit_data = mocker.patch("lustre_peer.LustrePeerObserver.set_unit_data")
        set_app_data = mocker.patch("lustre_peer.LustrePeerObserver.set_app_data")
        set_ready = mocker.patch("lustre_peer.LustrePeerObserver.set_unit_ready")
        publish_fs_info = mocker.patch(
            "lustre_peer.LustrePeerObserver._try_publish_filesystem_info"
        )

        observer = lustre_peer.LustrePeerObserver(mocker.MagicMock())
        result = observer.mgs_nids_published()

        assert result == MGS_NIDS

        unit_data = set_unit_data.call_args[0][0]
        assert unit_data.mgs_nids == MGS_NIDS

        set_app_data.assert_not_called()
        set_ready.assert_not_called()
        publish_fs_info.assert_not_called()

    def test_get_nid_fails(self, mocker: MockerFixture, mock_model: MagicMock) -> None:
        """Unit raises an error when get_nids() fails."""
        mocker.patch(
            "lustre_peer.lnet.get_nids",
            side_effect=LNetError("test get_nids failed"),
        )

        observer = lustre_peer.LustrePeerObserver(mocker.MagicMock())
        with pytest.raises(lustre_peer.LustrePeerError, match="Failed to determine MGS NID"):
            observer.mgs_nids_published()

    def test_empty_nids(self, mocker: MockerFixture, mock_model: MagicMock) -> None:
        """Unit raises an error when no NIDs are configured."""
        mocker.patch("lustre_peer.lnet.get_nids", return_value=[])

        observer = lustre_peer.LustrePeerObserver(mocker.MagicMock())
        with pytest.raises(
            lustre_peer.LustrePeerError, match="No LNet NIDs configured on this unit"
        ):
            observer.mgs_nids_published()

    def test_leader_republish_idempotent(
        self, mocker: MockerFixture, mock_model: MagicMock
    ) -> None:
        """Leader re-publishing matching NIDs does not rewrite app data."""
        mock_model.unit.is_leader.return_value = True
        mock_model.unit.name = MGS_UNIT_NAME
        existing = lustre_peer.LustrePeerAppData(mgs_nids=MGS_NIDS, mgs_unit_name=MGS_UNIT_NAME)
        mocker.patch("lustre_peer.lnet.get_nids", return_value=MGS_NIDS)
        mocker.patch(
            "lustre_peer.LustrePeerObserver.get_unit_data",
            return_value=lustre_peer.LustrePeerUnitData(),
        )
        mocker.patch("lustre_peer.LustrePeerObserver.get_app_data", return_value=existing)
        mocker.patch("lustre_peer.LustrePeerObserver.set_unit_data")
        set_app_data = mocker.patch("lustre_peer.LustrePeerObserver.set_app_data")
        mocker.patch("lustre_peer.LustrePeerObserver.set_unit_ready")
        mocker.patch("lustre_peer.LustrePeerObserver._try_publish_filesystem_info")

        observer = lustre_peer.LustrePeerObserver(mocker.MagicMock())
        result = observer.mgs_nids_published()

        assert result == MGS_NIDS
        set_app_data.assert_not_called()

    def test_leader_nids_changed_error(self, mocker: MockerFixture, mock_model: MagicMock) -> None:
        """Leader raises an error when its NIDs change after promotion."""
        mock_model.unit.is_leader.return_value = True
        mock_model.unit.name = MGS_UNIT_NAME
        existing = lustre_peer.LustrePeerAppData(
            mgs_nids=["10.0.0.99@tcp"], mgs_unit_name=MGS_UNIT_NAME
        )
        mocker.patch("lustre_peer.lnet.get_nids", return_value=MGS_NIDS)
        mocker.patch(
            "lustre_peer.LustrePeerObserver.get_unit_data",
            return_value=lustre_peer.LustrePeerUnitData(),
        )
        mocker.patch("lustre_peer.LustrePeerObserver.get_app_data", return_value=existing)
        mocker.patch("lustre_peer.LustrePeerObserver.set_unit_data")

        observer = lustre_peer.LustrePeerObserver(mocker.MagicMock())
        with pytest.raises(lustre_peer.LustrePeerError, match="MGS NIDs changed"):
            observer.mgs_nids_published()

    def test_duplicate_mgs_error(self, mocker: MockerFixture, mock_model: MagicMock) -> None:
        """Leader raises an error when another unit is already the assigned MGS."""
        mock_model.unit.is_leader.return_value = True
        mock_model.unit.name = MGS_UNIT_NAME
        existing = lustre_peer.LustrePeerAppData(mgs_nids=MGS_NIDS, mgs_unit_name=OSS_UNIT_NAME)
        mocker.patch("lustre_peer.lnet.get_nids", return_value=MGS_NIDS)
        mocker.patch(
            "lustre_peer.LustrePeerObserver.get_unit_data",
            return_value=lustre_peer.LustrePeerUnitData(),
        )
        mocker.patch("lustre_peer.LustrePeerObserver.get_app_data", return_value=existing)
        mocker.patch("lustre_peer.LustrePeerObserver.set_unit_data")

        observer = lustre_peer.LustrePeerObserver(mocker.MagicMock())
        with pytest.raises(LustrePeerDuplicateMgsError, match="already the MGS"):
            observer.mgs_nids_published()


class TestOnRelationChanged:
    """_on_relation_changed() tests."""

    @pytest.fixture(scope="function")
    def app_data_event(self, mocker: MockerFixture) -> MagicMock:
        """Relation-changed event for an application databag change (unit is None)."""
        event = mocker.MagicMock()
        event.unit = None
        return event

    @pytest.fixture(scope="function")
    def oss_unit(
        self, mocker: MockerFixture, mock_model: MagicMock
    ) -> tuple[MagicMock, MagicMock]:
        """Model of an OSS unit with MGS data published and oss_setup mocked."""
        mock_model.app.planned_units.return_value = 1
        mock_model.unit.name = OSS_UNIT_NAME

        app_data = lustre_peer.LustrePeerAppData(mgs_nids=MGS_NIDS, mgs_unit_name=MGS_UNIT_NAME)
        unit_data = lustre_peer.LustrePeerUnitData()
        mocker.patch("lustre_peer.LustrePeerObserver.get_app_data", return_value=app_data)
        mocker.patch("lustre_peer.LustrePeerObserver.get_unit_data", return_value=unit_data)
        mock_model.storages = {"ost": [MagicMock(location=d) for d in OST_DEVICES]}

        mock_oss = mocker.patch("lustre_peer.lustre_fs.oss_setup")
        return mock_model, mock_oss

    @pytest.mark.parametrize("is_leader", [True, False], ids=["leader", "non-leader"])
    def test_oss_unit_setup(
        self,
        mocker: MockerFixture,
        oss_unit: tuple[MagicMock, MagicMock],
        app_data_event: MagicMock,
        is_leader: bool,
    ) -> None:
        """OSS unit sets up correctly when relation data is available."""
        mock_model, mock_oss = oss_unit
        mock_model.unit.is_leader.return_value = is_leader

        expected_status = ops.ActiveStatus()
        mocker.patch("lustre_peer.check_lustre", return_value=expected_status)

        observer = lustre_peer.LustrePeerObserver(mocker.MagicMock())
        observer._on_relation_changed(app_data_event)

        mock_oss.assert_called_once_with(LUSTRE_FSNAME, OSS_UNIT_NAME, MGS_NIDS, mocker.ANY)
        assert mock_model.unit.status == expected_status

    def test_app_data_error(
        self,
        mocker: MockerFixture,
        mock_model: MagicMock,
        app_data_event: MagicMock,
    ) -> None:
        """OSS unit does not set up when relation data is unavailable."""
        mock_model.get_relation.return_value = None
        mock_oss = mocker.patch("lustre_peer.lustre_fs.oss_setup", autospec=True)

        observer = lustre_peer.LustrePeerObserver(mocker.MagicMock())
        observer._on_relation_changed(app_data_event)

        mock_oss.assert_not_called()

    def test_mgs_data_not_published(
        self,
        mocker: MockerFixture,
        mock_model_with_relation: tuple[MagicMock, MagicMock],
        app_data_event: MagicMock,
    ) -> None:
        """OSS unit does not set up when MGS NID is not published."""
        _, rel = mock_model_with_relation
        rel.load.return_value = None
        mock_oss = mocker.patch("lustre_peer.lustre_fs.oss_setup", autospec=True)

        observer = lustre_peer.LustrePeerObserver(mocker.MagicMock())
        observer._on_relation_changed(app_data_event)

        mock_oss.assert_not_called()

    def test_mgs_unit_skips_oss(
        self,
        mocker: MockerFixture,
        mock_model: MagicMock,
        app_data_event: MagicMock,
    ) -> None:
        """MGS unit does not attempt to set up OSS."""
        mock_model.app.planned_units.return_value = 1
        mock_model.unit.name = MGS_UNIT_NAME
        app_data = lustre_peer.LustrePeerAppData(mgs_nids=MGS_NIDS, mgs_unit_name=MGS_UNIT_NAME)
        mocker.patch("lustre_peer.LustrePeerObserver.get_app_data", return_value=app_data)
        mock_oss = mocker.patch("lustre_peer.lustre_fs.oss_setup", autospec=True)

        observer = lustre_peer.LustrePeerObserver(mocker.MagicMock())
        observer._on_relation_changed(app_data_event)

        mock_oss.assert_not_called()

    def test_oss_storage_not_provisioned(
        self,
        mocker: MockerFixture,
        oss_unit: tuple[MagicMock, MagicMock],
        app_data_event: MagicMock,
    ) -> None:
        """OSS setup is skipped when OST storage is not yet provisioned, e.g. after a reboot."""
        model, mock_oss = oss_unit

        class _UnprovisionedStorage:
            @property
            def location(self):
                raise ops.model.ModelError("storage not provisioned")

        model.storages = {"ost": [_UnprovisionedStorage()]}

        observer = lustre_peer.LustrePeerObserver(mocker.MagicMock())
        observer._on_relation_changed(app_data_event)

        mock_oss.assert_not_called()

    def test_oss_setup_failure(
        self,
        mocker: MockerFixture,
        oss_unit: tuple[MagicMock, MagicMock],
        app_data_event: MagicMock,
    ) -> None:
        """OSS service setup fails."""
        model, mock_oss = oss_unit
        mock_oss.side_effect = LustreFilesystemError("setup failed")

        observer = lustre_peer.LustrePeerObserver(mocker.MagicMock())
        observer._on_relation_changed(app_data_event)

        assert model.unit.status == ops.BlockedStatus(
            lustre_peer._LustrePeerStatus.FAILED_OSS_SETUP
        )

    def test_set_unit_ready_failure(
        self,
        mocker: MockerFixture,
        oss_unit: tuple[MagicMock, MagicMock],
        app_data_event: MagicMock,
    ) -> None:
        """OSS unit fails to set itself ready."""
        model, _ = oss_unit
        mocker.patch(
            "lustre_peer.LustrePeerObserver.set_unit_ready",
            side_effect=lustre_peer.LustrePeerError("set ready failed"),
        )

        observer = lustre_peer.LustrePeerObserver(mocker.MagicMock())
        observer._on_relation_changed(app_data_event)

        assert model.unit.status == ops.BlockedStatus(
            lustre_peer._LustrePeerStatus.FAILED_SET_UNIT_READY
        )

    def test_publish_filesystem_info_failure(
        self,
        mocker: MockerFixture,
        oss_unit: tuple[MagicMock, MagicMock],
        app_data_event: MagicMock,
    ) -> None:
        """Leader OSS unit filesystem info publishing attempt fails."""
        model, _ = oss_unit
        model.unit.is_leader.return_value = True
        mocker.patch("lustre_peer.LustrePeerObserver.set_unit_data")
        mocker.patch(
            "lustre_peer.LustrePeerObserver._try_publish_filesystem_info",
            side_effect=lustre_peer.LustrePeerError("publish failed"),
        )

        observer = lustre_peer.LustrePeerObserver(mocker.MagicMock())
        observer._on_relation_changed(app_data_event)

        # The publish failure propagates from set_unit_ready and is reported
        # as a failure to set the unit ready.
        assert model.unit.status == ops.BlockedStatus(
            lustre_peer._LustrePeerStatus.FAILED_SET_UNIT_READY
        )

    @pytest.fixture(scope="function")
    def leader_model(self, mock_model: MagicMock) -> MagicMock:
        """Model of the leader unit."""
        mock_model.unit.is_leader.return_value = True
        return mock_model

    def test_leader_promotes_mgs_nids(
        self, mocker: MockerFixture, leader_model: MagicMock
    ) -> None:
        """Leader promotes MGS NIDs from a peer unit's databag to app data."""
        event = mocker.MagicMock()
        event.unit.name = MGS_UNIT_NAME
        mgs_unit_data = lustre_peer.LustrePeerUnitData(mgs_nids=MGS_NIDS)
        mocker.patch("lustre_peer.LustrePeerObserver.get_unit_data", return_value=mgs_unit_data)
        mocker.patch(
            "lustre_peer.LustrePeerObserver.get_app_data",
            return_value=lustre_peer.LustrePeerAppData(),
        )
        set_app_data = mocker.patch("lustre_peer.LustrePeerObserver.set_app_data")

        observer = lustre_peer.LustrePeerObserver(mocker.MagicMock())
        observer._on_relation_changed(event)

        app_data = set_app_data.call_args[0][0]
        assert app_data.mgs_nids == MGS_NIDS
        assert app_data.mgs_unit_name == MGS_UNIT_NAME

    def test_leader_skips_promotion_without_nids(
        self, mocker: MockerFixture, leader_model: MagicMock
    ) -> None:
        """Leader does not promote when the changed unit published no MGS NIDs."""
        event = mocker.MagicMock()
        event.unit.name = OSS_UNIT_NAME
        mocker.patch(
            "lustre_peer.LustrePeerObserver.get_unit_data",
            return_value=lustre_peer.LustrePeerUnitData(),
        )
        mocker.patch(
            "lustre_peer.LustrePeerObserver.get_app_data",
            return_value=lustre_peer.LustrePeerAppData(),
        )
        set_app_data = mocker.patch("lustre_peer.LustrePeerObserver.set_app_data")

        observer = lustre_peer.LustrePeerObserver(mocker.MagicMock())
        observer._on_relation_changed(event)

        set_app_data.assert_not_called()

    def test_leader_duplicate_mgs_blocked(
        self, mocker: MockerFixture, leader_model: MagicMock
    ) -> None:
        """Leader blocks when a second unit publishes MGS NIDs."""
        event = mocker.MagicMock()
        event.unit.name = OSS_UNIT_NAME
        mgs_unit_data = lustre_peer.LustrePeerUnitData(mgs_nids=["10.0.0.9@tcp"])
        existing = lustre_peer.LustrePeerAppData(mgs_nids=MGS_NIDS, mgs_unit_name=MGS_UNIT_NAME)
        mocker.patch("lustre_peer.LustrePeerObserver.get_unit_data", return_value=mgs_unit_data)
        mocker.patch("lustre_peer.LustrePeerObserver.get_app_data", return_value=existing)
        mock_oss = mocker.patch("lustre_peer.lustre_fs.oss_setup", autospec=True)

        observer = lustre_peer.LustrePeerObserver(mocker.MagicMock())
        observer._on_relation_changed(event)

        assert leader_model.unit.status == ops.BlockedStatus(
            lustre_peer._LustrePeerStatus.MULTIPLE_MGS_UNITS
        )
        mock_oss.assert_not_called()

    def test_leader_nids_changed_not_duplicate(
        self, mocker: MockerFixture, leader_model: MagicMock
    ) -> None:
        """A changed-NIDs promotion error is not surfaced as a duplicate MGS error."""
        event = mocker.MagicMock()
        event.unit.name = MGS_UNIT_NAME
        mgs_unit_data = lustre_peer.LustrePeerUnitData(mgs_nids=["10.0.0.9@tcp"])
        existing = lustre_peer.LustrePeerAppData(mgs_nids=MGS_NIDS, mgs_unit_name=MGS_UNIT_NAME)
        mocker.patch("lustre_peer.LustrePeerObserver.get_unit_data", return_value=mgs_unit_data)
        mocker.patch("lustre_peer.LustrePeerObserver.get_app_data", return_value=existing)
        mock_oss = mocker.patch("lustre_peer.lustre_fs.oss_setup", autospec=True)

        observer = lustre_peer.LustrePeerObserver(mocker.MagicMock())
        observer._on_relation_changed(event)

        # The generic error is logged and skipped, but does not block the unit.
        assert leader_model.unit.status != ops.BlockedStatus(
            lustre_peer._LustrePeerStatus.MULTIPLE_MGS_UNITS
        )
        mock_oss.assert_not_called()

    def test_non_leader_does_not_promote(
        self, mocker: MockerFixture, mock_model: MagicMock
    ) -> None:
        """Non-leader units do not promote MGS NIDs."""
        mock_model.unit.is_leader.return_value = False
        event = mocker.MagicMock()
        event.unit.name = MGS_UNIT_NAME
        mgs_unit_data = lustre_peer.LustrePeerUnitData(mgs_nids=MGS_NIDS)
        mocker.patch("lustre_peer.LustrePeerObserver.get_unit_data", return_value=mgs_unit_data)
        mocker.patch(
            "lustre_peer.LustrePeerObserver.get_app_data",
            return_value=lustre_peer.LustrePeerAppData(),
        )
        set_app_data = mocker.patch("lustre_peer.LustrePeerObserver.set_app_data")

        observer = lustre_peer.LustrePeerObserver(mocker.MagicMock())
        observer._on_relation_changed(event)

        set_app_data.assert_not_called()


class TestSetAppData:
    """set_app_data() tests."""

    def test_saves_to_app_databag(
        self, mocker: MockerFixture, mock_model_with_relation: tuple[MagicMock, MagicMock]
    ) -> None:
        """Application data is saved to the peer relation application databag."""
        _, rel = mock_model_with_relation
        data = lustre_peer.LustrePeerAppData(mgs_nids=MGS_NIDS, mgs_unit_name=MGS_UNIT_NAME)

        observer = lustre_peer.LustrePeerObserver(mocker.MagicMock())
        observer.set_app_data(data)

        rel.save.assert_called_once_with(data, rel.app)


class TestAllUnitsReady:
    """_all_units_ready() tests."""

    def test_not_all_units_ready(
        self, mocker: MockerFixture, mock_model_with_relation: tuple[MagicMock, MagicMock]
    ) -> None:
        """Returns False when a peer unit has not reported ready."""
        model, rel = mock_model_with_relation
        model.app.planned_units.return_value = 2
        model.unit.name = MGS_UNIT_NAME
        rel.units = [mocker.MagicMock()]

        def fake_get_unit_data(unit=None):
            return lustre_peer.LustrePeerUnitData(ready=unit is model.unit)

        mocker.patch(
            "lustre_peer.LustrePeerObserver.get_unit_data", side_effect=fake_get_unit_data
        )

        observer = lustre_peer.LustrePeerObserver(mocker.MagicMock())
        assert observer._all_units_ready() is False


class TestTryPublishFilesystemInfo:
    """_try_publish_filesystem_info() tests."""

    def test_publishes_when_all_units_ready(
        self, mocker: MockerFixture, mock_model: MagicMock
    ) -> None:
        """Leader publishes filesystem info once all planned units report ready."""
        mock_model.app.planned_units.return_value = 1
        mock_model.unit.name = MGS_UNIT_NAME
        mocker.patch("lustre_peer.LustrePeerObserver._all_units_ready", return_value=True)

        charm = mocker.MagicMock()
        observer = lustre_peer.LustrePeerObserver(charm)
        observer._try_publish_filesystem_info(MGS_NIDS, LUSTRE_FSNAME)

        charm.filesystem.set_info.assert_called_once()
        args, _ = charm.filesystem.set_info.call_args
        assert args[0].mgs_ids == MGS_NIDS
        assert args[0].fs_name == LUSTRE_FSNAME

    def test_waits_when_units_not_ready(
        self, mocker: MockerFixture, mock_model: MagicMock
    ) -> None:
        """Filesystem info is not published while units are still reporting ready."""
        mock_model.app.planned_units.return_value = 1
        mock_model.unit.name = MGS_UNIT_NAME
        mocker.patch("lustre_peer.LustrePeerObserver._all_units_ready", return_value=False)

        charm = mocker.MagicMock()
        observer = lustre_peer.LustrePeerObserver(charm)
        observer._try_publish_filesystem_info(MGS_NIDS, LUSTRE_FSNAME)

        charm.filesystem.set_info.assert_not_called()
