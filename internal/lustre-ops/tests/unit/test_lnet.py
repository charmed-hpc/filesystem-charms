# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details

"""Unit tests for `lustre_ops.lnet`."""

import stat
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml
from lustre_ops import lnet
from lustre_ops.constants import LCTL_EXECUTABLE, LNETCTL_EXECUTABLE
from lustre_ops.errors import (
    LNetAddInterfaceError,
    LNetAddNetworkError,
    LNetAutodetectError,
    LNetConfigExportError,
    LNetParseError,
    LNetQueryError,
    LNetRemoveInterfaceError,
)
from pytest_mock import MockerFixture


@pytest.fixture(scope="function")
def mock_run(mocker: MockerFixture) -> MagicMock:
    """Mock subprocess.run."""
    return mocker.patch("lustre_ops.lnet.subprocess.run")


def _net_show_output(networks: dict[str, list[str]] | None = None) -> str:
    """Build realistic ``lnetctl net show`` YAML output.

    Args:
        networks: Mapping of net type to interfaces.
    """
    networks = networks or {}
    nets = []
    for net_type, ifaces in networks.items():
        nets.append(
            {
                "net type": net_type,
                "local NI(s)": [
                    {
                        "nid": f"10.0.0.1@{net_type}",
                        "status": "up",
                        "interfaces": {"0": iface},
                    }
                    for iface in ifaces
                ],
            }
        )
    return yaml.safe_dump({"net": nets}, sort_keys=False)


class TestInit:
    """init() tests."""

    @pytest.fixture(scope="function")
    def mock_ensure(self, mocker: MockerFixture) -> MagicMock:
        """Mock lustre_ops.lnet._ensure_networks."""
        return mocker.patch("lustre_ops.lnet._ensure_networks", autospec=True)

    @pytest.fixture(scope="function")
    def mock_persist(self, mocker: MockerFixture) -> MagicMock:
        """Mock lustre_ops.lnet._persist_lnet_config."""
        return mocker.patch("lustre_ops.lnet._persist_lnet_config", autospec=True)

    @pytest.fixture(scope="function")
    def mock_detect(self, mocker: MockerFixture) -> MagicMock:
        """Mock lustre_ops.lnet_detection.detect_networks."""
        return mocker.patch("lustre_ops.lnet.detect_networks", autospec=True)

    def test_explicit_networks(
        self,
        mock_ensure: MagicMock,
        mock_persist: MagicMock,
        mock_detect: MagicMock,
    ) -> None:
        """init() with explicit networks persists config without auto-detection."""
        nets = {"tcp": ["eth0"], "o2ib": ["ib0", "ib1"]}
        lnet.init(networks=nets)

        mock_ensure.assert_called_once_with(nets)
        mock_persist.assert_called_once()
        mock_detect.assert_not_called()

    def test_auto_detected_networks(
        self,
        mock_ensure: MagicMock,
        mock_persist: MagicMock,
        mock_detect: MagicMock,
    ) -> None:
        """init(networks=None) autodetects networks."""
        detected = {"tcp": ["eth0"], "o2ib": ["ib0", "ib1"]}
        mock_detect.return_value = detected

        lnet.init(networks=None)

        mock_ensure.assert_called_once_with(detected)

    def test_auto_detection_no_networks(
        self,
        mock_ensure: MagicMock,
        mock_persist: MagicMock,
        mock_detect: MagicMock,
    ) -> None:
        """init(networks=None) raises error when auto-detection finds no networks."""
        mock_detect.return_value = {}

        with pytest.raises(LNetAutodetectError):
            lnet.init(networks=None)


