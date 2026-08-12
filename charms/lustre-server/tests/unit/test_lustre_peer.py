# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Lustre peer relation observer unit tests."""

from unittest.mock import MagicMock

import lustre_peer
import ops
import pytest
from constants import LUSTRE_FSNAME
from errors import LustreFilesystemError
from lustre_ops.errors import LNetError
from pytest_mock import MockerFixture

MGS_UNIT_NAME = "lustre/0"
MGS_NIDS = ["10.0.0.5@tcp", "10.0.0.6@o2ib0"]
OSS_UNIT_NAME = "lustre/1"


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

    def test_leader_publishes_nid(
        self, mocker: MockerFixture, mock_model_with_relation: tuple[MagicMock, MagicMock]
    ) -> None:
        """Leader unit publishes MGS NIDs to relation data."""
        model, rel = mock_model_with_relation
        model.app.planned_units.return_value = 1
        model.unit.is_leader.return_value = True
        model.unit.name = MGS_UNIT_NAME
        rel.load.return_value = None
        mocker.patch("lustre_peer.lustre_fs.oss_setup")
        mocker.patch("lustre_peer.lnet.get_nids", return_value=MGS_NIDS)

        observer = lustre_peer.LustrePeerObserver(mocker.MagicMock())
        result = observer.mgs_nids_published()

        assert result == MGS_NIDS
        assert rel.save.call_count == 2

        unit_data = rel.save.call_args_list[0][0][0]
        app_data = rel.save.call_args_list[1][0][0]

        assert unit_data.ready is True
        assert app_data.mgs_nids == MGS_NIDS
        assert app_data.mgs_unit_name == MGS_UNIT_NAME

    def test_non_leader_raises(self, mocker: MockerFixture, mock_model: MagicMock) -> None:
        """Non-leader unit raises an error when publishing MGS NID."""
        mock_model.unit.is_leader.return_value = False

        observer = lustre_peer.LustrePeerObserver(mocker.MagicMock())
        with pytest.raises(lustre_peer.LustrePeerError, match="Non-leader"):
            observer.mgs_nids_published()

    def test_get_nid_fails(
        self, mocker: MockerFixture, mock_model_with_relation: tuple[MagicMock, MagicMock]
    ) -> None:
        """Leader unit raises an error when get_nids() fails."""
        model, rel = mock_model_with_relation
        model.unit.is_leader.return_value = True
        model.unit.name = MGS_UNIT_NAME
        rel.load.return_value = None
        mocker.patch("lustre_peer.lustre_fs.oss_setup")
        mocker.patch(
            "lustre_peer.lnet.get_nids",
            side_effect=LNetError("test get_nids failed"),
        )

        observer = lustre_peer.LustrePeerObserver(mocker.MagicMock())
        with pytest.raises(lustre_peer.LustrePeerError, match="Failed to determine MGS NID"):
            observer.mgs_nids_published()

    def test_empty_nids(
        self, mocker: MockerFixture, mock_model_with_relation: tuple[MagicMock, MagicMock]
    ) -> None:
        """Leader unit raises an error when no NIDs are configured."""
        model, rel = mock_model_with_relation
        model.unit.is_leader.return_value = True
        model.unit.name = MGS_UNIT_NAME
        rel.load.return_value = None
        mocker.patch("lustre_peer.lustre_fs.oss_setup")
        mocker.patch("lustre_peer.lnet.get_nids", return_value=[])

        observer = lustre_peer.LustrePeerObserver(mocker.MagicMock())
        with pytest.raises(
            lustre_peer.LustrePeerError, match="No LNet NIDs configured on this unit"
        ):
            observer.mgs_nids_published()

    def test_nid_already_published(
        self, mocker: MockerFixture, mock_model_with_relation: tuple[MagicMock, MagicMock]
    ) -> None:
        """Leader unit does not overwrite existing MGS NIDs in relation data."""
        model, rel = mock_model_with_relation
        model.unit.is_leader.return_value = True
        existing = lustre_peer.LustrePeerAppData(mgs_nids=MGS_NIDS, mgs_unit_name=MGS_UNIT_NAME)
        rel.load.return_value = existing

        observer = lustre_peer.LustrePeerObserver(mocker.MagicMock())
        result = observer.mgs_nids_published()

        assert result == MGS_NIDS
        rel.save.assert_not_called()


