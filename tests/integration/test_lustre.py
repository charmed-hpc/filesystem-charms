import logging
from pathlib import Path

import jubilant
import pytest
from conftest import Machines
from constants import (
    FILESYSTEM_CLIENT,
    LUSTRE_SERVER_PROXY,
    MOUNT_PROVIDER,
    MOUNT_REQUIRERS,
)
from helpers import bootstrap_lustre_server, charm_channel, check_files

logger = logging.getLogger(__name__)
pytestmark = pytest.mark.lustre


@pytest.mark.order(20)
def test_deploy_lustre(
    juju: jubilant.Juju, base: str, lustre_server_proxy: str | Path, machines: Machines
) -> None:
    """Setup the filesystem charms to mount Lustre filesystems.

    Assert on the unit status before any relations/configurations take place.
    """
    logger.info(f"Deploying {LUSTRE_SERVER_PROXY}")

    # Deploy the Lustre server proxy onto the storage machine.
    juju.deploy(
        str(lustre_server_proxy),
        LUSTRE_SERVER_PROXY,
        base=base,
        channel=charm_channel(lustre_server_proxy),
        to=machines.storage_machine_id,
    )

    # Setup the filesystem-client charms to support Lustre
    juju.config(
        FILESYSTEM_CLIENT,
        values={
            "enable-lustre": "true",
        },
    )
    juju.config(MOUNT_PROVIDER, values={"enable-lustre": "true"})

    # Bootstrap the Lustre server.
    lustre_info = bootstrap_lustre_server(juju, machines.storage_machine_id)

    juju.config(
        LUSTRE_SERVER_PROXY,
        values={"mgs-nids": " ".join(lustre_info.mgs_ids), "fs-name": lustre_info.fs_name},
    )

    # Wait for the proxy charm to become active.
    juju.wait(
        lambda status: jubilant.all_active(status, LUSTRE_SERVER_PROXY),
        error=lambda status: jubilant.any_error(status, LUSTRE_SERVER_PROXY),
    )


@pytest.mark.order(21)
def test_lustre(juju: jubilant.Juju) -> None:
    # Reconfigure and integrate with Lustre.
    juju.config(
        FILESYSTEM_CLIENT,
        values={
            "mountpoint": "/lustre",
            "noexec": "false",
            "nosuid": "false",
            "nodev": "true",
        },
    )
    juju.integrate(f"{FILESYSTEM_CLIENT}:filesystem", f"{LUSTRE_SERVER_PROXY}:filesystem")
    juju.integrate(f"{MOUNT_PROVIDER}:filesystem", f"{LUSTRE_SERVER_PROXY}:filesystem")
    juju.wait(
        lambda status: jubilant.all_active(status, FILESYSTEM_CLIENT, MOUNT_PROVIDER),
        error=lambda status: (
            jubilant.any_error(status, FILESYSTEM_CLIENT, MOUNT_PROVIDER)
            or (
                jubilant.all_agents_idle(status)
                and jubilant.any_blocked(status, FILESYSTEM_CLIENT, MOUNT_PROVIDER)
            )
        ),
    )

    check_files(juju, "ubuntu/0", "/lustre")
    for app in MOUNT_REQUIRERS:
        check_files(juju, f"{app}/0", f"/{app}")

    juju.remove_relation(f"{FILESYSTEM_CLIENT}:filesystem", f"{LUSTRE_SERVER_PROXY}:filesystem")
    juju.remove_relation(f"{MOUNT_PROVIDER}:filesystem", f"{LUSTRE_SERVER_PROXY}:filesystem")

    juju.wait(
        lambda status: jubilant.all_blocked(status, FILESYSTEM_CLIENT, MOUNT_PROVIDER),
        error=lambda status: jubilant.any_error(status, FILESYSTEM_CLIENT, MOUNT_PROVIDER),
    )