class TestEnsureNetworks:
    """_ensure_networks() tests."""

    @pytest.fixture(autouse=True)
    def _default_show(self, mock_run: MagicMock) -> None:
        """Default `lnetctl net show` that returns no configured networks."""
        mock_run.return_value = MagicMock(returncode=0, stdout=_net_show_output())

    def test_adds_missing_network_single_interface(self, mock_run: MagicMock) -> None:
        """A missing network is added with a single interface."""
        nets = {"tcp": ["eth0"]}

        lnet._ensure_networks(nets)

        # Two calls: `lnetctl net show` then `lnetctl net add`.
        assert mock_run.call_count == 2
        add_call = mock_run.call_args_list[1]
        assert add_call[0][0] == [LNETCTL_EXECUTABLE, "net", "add", "--net", "tcp", "--if", "eth0"]

    def test_adds_missing_network_multi_rail(self, mock_run: MagicMock) -> None:
        """A missing network is added with multi-rail interfaces."""
        nets = {"o2ib": ["ib0", "ib1"]}

        lnet._ensure_networks(nets)

        # Two calls: `lnetctl net show` then `lnetctl net add`.
        assert mock_run.call_count == 2
        add_call = mock_run.call_args_list[1]
        assert add_call[0][0] == [
            LNETCTL_EXECUTABLE,
            "net",
            "add",
            "--net",
            "o2ib",
            "--if",
            "ib0",
            "--if",
            "ib1",
        ]

    def test_skips_when_network_and_interfaces_exist(self, mock_run: MagicMock) -> None:
        """An existing network with matching interfaces is left untouched."""
        nets = {"tcp": ["eth0"]}
        mock_run.return_value.stdout = _net_show_output(nets)

        lnet._ensure_networks(nets)

        # No add/del calls
        assert mock_run.call_count == 1
        assert mock_run.call_args_list[0][0][0] == [LNETCTL_EXECUTABLE, "net", "show"]

    def test_adds_missing_interface_to_existing_network(self, mock_run: MagicMock) -> None:
        """A missing interface is added to an existing network (multi-rail expansion)."""
        mock_run.return_value.stdout = _net_show_output({"o2ib": ["ib0"]})
        nets = {"o2ib": ["ib0", "ib1"]}

        lnet._ensure_networks(nets)

        # net show, then interface add for ib1.
        assert mock_run.call_count == 2
        add_call = mock_run.call_args_list[1]
        assert add_call[0][0] == [
            LNETCTL_EXECUTABLE,
            "interface",
            "add",
            "--net",
            "o2ib",
            "--if",
            "ib1",
        ]

    def test_removes_stale_interface_from_existing_network(self, mock_run: MagicMock) -> None:
        """A bound interface not in the desired set is removed."""
        mock_run.return_value.stdout = _net_show_output({"tcp": ["eth0", "eth1"]})
        nets = {"tcp": ["eth0"]}

        lnet._ensure_networks(nets)

        del_call = mock_run.call_args_list[1]
        assert del_call[0][0] == [
            LNETCTL_EXECUTABLE,
            "interface",
            "del",
            "--net",
            "tcp",
            "--if",
            "eth1",
        ]

    def test_add_interface_failure_raises(self, mock_run: MagicMock) -> None:
        """A failure on interface add raises error."""
        mock_run.return_value.stdout = _net_show_output({"o2ib": ["ib0"]})
        mock_run.side_effect = [
            mock_run.return_value,
            subprocess.CalledProcessError(1, LNETCTL_EXECUTABLE),
        ]
        nets = {"o2ib": ["ib0", "ib1"]}

        with pytest.raises(LNetAddInterfaceError):
            lnet._ensure_networks(nets)

    def test_remove_interface_failure_raises(self, mock_run: MagicMock) -> None:
        """A failure on interface removal raises error."""
        mock_run.return_value.stdout = _net_show_output({"tcp": ["eth0", "eth1"]})
        mock_run.side_effect = [
            mock_run.return_value,
            subprocess.CalledProcessError(1, LNETCTL_EXECUTABLE),
        ]
        nets = {"tcp": ["eth0"]}  # eth1 is stale and should be removed

        with pytest.raises(LNetRemoveInterfaceError):
            lnet._ensure_networks(nets)

    def test_net_add_failure_raises(self, mock_run: MagicMock) -> None:
        """A failure adding a missing network raises error."""
        mock_run.side_effect = [
            mock_run.return_value,
            subprocess.CalledProcessError(1, LNETCTL_EXECUTABLE),
        ]
        nets = {"tcp": ["eth0"]}

        with pytest.raises(LNetAddNetworkError):
            lnet._ensure_networks(nets)

    def test_multiple_networks(self, mock_run: MagicMock) -> None:
        """Multiple new networks are successfully added."""
        nets = {
            "tcp": ["eth0"],
            "o2ib": ["ib0", "ib1"],
        }

        lnet._ensure_networks(nets)

        # One net show call then two net add calls.
        assert mock_run.call_count == 3
        assert mock_run.call_args_list[1][0][0] == [
            LNETCTL_EXECUTABLE,
            "net",
            "add",
            "--net",
            "tcp",
            "--if",
            "eth0",
        ]
        assert mock_run.call_args_list[2][0][0] == [
            LNETCTL_EXECUTABLE,
            "net",
            "add",
            "--net",
            "o2ib",
            "--if",
            "ib0",
            "--if",
            "ib1",
        ]

    def test_nonzero_net_seq(self, mock_run: MagicMock) -> None:
        """A new network with a non-zero net_seq is added."""
        nets = {"o2ib1": ["ib2"]}

        lnet._ensure_networks(nets)

        show_call = mock_run.call_args_list[0]
        assert show_call[0][0] == [LNETCTL_EXECUTABLE, "net", "show"]
        add_call = mock_run.call_args_list[1]
        assert add_call[0][0] == [
            LNETCTL_EXECUTABLE,
            "net",
            "add",
            "--net",
            "o2ib1",
            "--if",
            "ib2",
        ]

    def test_lnetctl_not_found_on_show(self, mock_run: MagicMock) -> None:
        """FileNotFoundError on net show raises error."""
        mock_run.side_effect = FileNotFoundError(1, "/bad/path/to/lnetctl")
        nets = {"tcp": ["eth0"]}

        with pytest.raises(LNetQueryError):
            lnet._ensure_networks(nets)

    def test_net_show_failure_raises(self, mock_run: MagicMock) -> None:
        """A non-zero exit from net show raises error."""
        mock_run.side_effect = subprocess.CalledProcessError(
            1, LNETCTL_EXECUTABLE, stderr="error running lnetctl net show"
        )
        nets = {"tcp": ["eth0"]}

        with pytest.raises(LNetQueryError):
            lnet._ensure_networks(nets)


