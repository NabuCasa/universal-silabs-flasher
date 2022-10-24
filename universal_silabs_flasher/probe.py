from __future__ import annotations

import sys
import math
import typing
import asyncio
import logging
import pathlib
import contextlib

import tqdm
import bellows.ezsp
import zigpy.serial
import async_timeout
import bellows.types
import bellows.config

from . import cpc_types
from .cpc import CPCProtocol, ResetCommand, PropertyCommand
from .common import validate_silabs_gbl, patch_pyserial_asyncio
from .emberznet import connect_ezsp
from .xmodemcrc import BLOCK_SIZE as XMODEM_BLOCK_SIZE
from .gecko_bootloader import GeckoBootloaderProtocol

patch_pyserial_asyncio()

_LOGGER = logging.getLogger(__name__)

INTER_PROBE_DELAY = 0.5
CONNECT_TIMEOUT = 1
PROBE_TIMEOUT = 2


@contextlib.asynccontextmanager
async def connect_protocol(port, baudrate, factory):
    loop = asyncio.get_running_loop()

    async with async_timeout.timeout(CONNECT_TIMEOUT):
        _, protocol = await zigpy.serial.create_serial_connection(
            loop=loop,
            protocol_factory=factory,
            url=port,
            baudrate=baudrate,
        )
        await protocol.wait_until_connected()

    try:
        yield protocol
    finally:
        protocol.disconnect()


async def get_application_version(
    port, baudrate
) -> tuple[
    str, type[CPCProtocol] | type[GeckoBootloaderProtocol] | typing.Literal["EZSP"]
]:
    # If we are in the bootloader, start the application
    async with connect_protocol(port, baudrate, GeckoBootloaderProtocol) as gecko:
        try:
            async with async_timeout.timeout(PROBE_TIMEOUT):
                await gecko.probe()
        except asyncio.TimeoutError as e:
            _LOGGER.debug("Failed to probe Gecko Bootloader: %r", e)
        else:
            _LOGGER.debug("Starting application from bootloader")
            await gecko.run_firmware()

    # Next, try EZSP
    try:
        async with connect_ezsp(port, baudrate) as ezsp:
            brd_manuf, brd_name, version = await ezsp.get_board_info()
            return f"{brd_manuf}:{brd_name}:{version}", "EZSP"
    except asyncio.TimeoutError as e:
        _LOGGER.debug("Failed to probe EZSP: %r", e)

    # Finally, try CPC
    async with connect_protocol(port, baudrate, CPCProtocol) as cpc:
        try:
            async with async_timeout.timeout(PROBE_TIMEOUT):
                await cpc.probe()
        except asyncio.TimeoutError:
            raise RuntimeError("Failed to probe CPC")

        try:
            # Now that we have connected, probe the application version
            version = await cpc.get_secondary_version()
        except asyncio.TimeoutError as e:
            _LOGGER.debug("Failed to probe CPC: %r", e)
        else:
            return version, CPCProtocol

    raise RuntimeError("Could not probe protocol version")


async def enter_bootloader(port, baudrate, protocol_cls):
    if protocol_cls == GeckoBootloaderProtocol:
        return
    elif protocol_cls == CPCProtocol:
        async with connect_protocol(port, baudrate, CPCProtocol) as cpc:
            await cpc.probe()
            await cpc.send_unnumbered_frame(
                command_id=cpc_types.UnnumberedFrameCommandId.PROP_VALUE_SET,
                command_payload=PropertyCommand(
                    property_id=cpc_types.PropertyId.BOOTLOADER_REBOOT_MODE,
                    value=cpc_types.RebootMode.BOOTLOADER.serialize(),
                ),
            )

            await cpc.send_unnumbered_frame(
                command_id=cpc_types.UnnumberedFrameCommandId.RESET,
                command_payload=ResetCommand(status=None),
            )
    elif protocol_cls == "EZSP":
        async with connect_ezsp(port, baudrate) as ezsp:
            try:
                res = await ezsp.launchStandaloneBootloader(0x01)
            except asyncio.TimeoutError:
                pass
            else:
                if res[0] != bellows.types.EmberStatus.SUCCESS:
                    raise RuntimeError(f"Failed to enter bootloader via EZSP: {res[0]}")
    else:
        raise ValueError(f"Invalid protocol class: {protocol_cls!r}")


async def main():
    port = sys.argv[1]
    baudrate = int(sys.argv[2])
    firmware_path = pathlib.Path(sys.argv[3])

    firmware = firmware_path.read_bytes()

    if len(firmware) % XMODEM_BLOCK_SIZE != 0:
        _LOGGER.warning("Padding image to a multiple of %s bytes", XMODEM_BLOCK_SIZE)

        padding_count = math.ceil(
            len(firmware) / XMODEM_BLOCK_SIZE
        ) * XMODEM_BLOCK_SIZE - len(firmware)
        firmware += b"\xFF" * padding_count

    # Ensure the firmware is a valid GBL file with checksum
    validate_silabs_gbl(firmware)

    version, protocol_cls = await get_application_version(port, baudrate)

    _LOGGER.info("Connected via %s: version %s", protocol_cls, version)

    await enter_bootloader(port, baudrate, protocol_cls)

    _LOGGER.info("Entered bootloader")

    async with connect_protocol(port, baudrate, GeckoBootloaderProtocol) as gecko:
        await gecko.probe()

        with tqdm.tqdm(
            desc=firmware_path.name,
            total=len(firmware),
            unit="B",
            unit_scale=True,
            unit_divisor=1024,
        ) as pbar:
            await gecko.upload_firmware(
                firmware,
                progress_callback=lambda current, _: pbar.update(XMODEM_BLOCK_SIZE),
            )

        await gecko.run_firmware()

    _LOGGER.info("Rebooted application")


if __name__ == "__main__":
    import coloredlogs

    coloredlogs.install(level="INFO")
    asyncio.run(main())
