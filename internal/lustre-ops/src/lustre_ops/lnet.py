# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details

"""LNet operations shared by the Lustre server and filesystem client charms.

Supports TCP (`tcp`) and InfiniBand (`o2ib`) LNDs, multiple networks, and
multi-rail (multiple interfaces bound to a single LNet network).
"""

import logging
import subprocess

import yaml
from pydantic import BaseModel, Field, ValidationError

from lustre_ops.constants import (
    LCTL_EXECUTABLE,
    LNETCTL_EXECUTABLE,
    LUSTRE_LNET_CONF,
)
from lustre_ops.errors import (
    LNetAddInterfaceError,
    LNetAddNetworkError,
    LNetAutodetectError,
    LNetConfigExportError,
    LNetParseError,
    LNetQueryError,
    LNetRemoveInterfaceError,
)
from lustre_ops.lnet_detection import detect_networks

_logger = logging.getLogger(__name__)


class _LocalNI(BaseModel):
    """A local Network Interface entry from ``lnetctl net show`` output."""

    interfaces: dict[int, str] = Field(default_factory=dict)


class _Net(BaseModel):
    """A network entry from ``lnetctl net show`` output."""

    net_type: str = Field(alias="net type")
    local_nis: list[_LocalNI] = Field(alias="local NI(s)", min_length=1)


class _NetShowOutput(BaseModel):
    """Parsed ``lnetctl net show`` output."""

    net: list[_Net] | None = None


def init(networks: dict[str, list[str]] | None = None) -> None:
    """Initialize LNet on this unit. Idempotent.

    When ``networks`` is ``None``, LNet networks are auto-configured based on available interfaces
    on the host: a ``tcp`` network is configured on the default-route interface and an ``o2ib``
    network is configured on all detected RDMA netdevs.

    When ``networks`` is provided, it is used as-is, overriding auto-detection.

    Args:
        networks: Dictionary of LNet networks and interfaces to configure. Example:
        ``{"tcp": ["eth0"], "o2ib": ["ib0", "ib1"]}``.
        If ``None``, networks are auto-detected.

    Raises:
        LNetAutodetectError: If auto-detection finds no usable network interfaces.
        LNetError: If any other LNet operation fails.
    """
    if networks is None:
        networks = detect_networks()
        _logger.info("LNet networks determined by auto-detection: %s", networks)
        if not networks:
            raise LNetAutodetectError("Auto-detection found no usable network interfaces")

    _ensure_networks(networks)
    _persist_lnet_config()


