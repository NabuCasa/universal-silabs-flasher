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
}


DEFAULT_BAUDRATES = {
    ApplicationType.GECKO_BOOTLOADER: [115200],
    ApplicationType.CPC: [460800, 115200, 230400],
    ApplicationType.EZSP: [115200],
    ApplicationType.SPINEL: [460800],
}