class TestParseNetworkConfig:
    """parse_network_config() tests."""

    @pytest.mark.parametrize(
        ("spec", "expected"),
        [
            pytest.param("tcp0=eth0", {"tcp": ["eth0"]}, id="single_network_single_interface"),
            pytest.param(
                "o2ib0=ib0,ib1", {"o2ib": ["ib0", "ib1"]}, id="single_network_multi_rail"
            ),
            pytest.param(
                "tcp0=eth0; o2ib0=ib0,ib1",
                {"tcp": ["eth0"], "o2ib": ["ib0", "ib1"]},
                id="multiple_networks",
            ),
            pytest.param("o2ib1=ib2", {"o2ib1": ["ib2"]}, id="nonzero_seq"),
            pytest.param(
                "  tcp0 = eth0 , eth1  ", {"tcp": ["eth0", "eth1"]}, id="strips_whitespace"
            ),
            pytest.param("tcp=eth0;;", {"tcp": ["eth0"]}, id="empty_token_skipped"),
            pytest.param("o2ib10=ib0", {"o2ib10": ["ib0"]}, id="multi_digit_name_not_stripped"),
            pytest.param("o2ib0=ib0", {"o2ib": ["ib0"]}, id="zero_seq_stripped"),
        ],
    )
    def test_parse_valid(self, spec: str, expected: dict[str, list[str]]) -> None:
        """Valid specifications parse to the expected network mapping."""
        assert lnet.parse_network_config(spec) == expected

    @pytest.mark.parametrize(
        "spec",
        [pytest.param("", id="empty_string"), pytest.param("   ", id="whitespace_only")],
    )
    def test_empty_yields_none(self, spec: str) -> None:
        """An empty or whitespace-only spec yields ``None``."""
        assert lnet.parse_network_config(spec) is None

    @pytest.mark.parametrize(
        ("spec", "match"),
        [
            pytest.param("tcp=", "No interfaces", id="no_interfaces"),
            pytest.param("tcp:eth0", "Invalid LNet network specification", id="no_equals_sign"),
            pytest.param("=eth0", "Empty network name", id="empty_name"),
            pytest.param("tcp=eth0;tcp=eth1", "Duplicate LNet network", id="duplicate_network"),
        ],
    )
    def test_invalid_raises(self, spec: str, match: str) -> None:
        """A malformed specification raises error with a descriptive message."""
        with pytest.raises(LNetParseError, match=match):
            lnet.parse_network_config(spec)

    def test_trailing_zero_equivalent_to_bare_name(self) -> None:
        """A trailing 0 on a single-digit network name is stripped (tcp0 == tcp)."""
        assert lnet.parse_network_config("tcp0=eth0") == lnet.parse_network_config("tcp=eth0")


