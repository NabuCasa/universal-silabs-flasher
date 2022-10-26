from __future__ import annotations

import enum
import json
import typing
import dataclasses

from awesomeversion import AwesomeVersion
from zigpy.ota.validators import parse_silabs_gbl

from .cpc_types import enum32

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


class FirmwareImageType(enum.Enum):
    # EmberZNet Zigbee firmware
    NCP_UART_HW = "ncp-uart-hw"

    # Multi-PAN RCP Multiprotocol (via zigbeed)
    RCP_UART_802154 = "rcp-uart-802154"

    # Zigbee NCP + OpenThread RCP
    ZIGBEE_NCP_RCP_UART_802154 = "zigbee-ncp-rcp-uart-802154"


@dataclasses.dataclass(frozen=True)
class NabuCasaMetadata:
    metadata_version: int
    sdk_version: AwesomeVersion | None
    ezsp_version: AwesomeVersion | None
    image_type: FirmwareImageType | None

    def get_public_version(self) -> AwesomeVersion | None:
        return self.ezsp_version or self.sdk_version

    @classmethod
    def from_json(cls, obj: dict[str, typing.Any]) -> NabuCasaMetadata:
        metadata_version = obj["metadata_version"]

        if metadata_version > NABUCASA_METADATA_VERSION:
            raise ValueError(
                f"Unknown metadata version: {metadata_version},"
                f" expected {NABUCASA_METADATA_VERSION}"
            )

        if sdk_version := obj.get("sdk_version"):
            sdk_version = AwesomeVersion(obj["sdk_version"])

        if ezsp_version := obj.get("ezsp_version"):
            ezsp_version = AwesomeVersion(obj["ezsp_version"])

        if image_type := obj.get("image_type"):
            image_type = FirmwareImageType(obj["image_type"])

        return cls(
            metadata_version=metadata_version,
            sdk_version=sdk_version,
            ezsp_version=ezsp_version,
            image_type=image_type,
        )


@dataclasses.dataclass(frozen=True)
class GBLImage:
    tags: list[tuple[TagId, bytes]]

    @classmethod
    def from_bytes(cls, data: bytes) -> GBLImage:
        tags = []

        for tag_bytes, value in parse_silabs_gbl(data):
            tag, _ = TagId.deserialize(tag_bytes)
            tags.append((tag, value))

        return cls(tags=tags)

    def get_first_tag(self, tag_id: TagId) -> bytes:
        try:
            return next(v for t, v in self.tags if t == tag_id)
        except StopIteration:
            raise KeyError(f"No tag with id {tag_id!r} exists")

    def get_nabucasa_metadata(self) -> NabuCasaMetadata:
        metadata = self.get_first_tag(TagId.METADATA)

        return NabuCasaMetadata.from_json(json.loads(metadata.decode("utf-8")))
