from __future__ import annotations

import enum
import math
import asyncio
import logging
import os.path
import functools

import click
import coloredlogs
import bellows.ezsp
import async_timeout
import bellows.types
import bellows.config
from awesomeversion import AwesomeVersion

from .cpc import CPCProtocol
from .gbl import GBLImage, FirmwareImageType
from .common import PROBE_TIMEOUT, connect_protocol, patch_pyserial_asyncio
from .emberznet import connect_ezsp
from .xmodemcrc import BLOCK_SIZE as XMODEM_BLOCK_SIZE
from .gecko_bootloader import GeckoBootloaderProtocol

patch_pyserial_asyncio()

_LOGGER = logging.getLogger(__name__)
LOG_LEVELS = ["WARNING", "INFO", "DEBUG"]


def click_coroutine(f):
    @functools.wraps(f)
    def inner(*args, **kwargs):
        return asyncio.run(f(*args, **kwargs))

    return inner


class CommunicationMethod(enum.Enum):
    GECKO_BOOTLOADER = "bootloader"
    CPC = "cpc"
    EZSP = "ezsp"


@click.group()
@click.option("-v", "--verbose", count=True)
@click.option("--device", required=True)
@click.option("--baudrate", default=115200, show_default=True)
@click.option("--bootloader-baudrate", default=None, show_default=True)
@click.pass_context
def main(ctx, verbose, device, baudrate, bootloader_baudrate):
    coloredlogs.install(level=LOG_LEVELS[min(len(LOG_LEVELS) - 1, verbose)])

    ctx.obj = {
        "device": device,
        "baudrate": baudrate,
        "bootloader_baudrate": bootloader_baudrate or baudrate,
    }


async def _enter_bootloader(ctx):
    # Try connecting with the bootloader first
    try:
        async with connect_protocol(
            ctx.obj["device"], ctx.obj["bootloader_baudrate"], GeckoBootloaderProtocol
        ) as gecko:
            try:
                await gecko.probe()
            except asyncio.TimeoutError as e:
                _LOGGER.info("Failed to probe Gecko Bootloader: %r", e)
            else:
                return
    except asyncio.TimeoutError as e:
        _LOGGER.info("Failed to probe bootloader: %r", e)

    # Next, try EZSP
    try:
        async with connect_ezsp(ctx.obj["device"], ctx.obj["baudrate"]) as ezsp:
            try:
                res = await ezsp.launchStandaloneBootloader(0x01)
            except asyncio.TimeoutError:
                return
            else:
                if res[0] != bellows.types.EmberStatus.SUCCESS:
                    click.echo(f"Failed to enter bootloader via EZSP: {res[0]}")
    except asyncio.TimeoutError as e:
        _LOGGER.info("Failed to probe EZSP: %r", e)

    # Finally, try CPC
    async with connect_protocol(
        ctx.obj["device"], ctx.obj["baudrate"], CPCProtocol
    ) as cpc:
        async with async_timeout.timeout(PROBE_TIMEOUT):
            await cpc.enter_bootloader()


@main.command()
@click.pass_context
@click_coroutine
async def bootloader(ctx):
    await _enter_bootloader(ctx)

    # Make sure we are in the bootloader
    async with connect_protocol(
        ctx.obj["device"], ctx.obj["bootloader_baudrate"], GeckoBootloaderProtocol
    ) as gecko:
        async with async_timeout.timeout(PROBE_TIMEOUT):
            await gecko.probe()


