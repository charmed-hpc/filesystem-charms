# Copyright 2024-2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for `utils.manager.MountsManager`."""

import pathlib
from unittest.mock import MagicMock

import pytest
from lustre_ops.errors import LNetError
from pytest_mock import MockerFixture
from utils.manager import Error, MountsManager, lnet


@pytest.fixture(scope="function")
def manager(mocker: MockerFixture, tmp_path: pathlib.Path) -> MountsManager:
    """Mock MountsManager with package and autofs concerns stubbed out."""
    charm = MagicMock()
    charm.unit.name = "filesystem-client/0"
    mgr = MountsManager(charm)
    # Stub out package installation and autofs file handling so setup() reaches the lnet path.
    mocker.patch.object(mgr, "_packages", return_value=[])
    mocker.patch.object(mgr, "_master_file", tmp_path / "master")
    mocker.patch.object(mgr, "_autofs_file", tmp_path / "autofs")
    return mgr


class TestSetupLnet:
    """LNet configuration wiring in MountsManager.setup()."""

    def test_lustre_disabled_skips_lnet(
        self, manager: MountsManager, mocker: MockerFixture
    ) -> None:
        """LNet is not configured when enable_lustre is False."""
        mock_init = mocker.patch.object(lnet, "init")
        manager.enable_lustre = False

        manager.setup()

        mock_init.assert_not_called()

    def test_config_spec_forwarded(self, manager: MountsManager, mocker: MockerFixture) -> None:
        """A non-empty lnet-networks spec is parsed and forwarded to lnet.init."""
        mock_init = mocker.patch.object(lnet, "init")
        manager.enable_lustre = True
        manager.lnet_networks_spec = "tcp=eth0; o2ib=ib0,ib1"

        manager.setup()

        mock_init.assert_called_once()
        _, kwargs = mock_init.call_args
        networks = kwargs["networks"]
        assert networks == {"tcp": ["eth0"], "o2ib": ["ib0", "ib1"]}

    def test_lnet_error_wrapped(self, manager: MountsManager, mocker: MockerFixture) -> None:
        """An LNetError from init is wrapped as a manager Error."""
        lnet_error_message = "test lnet error message"
        mocker.patch.object(lnet, "init", side_effect=LNetError(lnet_error_message))
        manager.enable_lustre = True
        manager.lnet_networks_spec = ""

        with pytest.raises(Error, match=lnet_error_message):
            manager.setup()