def get_nids() -> list[str]:
    """Return all Lustre NIDs configured on this unit.

    Returns:
        A list of NID strings in format <address>@<LND protocol><lnd#>. Example: ["10.0.0.5@tcp"].
        Empty if no NIDs are configured.

    Raises:
        LNetQueryError: If querying the NIDs fails.
    """
    try:
        result = subprocess.run(
            [LCTL_EXECUTABLE, "list_nids"], capture_output=True, text=True, check=True
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        raise LNetQueryError("Failed to query Lustre NIDs") from e

    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def parse_network_config(spec: str) -> dict[str, list[str]] | None:
    """Parse an operator-supplied LNet network specification string.

    The string is a semicolon-separated list of networks of the form:
    ``<name>=<iface>[,<iface>...]``, where ``<name>`` is the full LNet network name
    (e.g. ``tcp``, ``o2ib1``). Multiple interfaces on one network constitute
    multi-rail. Examples:

        tcp=eth0
        o2ib=ib0,ib1
        tcp=eth0; o2ib=ib0,ib1
        tcp=eth0; tcp1=eth1;
        o2ib1=ib2

    Args:
        spec: The specification string.

    Returns:
        A mapping from LNet network name to its interfaces. `None` if an empty/whitespace-only spec
        is provided.

    Raises:
        LNetParseError: If the specification is malformed.
    """
    spec = spec.strip()
    if not spec:
        return None

    networks: dict[str, list[str]] = {}
    for token in spec.split(";"):
        token = token.strip()
        if not token:
            continue
        if "=" not in token:
            raise LNetParseError(f"Invalid LNet network specification `{token}`")

        name, interfaces_str = token.split("=", 1)
        name = name.strip()
        if not name:
            raise LNetParseError(f"Empty network name in `{token}`")

        # Special case of index 0 network must be handled.
        # LNet indexes from 0 but does not require the trailing 0 for the first net.
        # Example:
        #   `lnetctl net add --net tcp --if eth0`
        # and:
        #   `lnetctl net add --net tcp0 --if eth0`
        # are equivalent, and `lnetctl net show` always renders the NIDs as `@tcp` (no trailing 0).
        #
        # Check for a trailing 0 here and remove it to ensure configurations like `tcp=eth0` and
        # `tcp0=eth0` are treated equivalently. Second-to-last character is also checked to avoid
        # removing a trailing 0 from a network name such as `o2ib10`.
        if len(name) > 1 and name[-1] == "0" and not name[-2].isdigit():
            name = name[:-1]

        interfaces = [s.strip() for s in interfaces_str.split(",") if s.strip()]
        if not interfaces:
            raise LNetParseError(f"No interfaces specified for network `{token}`")

        if name in networks:
            raise LNetParseError(f"Duplicate LNet network `{name}`")
        networks[name] = interfaces

    return networks


def _add_interfaces(net: str, interfaces: set[str]) -> None:
    """Add interfaces to an existing LNet network.

    Args:
        net: The LNet network name (e.g. ``tcp0``).
        interfaces: The interfaces to add.

    Raises:
        LNetAddInterfaceError: If adding interfaces fails.
    """
    cmd = [LNETCTL_EXECUTABLE, "interface", "add", "--net", net]
    for iface in sorted(interfaces):  # sort to ensure deterministic interface order
        cmd += ["--if", iface]

    try:
        subprocess.run(cmd, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        raise LNetAddInterfaceError(
            f"Failed to add interfaces {interfaces} to LNet network {net}"
        ) from e


def _add_network(name: str, interfaces: list[str]) -> None:
    """Add a new LNet network with all given interfaces.

    Args:
        name: The LNet network name (e.g. ``tcp0``, ``o2ib1``).
        interfaces: The interfaces to bind to this network.

    Raises:
        LNetAddNetworkError: If adding the network fails.
    """
    cmd = [LNETCTL_EXECUTABLE, "net", "add", "--net", name]
    for iface in interfaces:
        cmd += ["--if", iface]
    try:
        subprocess.run(cmd, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        raise LNetAddNetworkError(f"Failed to add LNet network {name}") from e


def _ensure_networks(networks: dict[str, list[str]]) -> None:
    """Ensure each of the given LNet networks is configured as specified. Idempotent.

    If a given network does not exist, it is created. If it already exists, missing interfaces are
    added and stale interfaces are removed. Networks that exist on the host but are absent from
    ``networks`` are not removed.

    Args:
        networks: Dictionary of LNet networks and interfaces to configure. Example:
        ``{"tcp": ["eth0"], "o2ib": ["ib0", "ib1"]}``.

    Raises:
        LNetAddNetworkError: If adding a network fails.
        LNetAddInterfaceError: If adding interfaces fails.
        LNetQueryError: If querying the existing LNet networks fails.
        LNetRemoveInterfaceError: If removing interfaces fails.
    """
    try:
        result = subprocess.run(
            [LNETCTL_EXECUTABLE, "net", "show"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        raise LNetQueryError("Failed to query LNet networks") from e

    # TODO: Address potential TOCTOU race. State of LNet networks may change between above query and
    # the add/remove operations below. This will only occur if the networks are being modified by
    # another process outside of charm control (unlikely).

    existing_networks = _parse_networks(result.stdout)
    for name, interfaces in networks.items():
        if name not in existing_networks:
            # Network is missing: add it and all interfaces.
            _add_network(name, interfaces)
            _logger.debug("added LNet network %s with interfaces %s", name, interfaces)
            continue

        # Network exists: reconcile interfaces.
        desired_interfaces = set(interfaces)
        bound_interfaces = set(existing_networks.get(name, []))

        to_add = desired_interfaces - bound_interfaces
        if to_add:
            _add_interfaces(name, to_add)
            _logger.debug("added interfaces %s to LNet network %s", to_add, name)

        to_remove = bound_interfaces - desired_interfaces
        if to_remove:
            _remove_interfaces(name, to_remove)
            _logger.debug("removed interfaces %s from LNet network %s", to_remove, name)


def _persist_lnet_config() -> None:
    """Export the current LNet configuration to the disk.

    Raises:
        LNetConfigExportError: If persisting the LNet configuration fails.
    """
    try:
        result = subprocess.run(
            [LNETCTL_EXECUTABLE, "export", "--backup"], text=True, check=True, capture_output=True
        ).stdout

        # Write to temp file then atomically replace existing config. Avoids leaving partial config
        # file if write process is interrupted.
        tmp = LUSTRE_LNET_CONF.with_name(f".{LUSTRE_LNET_CONF.name}.tmp")
        tmp.unlink(missing_ok=True)  # Clean up any failed previous attempt.
        tmp.touch(mode=0o600)
        tmp.write_text(result)
        tmp.replace(LUSTRE_LNET_CONF)
    except (subprocess.CalledProcessError, OSError) as e:
        raise LNetConfigExportError("Failed to write LNet configuration data") from e


def _parse_networks(show_output: str) -> dict[str, list[str]]:
    r"""Parse all networks and their bound interfaces from ``lnetctl net show`` output.

    Args:
        show_output: The raw stdout of ``lnetctl net show``.

    Returns:
        A mapping from LNet network name to its interfaces.

    Raises:
        LNetParseError: If the given string cannot be parsed.

    Examples:
        >>> show_output = '''\
        ... net:
        ... -     net type: lo
        ...       local NI(s):
        ...       -     nid: 0@lo
        ...             status: up
        ... -     net type: o2ib
        ...       local NI(s):
        ...       -     nid: 10.0.0.10@o2ib
        ...             status: up
        ...             interfaces:
        ...                   0: enp6s0
        ...       -     nid: 10.0.0.11@o2ib
        ...             status: up
        ...             interfaces:
        ...                   0: enp7s0
        ... -     net type: tcp
        ...       local NI(s):
        ...       -     nid: 10.200.245.133@tcp
        ...             status: up
        ...             interfaces:
        ...                   0: enp5s0
        ... '''
        >>> _parse_networks(show_output)
        {'lo': [], 'o2ib': ['enp6s0', 'enp7s0'], 'tcp': ['enp5s0']}
    """
    try:
        data = yaml.safe_load(show_output)
    except yaml.YAMLError as e:
        raise LNetParseError("Failed to parse LNet network output") from e

    if data is None:
        raise LNetParseError("Empty LNet network output")

    try:
        parsed = _NetShowOutput.model_validate(data)
    except ValidationError as e:
        raise LNetParseError(f"Failed to validate LNet network output: {data}") from e

    if not parsed.net:
        # TODO: Confirm if this should be an error. Empty "net" indicates no networks configured,
        # not even "lo". Valid or is `lnetctl` output malformed?
        _logger.warning("No LNet networks found in output: %s", data)
        return {}

    networks: dict[str, list[str]] = {}
    for net in parsed.net:
        interfaces: list[str] = []
        for ni in net.local_nis:
            interfaces.extend(ni.interfaces.values())
        networks[net.net_type] = interfaces

    return networks


def _remove_interfaces(net: str, interfaces: set[str]) -> None:
    """Remove interfaces from an existing LNet network.

    Args:
        net: The LNet network name (e.g. ``tcp0``).
        interfaces: The interfaces to remove.

    Raises:
        LNetRemoveInterfaceError: If removing the interfaces fails.
    """
    cmd = [LNETCTL_EXECUTABLE, "interface", "del", "--net", net]
    for iface in interfaces:
        cmd += ["--if", iface]

    try:
        subprocess.run(cmd, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        raise LNetRemoveInterfaceError(
            f"Failed to remove interfaces {interfaces} from LNet network {net}"
        ) from e