@main.command()
@click.option("--firmware", type=click.File("rb"), required=True, show_default=True)
@click.option("--force", is_flag=True, default=False, show_default=True)
@click.option("--allow-downgrades", is_flag=True, default=False, show_default=True)
@click.option("--allow-cross-flashing", is_flag=True, default=False, show_default=True)
@click.option(
    "--skip-if-version-matches", is_flag=True, default=True, show_default=True
)
@click.pass_context
@click_coroutine
async def flash(
    ctx,
    firmware,
    force,
    allow_downgrades,
    allow_cross_flashing,
    skip_if_version_matches,
):
    # Parse and validate the firmware image
    firmware_data = firmware.read()
    gbl_image = GBLImage.from_bytes(firmware_data)
    firmware.close()

    # Pad the image to the XMODEM block size
    if len(firmware_data) % XMODEM_BLOCK_SIZE != 0:
        padding_count = math.ceil(
            len(firmware_data) / XMODEM_BLOCK_SIZE
        ) * XMODEM_BLOCK_SIZE - len(firmware_data)
        firmware_data += b"\xFF" * padding_count

    try:
        metadata = gbl_image.get_nabucasa_metadata()
    except KeyError:
        metadata = None

    _LOGGER.debug("Extracted GBL metadata: %s", metadata)

    app_version, application_type = await _get_application_version(ctx)
    assert application_type != CommunicationMethod.GECKO_BOOTLOADER

    if application_type == CommunicationMethod.EZSP:
        running_image_type = FirmwareImageType.NCP_UART_HW
    else:
        # TODO: how do you distinguish RCP_UART_802154 from ZIGBEE_NCP_RCP_UART_802154
        running_image_type = FirmwareImageType.ZIGBEE_NCP_RCP_UART_802154

    if (
        not force
        and metadata is not None
        and metadata.image_type is not None
        and metadata.image_type != running_image_type
        and not allow_cross_flashing
    ):
        raise click.ClickException(
            f"Running image type {running_image_type}"
            f" does not match firmware image type {metadata.image_type}"
        )

    if (
        not force
        and metadata is not None
        and app_version == metadata.get_public_version()
        and skip_if_version_matches
    ):
        click.echo(f"Firmware version {app_version} is already running, not upgrading")
        return

    if (
        not force
        and metadata is not None
        and app_version > metadata.get_public_version()
        and not allow_downgrades
    ):
        raise click.ClickException(
            f"Firmware version {metadata.get_public_version()} does not upgrade"
            f" current version {app_version}"
        )

    await _enter_bootloader(ctx)

    # Flash the image
    async with connect_protocol(
        ctx.obj["device"], ctx.obj["bootloader_baudrate"], GeckoBootloaderProtocol
    ) as gecko:
        await gecko.probe()

        with click.progressbar(
            label=os.path.basename(firmware.name),
            length=len(firmware_data),
            show_eta=True,
            show_percent=True,
        ) as pbar:
            await gecko.upload_firmware(
                firmware_data,
                progress_callback=lambda current, _: pbar.update(XMODEM_BLOCK_SIZE),
            )

        await gecko.run_firmware()


async def _get_application_version(ctx) -> tuple[AwesomeVersion, CommunicationMethod]:
    # If we are in the bootloader, start the application
    async with connect_protocol(
        ctx.obj["device"], ctx.obj["bootloader_baudrate"], GeckoBootloaderProtocol
    ) as gecko:
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
        async with connect_ezsp(ctx.obj["device"], ctx.obj["baudrate"]) as ezsp:
            brd_manuf, brd_name, version = await ezsp.get_board_info()
            return AwesomeVersion(version), CommunicationMethod.EZSP
    except asyncio.TimeoutError as e:
        _LOGGER.debug("Failed to probe EZSP: %r", e)

    # Finally, try CPC
    async with connect_protocol(
        ctx.obj["device"], ctx.obj["baudrate"], CPCProtocol
    ) as cpc:
        try:
            async with async_timeout.timeout(PROBE_TIMEOUT):
                await cpc.probe()
        except asyncio.TimeoutError:
            raise RuntimeError("Failed to probe CPC")

        try:
            # Now that we have connected, probe the application version
            version = await cpc.get_cpc_version()
        except asyncio.TimeoutError as e:
            _LOGGER.debug("Failed to probe CPC: %r", e)
        else:
            return version, CommunicationMethod.CPC

    raise RuntimeError("Could not probe protocol version")


async def enter_bootloader(port, baudrate, protocol_cls):
    if protocol_cls == GeckoBootloaderProtocol:
        return
    elif protocol_cls == CPCProtocol:
        async with connect_protocol(port, baudrate, CPCProtocol) as cpc:
            await cpc.probe()
            await cpc.enter_bootloader()
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
