#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Lustre charm configuration model."""

from pydantic import BaseModel


class LustreConfig(BaseModel):
    """Lustre charm configuration model."""

    lnet_networks: str = ""
