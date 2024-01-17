import enum


class FirmwareImageType(enum.Enum):
    # EmberZNet Zigbee firmware
    NCP_UART_HW = "ncp-uart-hw"

    # Multi-PAN RCP Multiprotocol (via zigbeed)
    RCP_UART_802154 = "rcp-uart-802154"

    # Zigbee NCP + OpenThread RCP
    ZIGBEE_NCP_RCP_UART_802154 = "zigbee-ncp-rcp-uart-802154"

    # OpenThread RCP
    OT_RCP = "ot-rcp"

    # Z-Wave
    Z_WAVE = "z-wave"

    # Gecko Bootloader
    GECKO_BOOTLOADER = "gecko-bootloader"


class ApplicationType(enum.Enum):
    GECKO_BOOTLOADER = "bootloader"
    CPC = "cpc"
    EZSP = "ezsp"
    SPINEL = "spinel"


FW_IMAGE_TYPE_TO_APPLICATION_TYPE = {
    FirmwareImageType.NCP_UART_HW: ApplicationType.EZSP,
    FirmwareImageType.RCP_UART_802154: ApplicationType.CPC,
    FirmwareImageType.ZIGBEE_NCP_RCP_UART_802154: ApplicationType.CPC,
    FirmwareImageType.OT_RCP: ApplicationType.SPINEL,
    FirmwareImageType.GECKO_BOOTLOADER: ApplicationType.GECKO_BOOTLOADER,
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
}