class TestParseNetworks:
    """_parse_networks() tests."""

    def test_extracts_interfaces(self) -> None:
        """Interfaces are parsed into a list keyed by network name."""
        assert lnet._parse_networks(_net_show_output({"tcp": ["eth0", "eth1"]})) == {
            "tcp": ["eth0", "eth1"]
        }

    def test_no_interfaces(self) -> None:
        """A NI without an 'interfaces' key contributes no interfaces."""
        output = (
            "net:\n"
            "-    net type: tcp\n"
            "     local NI(s):\n"
            "     -    nid: 10.0.0.5@tcp\n"
            "          status: up\n"
        )
        assert lnet._parse_networks(output) == {"tcp": []}

    def test_no_local_nis_raises(self) -> None:
        """A network with no 'local NI(s)' raises error."""
        output = "net:\n-    net type: tcp\n"
        with pytest.raises(LNetParseError, match="Failed to validate LNet network output"):
            lnet._parse_networks(output)

    def test_multi_ni_multi_rail(self) -> None:
        """Interfaces across multiple NIs are all collected."""
        assert lnet._parse_networks(_net_show_output({"o2ib": ["enp6s0", "enp7s0"]})) == {
            "o2ib": ["enp6s0", "enp7s0"]
        }

    def test_distinct_network_names_preserved(self) -> None:
        """Networks with numeric suffixes are kept distinct."""
        assert lnet._parse_networks(
            _net_show_output({"o2ib": ["enp6s0"], "o2ib1": ["enp5s0"]})
        ) == {"o2ib": ["enp6s0"], "o2ib1": ["enp5s0"]}

    def test_empty_output_raises(self) -> None:
        """Empty stdout raises error."""
        with pytest.raises(LNetParseError, match="Empty LNet network output"):
            lnet._parse_networks("")

    def test_no_nets_returns_empty(self) -> None:
        """Output with no 'net' key yields an empty dict."""
        assert lnet._parse_networks("net:\n") == {}

    def test_malformed_yaml_raises(self) -> None:
        """Malformed YAML raises error."""
        with pytest.raises(LNetParseError, match="Failed to parse LNet network output"):
            lnet._parse_networks("net:\n    - [unclosed")


class TestGetNids:
    """get_nids() tests."""

    @pytest.mark.parametrize(
        ("stdout", "expected"),
        [
            pytest.param(
                "10.0.0.5@tcp\n10.0.0.6@tcp\n",
                ["10.0.0.5@tcp", "10.0.0.6@tcp"],
                id="multiple_nids",
            ),
            pytest.param("", [], id="empty"),
            pytest.param("\n", [], id="blank_line_only"),
        ],
    )
    def test_returns_nids(self, mock_run: MagicMock, stdout: str, expected: list[str]) -> None:
        """get_nids() parses lctl stdout into a list of NIDs."""
        mock_run.return_value.stdout = stdout

        assert lnet.get_nids() == expected
        mock_run.assert_called_once_with(
            [LCTL_EXECUTABLE, "list_nids"], capture_output=True, text=True, check=True
        )

    @pytest.mark.parametrize(
        ("side_effect"),
        [
            pytest.param(
                subprocess.CalledProcessError(1, LCTL_EXECUTABLE),
                id="run_error",
            ),
            pytest.param(
                FileNotFoundError(1, "/bad/path/to/lctl"),
                id="not_found",
            ),
        ],
    )
    def test_nid_errors(self, mock_run: MagicMock, side_effect: Exception) -> None:
        """Errors from lctl are raised."""
        mock_run.side_effect = side_effect

        with pytest.raises(LNetQueryError):
            lnet.get_nids()


class TestPersistLnetConfig:
    """persist_lnet_config() tests."""

    def test_successful_export(
        self, mocker: MockerFixture, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """Exports the LNet configuration to a file with 0600 permissions."""
        conf_path = tmp_path / "lnet.conf"
        mock_run.return_value = MagicMock(returncode=0, stdout="test config data")
        mocker.patch("lustre_ops.lnet.LUSTRE_LNET_CONF", conf_path)

        lnet._persist_lnet_config()

        assert conf_path.read_text() == "test config data"
        assert stat.S_IMODE(conf_path.stat().st_mode) == 0o600

    def test_export_failure(self, mock_run: MagicMock) -> None:
        """Lnetctl export failure."""
        mock_run.side_effect = subprocess.CalledProcessError(1, LNETCTL_EXECUTABLE)

        with pytest.raises(LNetConfigExportError):
            lnet._persist_lnet_config()
