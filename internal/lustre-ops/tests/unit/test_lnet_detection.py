# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details

"""Unit tests for `lustre_ops.lnet_detection`."""

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from lustre_ops import lnet_detection
from lustre_ops.errors import LNetParseError, LNetQueryError
from pytest_mock import MockerFixture


@pytest.fixture(scope="function")
def mock_run(mocker: MockerFixture) -> MagicMock:
    """Mock subprocess.run."""
    return mocker.patch("lustre_ops.lnet_detection.subprocess.run")


class TestDetectNetworks:
    """detect_networks() tests."""

    @pytest.mark.parametrize(
        ("default_route", "rdma_ifaces", "expected"),
        [
            ("eth0", [], {"tcp": ["eth0"]}),
            (None, ["ib0", "ib1"], {"o2ib": ["ib0", "ib1"]}),
            ("eth0", ["ib0", "ib1"], {"tcp": ["eth0"], "o2ib": ["ib0", "ib1"]}),
            (None, [], {}),
        ],
        ids=["ethernet_only", "ib_only", "mixed", "no_interfaces"],
    )
    def test_detect_networks(
        self,
        mocker: MockerFixture,
        default_route: str | None,
        rdma_ifaces: list[str],
        expected: dict[str, list[str]],
    ) -> None:
        """detect_networks() combines the default route and RDMA interfaces."""
        mocker.patch(
            "lustre_ops.lnet_detection._default_route_interface", return_value=default_route
        )
        mocker.patch("lustre_ops.lnet_detection._rdma_interfaces", return_value=rdma_ifaces)

        assert lnet_detection.detect_networks() == expected


class TestDefaultRouteInterface:
    """_default_route_interface() tests."""

    def test_success(self, mock_run: MagicMock) -> None:
        """Successfully retrieves the default network interface."""
        mock_run.return_value.stdout = json.dumps([{"dev": "eth0"}])

        assert lnet_detection._default_route_interface() == "eth0"

    def test_no_default_route(self, mock_run: MagicMock) -> None:
        """Returns None when there is no default route."""
        mock_run.return_value.stdout = json.dumps([])

        assert lnet_detection._default_route_interface() is None

    def test_ip_run_error(self, mock_run: MagicMock) -> None:
        """Ip command fails."""
        mock_run.side_effect = subprocess.CalledProcessError(1, "ip")

        with pytest.raises(LNetQueryError):
            lnet_detection._default_route_interface()

    def test_bad_json(self, mock_run: MagicMock) -> None:
        """Ip command returns invalid JSON."""
        mock_run.return_value.stdout = "not json"

        with pytest.raises(LNetParseError):
            lnet_detection._default_route_interface()

    def test_missing_dev_key(self, mock_run: MagicMock) -> None:
        """Ip command returns JSON without 'dev' key."""
        mock_run.return_value.stdout = json.dumps([{"not_dev": "eth0"}])

        with pytest.raises(LNetParseError):
            lnet_detection._default_route_interface()


