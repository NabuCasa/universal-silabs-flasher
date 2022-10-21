from __future__ import annotations

import sys
import asyncio
import logging

import zigpy.serial

from .cpc import CPCProtocol
from .gecko_bootloader import GeckoBootloaderOption, GeckoBootloaderProtocol

_LOGGER = logging.getLogger(__name__)


async def probe_cpc() -> str:
    loop = asyncio.get_running_loop()

    _, protocol = await zigpy.serial.create_serial_connection(
        loop=loop,
        protocol_factory=CPCProtocol,
        url=sys.argv[1],
        baudrate=115_200,
    )

    await protocol.wait_until_connected()
    version = await protocol.probe()

    return version


async def probe_bootloader() -> str:
    loop = asyncio.get_running_loop()

    _, protocol = await zigpy.serial.create_serial_connection(
        loop=loop,
        protocol_factory=GeckoBootloaderProtocol,
        url=sys.argv[1],
        baudrate=115_200,
    )

    await protocol.wait_until_connected()
    bootloader_version = await protocol.probe()
    await protocol.choose_option(GeckoBootloaderOption.RUN_FIRMWARE)

    return bootloader_version


if __name__ == "__main__":
    import coloredlogs

    coloredlogs.install(level="DEBUG")

    # asyncio.run(probe_bootloader())
    asyncio.run(probe_cpc())