class TestOnRelationChanged:
    """_on_relation_changed() tests."""

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

        mock_oss = mocker.patch("lustre_peer.lustre_fs.oss_setup")
        return mock_model, mock_oss

    @pytest.mark.parametrize("is_leader", [True, False], ids=["leader", "non-leader"])
    def test_oss_unit_setup(
        self, mocker: MockerFixture, oss_unit: tuple[MagicMock, MagicMock], is_leader: bool
    ) -> None:
        """OSS unit sets up correctly when relation data is available."""
        mock_model, mock_oss = oss_unit
        mock_model.unit.is_leader.return_value = is_leader

        expected_status = ops.ActiveStatus()
        mocker.patch("lustre_peer.check_lustre", return_value=expected_status)

        observer = lustre_peer.LustrePeerObserver(mocker.MagicMock())
        observer._on_relation_changed(mocker.MagicMock())

        mock_oss.assert_called_once_with(LUSTRE_FSNAME, OSS_UNIT_NAME, MGS_NIDS)
        assert mock_model.unit.status == expected_status

    def test_app_data_error(self, mocker: MockerFixture, mock_model: MagicMock) -> None:
        """OSS unit does not set up when relation data is unavailable."""
        mock_model.get_relation.return_value = None
        mock_oss = mocker.patch("lustre_peer.lustre_fs.oss_setup", autospec=True)

        observer = lustre_peer.LustrePeerObserver(mocker.MagicMock())
        observer._on_relation_changed(mocker.MagicMock())

        mock_oss.assert_not_called()

    def test_mgs_data_not_published(
        self, mocker: MockerFixture, mock_model_with_relation: tuple[MagicMock, MagicMock]
    ) -> None:
        """OSS unit does not set up when MGS NID is not published."""
        _, rel = mock_model_with_relation
        rel.load.return_value = None
        mock_oss = mocker.patch("lustre_peer.lustre_fs.oss_setup", autospec=True)

        observer = lustre_peer.LustrePeerObserver(mocker.MagicMock())
        observer._on_relation_changed(mocker.MagicMock())

        mock_oss.assert_not_called()

    def test_mgs_unit_skips_oss(self, mocker: MockerFixture, mock_model: MagicMock) -> None:
        """MGS unit does not attempt to set up OSS."""
        mock_model.app.planned_units.return_value = 1
        mock_model.unit.name = MGS_UNIT_NAME
        app_data = lustre_peer.LustrePeerAppData(mgs_nids=MGS_NIDS, mgs_unit_name=MGS_UNIT_NAME)
        mocker.patch("lustre_peer.LustrePeerObserver.get_app_data", return_value=app_data)
        mock_oss = mocker.patch("lustre_peer.lustre_fs.oss_setup", autospec=True)

        observer = lustre_peer.LustrePeerObserver(mocker.MagicMock())
        observer._on_relation_changed(mocker.MagicMock())

        mock_oss.assert_not_called()

    def test_oss_setup_failure(
        self, mocker: MockerFixture, oss_unit: tuple[MagicMock, MagicMock]
    ) -> None:
        """OSS service setup fails."""
        model, mock_oss = oss_unit
        mock_oss.side_effect = LustreFilesystemError("setup failed")

        observer = lustre_peer.LustrePeerObserver(mocker.MagicMock())
        observer._on_relation_changed(mocker.MagicMock())

        assert model.unit.status == ops.BlockedStatus(
            lustre_peer._LustrePeerStatus.FAILED_OSS_SETUP
        )

    def test_set_unit_ready_failure(
        self, mocker: MockerFixture, oss_unit: tuple[MagicMock, MagicMock]
    ) -> None:
        """OSS unit fails to set itself ready."""
        model, _ = oss_unit
        mocker.patch(
            "lustre_peer.LustrePeerObserver._set_unit_ready",
            side_effect=lustre_peer.LustrePeerError("set ready failed"),
        )

        observer = lustre_peer.LustrePeerObserver(mocker.MagicMock())
        observer._on_relation_changed(mocker.MagicMock())

        assert model.unit.status == ops.BlockedStatus(
            lustre_peer._LustrePeerStatus.FAILED_SET_UNIT_READY
        )

    def test_publish_filesystem_info_failure(
        self, mocker: MockerFixture, oss_unit: tuple[MagicMock, MagicMock]
    ) -> None:
        """Leader OSS unit filesystem info publishing attempt fails."""
        model, _ = oss_unit
        model.unit.is_leader.return_value = True
        mocker.patch("lustre_peer.LustrePeerObserver._set_unit_ready")
        mocker.patch(
            "lustre_peer.LustrePeerObserver._try_publish_filesystem_info",
            side_effect=lustre_peer.LustrePeerError("publish failed"),
        )

        observer = lustre_peer.LustrePeerObserver(mocker.MagicMock())
        observer._on_relation_changed(mocker.MagicMock())

        assert model.unit.status == ops.BlockedStatus(
            lustre_peer._LustrePeerStatus.FAILED_PUBLISH_FILESYSTEM_INFO
        )


class TestGetUnitData:
    """get_unit_data() tests."""

    def test_decodes_unquoted_values(
        self, mocker: MockerFixture, mock_model_with_relation: tuple[MagicMock, MagicMock]
    ) -> None:
        """get_unit_data decoder wraps unquoted databag values before json.loads."""
        _, rel = mock_model_with_relation
        captured = {}
        unit_data = {"ingress-address": "10.200.245.189", "other-key": '"a string"'}

        def fake_load(model, unit, decoder=lambda x: x):
            # Simulate ops reading unit data with custom decoder. The custom decoder defined in the
            # charm code is passed to this function and tested below.
            for key, value in unit_data.items():
                captured[key] = decoder(value)
            return None

        rel.load.side_effect = fake_load

        observer = lustre_peer.LustrePeerObserver(mocker.MagicMock())
        data = observer.get_unit_data()

        assert captured["ingress-address"] == "10.200.245.189"
        assert captured["other-key"] == "a string"
        assert data.ready is False


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
