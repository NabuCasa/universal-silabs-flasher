from __future__ import annotations

import enum
import json
import typing
import logging
import dataclasses

import zigpy.types as zigpy_t
from awesomeversion import AwesomeVersion
from zigpy.ota.validators import ValidationError, parse_silabs_ebl, parse_silabs_gbl

from .common import pad_to_multiple

_LOGGER = logging.getLogger(__name__)

NABUCASA_METADATA_VERSION = 1


class GBLTagId(zigpy_t.enum32):
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


class EBLTagId(zigpy_t.enum16):
    # TODO: flip the endianness
    HEADER = 0x0000
    PROG = 0x01FE
    MFGPROG = 0xFE02
    ERASEPROG = 0x03FD
    END = 0x04FC
    ENC_HEADER = 0x05FB
    ENC_INIT = 0x06FA
    ENC_EBL_DATA = 0x07F9
    ENC_MAC = 0x09F7


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
    fw_type: FirmwareImageType | None

    def get_public_version(self) -> AwesomeVersion | None:
        return self.ezsp_version or self.sdk_version

    @classmethod
    def from_json(cls, obj: dict[str, typing.Any]) -> NabuCasaMetadata:
        metadata_version = obj.pop("metadata_version")

        if metadata_version > NABUCASA_METADATA_VERSION:
            raise ValueError(
                f"Unknown metadata version: {metadata_version},"
                f" expected {NABUCASA_METADATA_VERSION}"
            )

        if sdk_version := obj.pop("sdk_version", None):
            sdk_version = AwesomeVersion(sdk_version)

        if ezsp_version := obj.pop("ezsp_version", None):
            ezsp_version = AwesomeVersion(ezsp_version)

        if fw_type := obj.pop("fw_type", None):
            fw_type = FirmwareImageType(fw_type)

        if obj:
            _LOGGER.warning("Unexpected keys in JSON remain: %r", obj)

        return cls(
            metadata_version=metadata_version,
            sdk_version=sdk_version,
            ezsp_version=ezsp_version,
            fw_type=fw_type,
        )


@dataclasses.dataclass(frozen=True)
class FirmwareImage:
    tags: list[tuple[GBLTagId, bytes]]

    @classmethod
    def from_bytes(cls, data: bytes) -> FirmwareImage:
        raise NotImplementedError()

    def serialize(self) -> bytes:
        raise NotImplementedError()

    def get_first_tag(self, tag_id: GBLTagId) -> bytes:
        try:
            return next(v for t, v in self.tags if t == tag_id)
        except StopIteration:
            raise KeyError(f"No tag with id {tag_id!r} exists")


@dataclasses.dataclass(frozen=True)
class GBLImage(FirmwareImage):
    @classmethod
    def from_bytes(cls, data: bytes) -> GBLImage:
        tags = []

        for tag_bytes, value in parse_silabs_gbl(data):
            tag, _ = GBLTagId.deserialize(tag_bytes)
            tags.append((tag, value))

        return cls(tags=tags)

    def serialize(self) -> bytes:
        return pad_to_multiple(
            b"".join(
                [
                    tag_id.serialize() + len(value).to_bytes(4, "little") + value
                    for tag_id, value in self.tags
                ]
            ),
            4,
            b"\xFF",
        )

    def get_nabucasa_metadata(self) -> NabuCasaMetadata:
        metadata = self.get_first_tag(GBLTagId.METADATA)

        return NabuCasaMetadata.from_json(json.loads(metadata.decode("utf-8")))


@dataclasses.dataclass(frozen=True)
class EBLImage(FirmwareImage):
    @classmethod
    def from_bytes(cls, data: bytes) -> EBLImage:
        tags = []

        for tag_bytes, value in parse_silabs_ebl(data):
            tag, _ = EBLTagId.deserialize(tag_bytes)
            tags.append((tag, value))

        return cls(tags=tags)

    def serialize(self) -> bytes:
        return pad_to_multiple(
            b"".join(
                [
                    tag_id.serialize() + len(value).to_bytes(2, "big") + value
                    for tag_id, value in self.tags
                ]
            ),
            64,
            b"\xFF",
        )

    def get_nabucasa_metadata(self) -> NabuCasaMetadata:
        raise KeyError("Metadata not supported for EBL")


def parse_firmware_image(data: bytes) -> FirmwareImage:
    for fw_cls in [GBLImage, EBLImage]:
        try:
            return fw_cls.from_bytes(data)
        except ValidationError:
            pass

    raise ValueError("Unknown firmware image type")
