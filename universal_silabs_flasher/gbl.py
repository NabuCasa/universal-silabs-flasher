from __future__ import annotations

import dataclasses
import json
import logging
import typing

from zigpy.ota.validators import parse_silabs_gbl

from .common import Version
from .const import FirmwareImageType
from .cpc_types import enum32

_LOGGER = logging.getLogger(__name__)

NABUCASA_METADATA_VERSION = 1


class TagId(enum32):
    # First tag in the file. The header tag contains the version number of the GBL file
    # specification, and flags indicating the type of GBL file – whether it is signed
    # or encrypted.
    HEADER = 0x03A617EB
    # Information about the application update image that is contained in this GBL
    # file.
    APP_INFO = 0xF40A0AF4
    # A complete encrypted Secure Element update image. Only applicable on Series 2
    # devices.
    SE_UPGRADE = 0x5EA617EB
    # A complete bootloader update image.
    BOOTLOADER = 0xF50909F5
    # Information about what application data to program at a specific address into the
    # main flash memory. The two tags are interchangeable.
    PROGRAM_DATA1 = 0xFE0101FE
    PROGRAM_DATA2 = 0xFD0303FD
    # LZ4 compressed information about what application data to program at a specific
    # address into the main flash memory.
    PROGRAM_DATA_LZ4 = 0xFD0505FD
    # LZMA compressed information about what application data to program at a specific
    # address into the main flash memory.
    PROGRAM_DATA_LZMA = 0xFD0707FD
    # Metadata that the bootloader does not parse, but can be returned to the
    # application through a callback.
    METADATA = 0xF60808F6
    # The ECDSA-P256 signature of all preceding data in the file.
    SIGNATURE = 0xF70A0AF7
    # End of the GBL file. It contains a 32-bit CRC for the entire file as an integrity
    # check. The CRC is a non-cryptographic check. This must be the last tag.
    END = 0xFC0404FC


@dataclasses.dataclass(frozen=True)
class NabuCasaMetadata:
    metadata_version: int

    sdk_version: Version | None
    ezsp_version: Version | None
    ot_rcp_version: Version | None

    fw_type: FirmwareImageType | None
    baudrate: int | None

    original_json: dict[str, typing.Any] = dataclasses.field(repr=False)

    def get_public_version(self) -> Version | None:
        return self.ezsp_version or self.ot_rcp_version or self.sdk_version

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

        if fw_type := obj.pop("fw_type", None):
            fw_type = FirmwareImageType(fw_type)

        baudrate = obj.pop("baudrate", None)

        if obj:
            _LOGGER.warning("Unexpected keys in JSON remain: %r", obj)

        return cls(
            metadata_version=metadata_version,
            sdk_version=sdk_version,
            ezsp_version=ezsp_version,
            ot_rcp_version=ot_rcp_version,
            fw_type=fw_type,
            baudrate=baudrate,
            original_json=original_json,
        )


@dataclasses.dataclass(frozen=True)
class GBLImage:
    tags: list[tuple[TagId, bytes]]

    @classmethod
    def from_bytes(cls, data: bytes) -> GBLImage:
        if isinstance(data, memoryview):
            data = data.tobytes()

        tags = []

        for tag_bytes, value in parse_silabs_gbl(data):
            tag, _ = TagId.deserialize(tag_bytes)
            tags.append((tag, value))

        return cls(tags=tags)

    def serialize(self) -> bytes:
        return b"".join(
            [
                tag_id.serialize() + len(value).to_bytes(4, "little") + value
                for tag_id, value in self.tags
            ]
        )

    def get_first_tag(self, tag_id: TagId) -> bytes:
        try:
            return next(v for t, v in self.tags if t == tag_id)
        except StopIteration:
            raise KeyError(f"No tag with id {tag_id!r} exists")

    def get_nabucasa_metadata(self) -> NabuCasaMetadata:
        metadata = self.get_first_tag(TagId.METADATA)

        return NabuCasaMetadata.from_json(json.loads(metadata))
