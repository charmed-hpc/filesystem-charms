# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details

"""Auto-detection of LNet networks from host interfaces."""

import json
import logging
import subprocess

from lustre_ops.constants import IP_EXECUTABLE, RDMA_EXECUTABLE, SYS_CLASS_NET
from lustre_ops.errors import LNetParseError, LNetQueryError

_logger = logging.getLogger(__name__)


def detect_networks() -> dict[str, list[str]]:
    """Auto-detect the LNet networks to configure on this unit.

    Returns a dict containing:
        - One ``tcp`` network specifying the default-route interface (if any).
        - One ``o2ib`` network specifying every RDMA netdev detected (multi-rail when >1).

    Returns:
        The detected LNet networks. May be empty if neither a default route nor any RDMA devices are
        present.

    Raises:
        LNetAutodetectError: If detection fails.
    """
    networks: dict[str, list[str]] = {}

    default_interface = _default_route_interface()
    _logger.info("auto-detected TCP interfaces: %s", default_interface)
    if default_interface is not None:
        networks["tcp"] = [default_interface]

    rdma_interfaces = _rdma_interfaces()
    _logger.info("auto-detected RDMA interfaces: %s", rdma_interfaces)
    if rdma_interfaces:
        networks["o2ib"] = rdma_interfaces

    return networks


def _default_route_interface() -> str | None:
    """Return the default-route interface name, or ``None`` if there is none.

    Raises:
        LNetQueryError: If querying the default route fails.
        LNetParseError: If parsing the default route data fails.
    """
    try:
        result = subprocess.run(
            [IP_EXECUTABLE, "-json", "route", "show", "default"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        raise LNetQueryError("Failed to query default network interface") from e

    try:
        routes = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise LNetParseError("Failed to parse default route data") from e

    if not routes:
        return None
    try:
        return routes[0]["dev"]
    except KeyError as e:
        raise LNetParseError("Failed to extract default network interface from route data") from e


def _ipoib_netdev_map() -> dict[str, str]:
    """Build a mapping of RDMA device name to netdev for native IPoIB devices.

    Returns:
        A mapping from RDMA device name (example: ``mlx5_0``) to netdev name (example: ``ib0``). May
        be empty if no IPoIB devices are present.
    """
    # IPoIB devices are represented in sysfs at `/sys/class/net/*/device/infiniband`. Scan this for
    # netdevs backed by an InfiniBand device.
    rdma_net_map: dict[str, str] = {}
    for netdev in SYS_CLASS_NET.iterdir():
        ib_path = netdev / "device/infiniband"
        if not ib_path.exists():
            continue

        ib_devs = list(ib_path.iterdir())
        if len(ib_devs) != 1:
            _logger.warning(
                "unexpected number of InfiniBand devices for netdev %s. expected 1, got: %s",
                netdev.name,
                ib_devs,
            )
            continue

        rdma_net_map[ib_devs[0].name] = netdev.name
    return rdma_net_map


def _rdma_interfaces() -> list[str]:
    """Return netdev names associated with RDMA devices on this unit.

    Returns:
        A list of netdev names (e.g. ``ib0``, ``ib1``) for all RDMA devices that are active and have
        a physical link up. Empty if no RDMA devices are present or none are active.

    Raises:
        LNetQueryError: If querying RDMA links fails.
        LNetParseError: If parsing RDMA link data fails.
    """
    try:
        result = subprocess.run(
            [RDMA_EXECUTABLE, "--json", "link", "show"],
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError as e:
        raise LNetQueryError(f"Failed to query RDMA links: {RDMA_EXECUTABLE} not found") from e
    except subprocess.CalledProcessError as e:
        raise LNetQueryError("Failed to query RDMA links") from e

    try:
        links = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise LNetParseError("Failed to parse RDMA link data") from e

    # Map of RDMA device (e.g. mlx5_0) to IPoIB netdev (e.g. ib0) is necessary as the `rdma` command
    # does not give the netdev for IPoIB devices. LNet requires netdev name for o2ib networks (RDMA
    # device name is not sufficient).
    ipoib_map = _ipoib_netdev_map()

    seen_interfaces: set[str] = set()
    for link in links:
        if not (link.get("state") == "ACTIVE" and link.get("physical_state") == "LINK_UP"):
            _logger.info("ignoring inactive RDMA link: %s", link)
            continue

        netdev = link.get("netdev")
        if not netdev:
            _logger.debug("RDMA link has no netdev: %s", link)

            ifname = link.get("ifname")
            if not ifname:
                _logger.warning("RDMA link has no ifname: %s", link)
                continue

            netdev = ipoib_map.get(ifname)
            if not netdev:
                _logger.warning("no IPoIB netdev found for RDMA device %s", ifname)
                continue

        if netdev not in seen_interfaces:
            seen_interfaces.add(netdev)
            _logger.debug("detected RDMA interface %s for link %s", netdev, link)

    rdma_interfaces = sorted(seen_interfaces)
    return rdma_interfaces
