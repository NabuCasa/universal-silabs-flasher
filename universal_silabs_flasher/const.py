import enum


class FirmwareImageType(enum.Enum):
    ZIGBEE_NCP = "zigbee_ncp"
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


FW_IMAGE_TYPE_TO_APPLICATION_TYPE = {
    FirmwareImageType.ZIGBEE_NCP: ApplicationType.EZSP,
    FirmwareImageType.MULTIPAN: ApplicationType.CPC,
    FirmwareImageType.OPENTHREAD_RCP: ApplicationType.SPINEL,
    FirmwareImageType.BOOTLOADER: ApplicationType.GECKO_BOOTLOADER,
}


DEFAULT_BAUDRATES = {
    ApplicationType.GECKO_BOOTLOADER: [115200],
    ApplicationType.CPC: [460800, 115200, 230400],
    ApplicationType.EZSP: [115200],
    ApplicationType.SPINEL: [460800],
}


class ResetTarget(enum.Enum):
    YELLOW = "yellow"
    IHOST = "ihost"
    SLZB07 = "slzb07"
    SONOFF = "sonoff"


GPIO_CONFIGS = {
    ResetTarget.YELLOW: {
        "chip": "/dev/gpiochip0",
        "pin_states": {
            24: [True, False, False, True],
            25: [True, False, True, True],
        },
        "toggle_delay": 0.1,
    },
    ResetTarget.IHOST: {
        "chip": "/dev/gpiochip1",
        "pin_states": {
            27: [True, False, False, True],
            26: [True, False, True, True],
        },
        "toggle_delay": 0.1,
    },
    ResetTarget.SLZB07: {
        "chip_name": "cp210x",
        "pin_states": {
            5: [True, False, False, True],
            4: [True, False, True, True],
        },
        "toggle_delay": 0.1,
    },
}
