from __future__ import annotations

import dataclasses
import enum


class FirmwareImageType(enum.Enum):
    ZIGBEE_NCP = "zigbee_ncp"
    ZIGBEE_ROUTER = "zigbee_router"
    OPENTHREAD_RCP = "openthread_rcp"
    ZWAVE_NCP = "zwave_ncp"
    BOOTLOADER = "bootloader"
    MULTIPAN = "multipan"

    UNKNOWN = "unknown"


LEGACY_FIRMWARE_TYPE_REMAPPING = {
    "ncp-uart-hw": FirmwareImageType.ZIGBEE_NCP,
    "ncp-uart-sw": FirmwareImageType.ZIGBEE_NCP,
    "rcp-uart-802154": FirmwareImageType.MULTIPAN,
    "ot-rcp": FirmwareImageType.OPENTHREAD_RCP,
    "z-wave": FirmwareImageType.ZWAVE_NCP,
    "gecko-bootloader": FirmwareImageType.BOOTLOADER,
}


class ApplicationType(enum.Enum):
    GECKO_BOOTLOADER = "bootloader"
    CPC = "cpc"
    EZSP = "ezsp"
    SPINEL = "spinel"
    ROUTER = "router"


FW_IMAGE_TYPE_TO_APPLICATION_TYPE = {
    FirmwareImageType.ZIGBEE_NCP: ApplicationType.EZSP,
    FirmwareImageType.MULTIPAN: ApplicationType.CPC,
    FirmwareImageType.OPENTHREAD_RCP: ApplicationType.SPINEL,
    FirmwareImageType.BOOTLOADER: ApplicationType.GECKO_BOOTLOADER,
    FirmwareImageType.ZIGBEE_ROUTER: ApplicationType.ROUTER,
}


DEFAULT_BAUDRATES = {
    ApplicationType.GECKO_BOOTLOADER: [115200],
    ApplicationType.EZSP: [115200, 460800],
    ApplicationType.SPINEL: [460800],
    ApplicationType.ROUTER: [115200],
    ApplicationType.CPC: [460800, 115200, 230400],
}


class ResetTarget(enum.Enum):
    YELLOW = "yellow"
    IHOST = "ihost"
    SLZB07 = "slzb07"
    RTS_DTR = "rts_dtr"
    BAUDRATE = "baudrate"


@dataclasses.dataclass
class GpioPattern:
    pins: dict[str | int, bool]
    delay_after: float


@dataclasses.dataclass
class GpioResetConfig:
    chip: str | None
    chip_type: str | None
    pattern: list[GpioPattern]


@dataclasses.dataclass
class BaudrateResetConfig:
    baudrates: list[int]
    delay_after_each: float
    delay_after_final: float
    command: bytes | None = None


# fmt: off
RESET_CONFIGS = {
    ResetTarget.YELLOW: GpioResetConfig(
        chip="/dev/gpiochip0",
        chip_type=None,
        pattern=[
            GpioPattern(pins={24: True,  25: True},  delay_after=0.1),
            GpioPattern(pins={24: False, 25: False}, delay_after=0.1),
            GpioPattern(pins={24: False, 25: True},  delay_after=0.1),
            GpioPattern(pins={24: True,  25: True},  delay_after=0.0),
        ],
    ),
    ResetTarget.IHOST: GpioResetConfig(
        chip="/dev/gpiochip1",
        chip_type=None,
        pattern=[
            GpioPattern(pins={26: True,  27: True},  delay_after=0.1),
            GpioPattern(pins={26: False, 27: False}, delay_after=0.1),
            GpioPattern(pins={26: True,  27: False}, delay_after=0.1),
            GpioPattern(pins={26: True,  27: True},  delay_after=0.0),
        ]
    ),
    ResetTarget.SLZB07: GpioResetConfig(
        chip=None,
        chip_type="cp210x",
        pattern=[
            GpioPattern(pins={4: True,  5: True},  delay_after=0.1),
            GpioPattern(pins={4: False, 5: False}, delay_after=0.1),
            GpioPattern(pins={4: True,  5: False}, delay_after=0.1),
            GpioPattern(pins={4: True,  5: True},  delay_after=0.0),
        ]
    ),
    ResetTarget.RTS_DTR: GpioResetConfig(
        chip=None,
        chip_type="uart",
        pattern=[
            GpioPattern(pins={"dtr": False, "rts": True},  delay_after=0.1),
            GpioPattern(pins={"dtr": True,  "rts": False}, delay_after=0.5),
            GpioPattern(pins={"dtr": False, "rts": False}, delay_after=0.0),
        ]
    ),
    ResetTarget.BAUDRATE: BaudrateResetConfig(
        baudrates=[150, 300, 1200],
        delay_after_each=0.1,
        delay_after_final=0.5,
        command=b"BZ",
    ),
}
# fmt: on
