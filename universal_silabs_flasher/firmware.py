from __future__ import annotations

import dataclasses
import json
import logging
import typing

from pygbl import FirmwareImage, GBL3Image

from .common import Version
from .const import LEGACY_FIRMWARE_TYPE_REMAPPING, FirmwareImageType

_LOGGER = logging.getLogger(__name__)

NABUCASA_METADATA_VERSION = 2


@dataclasses.dataclass(frozen=True)
class NabuCasaMetadata:
    metadata_version: int

    sdk_version: Version | None = None
    ezsp_version: Version | None = None
    ot_rcp_version: Version | None = None
    cpc_version: Version | None = None
    zwave_version: Version | None = None

    fw_type: FirmwareImageType | None = None
    fw_variant: str | None = None
    baudrate: int | None = None

    original_json: dict[str, typing.Any] = dataclasses.field(
        repr=False, default_factory=dict
    )

    def get_public_version(self) -> Version | None:
        return (
            self.cpc_version
            or self.ezsp_version
            or self.ot_rcp_version
            or self.zwave_version
            or self.sdk_version
        )

    @classmethod
    def from_json(cls, obj: dict[str, typing.Any]) -> NabuCasaMetadata:
        original_json = json.loads(json.dumps(obj))
        metadata_version = obj.pop("metadata_version")

        if metadata_version > NABUCASA_METADATA_VERSION:
            raise ValueError(
                f"Unknown metadata version: {metadata_version},"
                f" expected {NABUCASA_METADATA_VERSION}"
            )

        if sdk_version := obj.pop("sdk_version", None):
            sdk_version = Version(sdk_version)

        if ezsp_version := obj.pop("ezsp_version", None):
            ezsp_version = Version(ezsp_version)

        if ot_rcp_version := obj.pop("ot_rcp_version", None):
            ot_rcp_version = Version(ot_rcp_version)

        if cpc_version := obj.pop("cpc_version", None):
            cpc_version = Version(cpc_version)

        if zwave_version := obj.pop("zwave_version", None):
            zwave_version = Version(zwave_version)

        if fw_type := obj.pop("fw_type", None):
            if fw_type in LEGACY_FIRMWARE_TYPE_REMAPPING:
                fw_type = LEGACY_FIRMWARE_TYPE_REMAPPING[fw_type]

            try:
                fw_type = FirmwareImageType(fw_type)
            except ValueError:
                _LOGGER.warning("Unknown firmware type: %r", fw_type)
                fw_type = None

        if fw_variant := obj.pop("fw_variant", None):
            fw_variant = fw_variant

        baudrate = obj.pop("baudrate", None)

        if obj:
            _LOGGER.warning("Unexpected keys in JSON remain: %r", obj)

        return cls(
            metadata_version=metadata_version,
            sdk_version=sdk_version,
            ezsp_version=ezsp_version,
            ot_rcp_version=ot_rcp_version,
            cpc_version=cpc_version,
            zwave_version=zwave_version,
            fw_type=fw_type,
            fw_variant=fw_variant,
            baudrate=baudrate,
            original_json=original_json,
        )


def get_nabucasa_metadata(image: FirmwareImage) -> NabuCasaMetadata:
    """Read the Nabu Casa metadata tag from a firmware image."""
    if not isinstance(image, GBL3Image):
        raise KeyError(f"Metadata is not supported for {type(image).__name__}")

    metadata = image.get_metadata()

    if metadata is None:
        raise KeyError("Image contains no metadata tag")

    return NabuCasaMetadata.from_json(json.loads(metadata))
