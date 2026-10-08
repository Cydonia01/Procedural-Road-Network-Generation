"""Validate an .xodr file against the OpenDRIVE XSD.

The XSD is not bundled (ASAM license). Point OPENDRIVE_XSD at the main schema
file, e.g. opendrive_16_core.xsd, or pass xsd_path explicitly.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional, Union

from lxml import etree

log = logging.getLogger(__name__)

XSD_ENV_VAR = "OPENDRIVE_XSD"

PathLike = Union[str, Path]


def validate_schema(
    xodr_path: PathLike, xsd_path: Optional[PathLike] = None
) -> Optional[list[str]]:
    """Return schema errors (empty list if valid), or None if no XSD is configured."""
    xsd_path = xsd_path or os.environ.get(XSD_ENV_VAR)
    if not xsd_path:
        log.warning("no OpenDRIVE XSD configured (set %s); skipping schema check", XSD_ENV_VAR)
        return None
    schema = etree.XMLSchema(etree.parse(str(xsd_path)))
    doc = etree.parse(str(xodr_path))
    if schema.validate(doc):
        return []
    return [f"line {e.line}: {e.message}" for e in schema.error_log]