class TestRdmaInterfaces:
    """_rdma_interfaces() tests."""

    def _rdma_link(
        self,
        ifname: str | None = "mlx5_0",
        netdev: str | None = "ib0",
        state: str = "ACTIVE",
        physical_state: str = "LINK_UP",
    ) -> dict[str, str | int]:
        """Build an ``rdma --json link show`` entry with typical default values."""
        link: dict[str, str | int] = {"port": 1, "state": state, "physical_state": physical_state}
        if ifname is not None:
            link["ifname"] = ifname
        if netdev is not None:
            link["netdev"] = netdev
        return link

    def test_finds_netdevs(self, mock_run: MagicMock) -> None:
        """Parses netdev names from JSON input."""
        mock_run.return_value.stdout = json.dumps(
            [
                self._rdma_link("mlx5_0", netdev="ib0"),
                self._rdma_link("mlx5_1", netdev="ib1"),
            ]
        )

        assert lnet_detection._rdma_interfaces() == ["ib0", "ib1"]

    def test_ethernet_backed_rdma_device(self, mock_run: MagicMock) -> None:
        """Ethernet-backed RDMA devices (Soft-RoCE rxe devices) are detected."""
        mock_run.return_value.stdout = json.dumps([self._rdma_link("rxe0", netdev="eth0")])

        assert lnet_detection._rdma_interfaces() == ["eth0"]

    def test_deduplicates_across_links(self, mock_run: MagicMock) -> None:
        """The same netdev across multiple links is listed only once."""
        mock_run.return_value.stdout = json.dumps(
            [
                self._rdma_link("mlx5_0", netdev="ib0"),
                self._rdma_link("mlx5_1", netdev="ib0"),
            ]
        )

        assert lnet_detection._rdma_interfaces() == ["ib0"]

    def test_no_rdma_devices(self, mock_run: MagicMock) -> None:
        """An empty rdma link list yields an empty list."""
        mock_run.return_value.stdout = json.dumps([])

        assert lnet_detection._rdma_interfaces() == []

    def test_skips_inactive_links(self, mock_run: MagicMock) -> None:
        """Links not in ACTIVE/LINK_UP state are skipped."""
        mock_run.return_value.stdout = json.dumps(
            [
                self._rdma_link("mlx5_0", netdev="ib0", state="DOWN"),
                self._rdma_link("mlx5_1", netdev="ib1"),
            ]
        )

        assert lnet_detection._rdma_interfaces() == ["ib1"]

    def test_sorts_results(self, mock_run: MagicMock) -> None:
        """Returned netdevs are sorted by name."""
        mock_run.return_value.stdout = json.dumps(
            [
                self._rdma_link("mlx5_1", netdev="ib1"),
                self._rdma_link("mlx5_0", netdev="ib0"),
            ]
        )

        assert lnet_detection._rdma_interfaces() == ["ib0", "ib1"]

    def test_ipoib_lookup_for_missing_netdev(
        self, mock_run: MagicMock, mocker: MockerFixture
    ) -> None:
        """Links without a netdev are resolved via the IPoIB sysfs map."""
        mock_run.return_value.stdout = json.dumps([self._rdma_link("mlx5_0", netdev=None)])
        mock_ipoib = mocker.patch(
            "lustre_ops.lnet_detection._ipoib_netdev_map", return_value={"mlx5_0": "ib0"}
        )

        assert lnet_detection._rdma_interfaces() == ["ib0"]
        mock_ipoib.assert_called_once()

    def test_skips_link_with_no_ifname_and_no_netdev(
        self, mock_run: MagicMock, mocker: MockerFixture
    ) -> None:
        """A link missing both netdev and ifname is skipped after map lookup."""
        mock_run.return_value.stdout = json.dumps([self._rdma_link(ifname=None, netdev=None)])
        mocker.patch("lustre_ops.lnet_detection._ipoib_netdev_map", return_value={})

        assert lnet_detection._rdma_interfaces() == []

    def test_skips_link_with_no_matching_ipoib_netdev(
        self, mock_run: MagicMock, mocker: MockerFixture
    ) -> None:
        """A link whose ifname has no IPoIB netdev mapping is skipped."""
        mock_run.return_value.stdout = json.dumps([self._rdma_link("mlx5_0", netdev=None)])
        mocker.patch("lustre_ops.lnet_detection._ipoib_netdev_map", return_value={})

        assert lnet_detection._rdma_interfaces() == []

    def test_rdma_not_found(self, mock_run: MagicMock) -> None:
        """FileNotFoundError (rdma not installed) raises error."""
        mock_run.side_effect = FileNotFoundError(1, "/bad/path/to/rdma")

        with pytest.raises(LNetQueryError):
            lnet_detection._rdma_interfaces()

    def test_rdma_command_fails(self, mock_run: MagicMock) -> None:
        """A non-zero exit from rdma raises error."""
        mock_run.side_effect = subprocess.CalledProcessError(1, "rdma error")

        with pytest.raises(LNetQueryError):
            lnet_detection._rdma_interfaces()

    def test_bad_json(self, mock_run: MagicMock) -> None:
        """Invalid JSON output raises error."""
        mock_run.return_value.stdout = "not json"

        with pytest.raises(LNetParseError):
            lnet_detection._rdma_interfaces()


class TestIpoibNetdevMap:
    """_ipoib_netdev_map() tests."""

    @pytest.fixture(scope="function")
    def sys_class_net(self, mocker: MockerFixture, tmp_path: Path) -> Path:
        """Return sysfs net root mocked to a temp directory."""
        mocker.patch("lustre_ops.lnet_detection.SYS_CLASS_NET", tmp_path)
        return tmp_path

    @staticmethod
    def _add_ipoib_netdev(root: Path, netdev: str, ib_devs: list[str]) -> None:
        """Create a test netdev backed by the given InfiniBand device(s)."""
        ib_path = root / netdev / "device" / "infiniband"
        ib_path.mkdir(parents=True)
        for dev in ib_devs:
            (ib_path / dev).mkdir()

    def test_maps_single_ib_device(self, sys_class_net: Path) -> None:
        """A netdev backed by exactly one IB device is mapped."""
        self._add_ipoib_netdev(sys_class_net, "ib0", ["mlx5_0"])

        assert lnet_detection._ipoib_netdev_map() == {"mlx5_0": "ib0"}

    def test_skips_netdev_without_ib_device(self, sys_class_net: Path) -> None:
        """Netdevs without an infiniband device directory are skipped."""
        (sys_class_net / "eth0").mkdir()

        assert lnet_detection._ipoib_netdev_map() == {}

    def test_skips_netdev_with_multiple_ib_devices(self, sys_class_net: Path) -> None:
        """Netdevs backed by multiple IB devices are skipped with a warning."""
        self._add_ipoib_netdev(sys_class_net, "ib0", ["mlx5_0", "mlx5_1"])

        assert lnet_detection._ipoib_netdev_map() == {}

    def test_empty_sysfs(self, sys_class_net: Path) -> None:
        """No netdevs in /sys/class/net yields an empty map."""
        assert lnet_detection._ipoib_netdev_map() == {}
