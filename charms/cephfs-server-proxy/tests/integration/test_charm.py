#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
import logging
from pathlib import Path

import juju
import pytest
import tenacity
import yaml
from helpers import bootstrap_microceph
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

BASES = ["ubuntu@22.04"]
CLIENT = "filesystem-client"
METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())
PROXY = METADATA["name"]


@pytest.mark.abort_on_fail
@pytest.mark.skip_if_deployed
@pytest.mark.parametrize("base", BASES)
@pytest.mark.order(1)
async def test_build_and_deploy(ops_test: OpsTest, base: str) -> None:
    """Test that the CephFS server can stabilize against filesystem-client."""
    cephfs_info = bootstrap_microceph()

    proxy_charm = str(await ops_test.build_charm("."))
    logger.info(f"Deploying {PROXY} against {CLIENT} and {base}")

    # Deploy the charm and wait for active/idle status
    await asyncio.gather(
        ops_test.model.deploy(
            proxy_charm,
            application_name=PROXY,
            config={
                "fsid": cephfs_info.fsid,
                "sharepoint": f"{cephfs_info.name}:{cephfs_info.path}",
                "monitor-hosts": " ".join(cephfs_info.monitor_hosts),
                "auth-info": f"{cephfs_info.user}:{cephfs_info.key}",
            },
            num_units=1,
            base=base,
        ),
        ops_test.model.deploy(
            "ubuntu",
            application_name="ubuntu",
            channel="edge",
            num_units=1,
            base=base,
            constraints=juju.constraints.parse("virt-type=virtual-machine"),
        ),
        ops_test.model.deploy(
            CLIENT,
            application_name=CLIENT,
            channel="edge",
            config={"mountpoint": "/data"},
            num_units=0,
        ),
    )

    # Set integrations for charmed applications.
    await ops_test.model.integrate(f"{CLIENT}:juju-info", "ubuntu:juju-info")
    await ops_test.model.integrate(f"{CLIENT}:filesystem", f"{PROXY}:filesystem")

    # Reduce the update status frequency to accelerate the triggering of deferred events.
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(apps=[PROXY, CLIENT], status="active", timeout=1000)
        assert ops_test.model.applications[PROXY].units[0].workload_status == "active"


@pytest.mark.abort_on_fail
@pytest.mark.order(2)
@tenacity.retry(
    wait=tenacity.wait.wait_exponential(multiplier=2, min=1, max=30),
    stop=tenacity.stop_after_attempt(3),
    reraise=True,
)
async def test_share_active(ops_test: OpsTest) -> None:
    """Test that the share is mounted on the client charm."""
    base_unit = ops_test.model.applications[CLIENT].units[0]
    result = (await base_unit.ssh("ls /data")).strip("\n")
    assert "test-1" in result
    assert "test-2" in result
    assert "test-3" in result


@pytest.mark.abort_on_fail
@pytest.mark.order(3)
async def test_reintegrate(ops_test: OpsTest) -> None:
    """Test that the client can reintegrate with the server."""
    await ops_test.model.applications[CLIENT].destroy_relation(
        f"{CLIENT}:filesystem", f"{PROXY}:filesystem", block_until_done=True
    )

    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[PROXY], status="active", raise_on_blocked=True, timeout=1000
        )
        await ops_test.model.wait_for_idle(
            apps=[CLIENT], status="blocked", raise_on_error=True, timeout=1000
        )

    await ops_test.model.integrate(f"{CLIENT}:filesystem", f"{PROXY}:filesystem")
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[PROXY, CLIENT], status="active", raise_on_error=True, timeout=360
        )

    base_unit = ops_test.model.applications[CLIENT].units[0]
    result = (await base_unit.ssh("ls /data")).strip("\n")
    assert "test-1" in result
    assert "test-2" in result
    assert "test-3" in result
