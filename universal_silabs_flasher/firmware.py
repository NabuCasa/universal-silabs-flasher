from __future__ import annotations

import dataclasses
import json
import logging
import typing

from zigpy.ota.validators import ValidationError, parse_silabs_ebl, parse_silabs_gbl
import zigpy.types as zigpy_t

from .common import Version, pad_to_multiple
from .const import LEGACY_FIRMWARE_TYPE_REMAPPING, FirmwareImageType

_LOGGER = logging.getLogger(__name__)

NABUCASA_METADATA_VERSION = 2


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


@dataclasses.dataclass(frozen=True)
class NabuCasaMetadata:
    metadata_version: int

    sdk_version: Version | None
    ezsp_version: Version | None
    ot_rcp_version: Version | None
    cpc_version: Version | None

    fw_type: FirmwareImageType | None
    fw_variant: str | None
    baudrate: int | None

    original_json: dict[str, typing.Any] = dataclasses.field(repr=False)

    def get_public_version(self) -> Version | None:
        return (
            self.cpc_version
            or self.ezsp_version
            or self.ot_rcp_version
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
            fw_type=fw_type,
            fw_variant=fw_variant,
            baudrate=baudrate,
            original_json=original_json,
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
        if isinstance(data, memoryview):
            data = data.tobytes()

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
            b"\xff",
        )

    def get_nabucasa_metadata(self) -> NabuCasaMetadata:
        metadata = self.get_first_tag(GBLTagId.METADATA)

        return NabuCasaMetadata.from_json(json.loads(metadata))


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
            b"\xff",
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
