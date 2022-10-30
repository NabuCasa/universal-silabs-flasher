from __future__ import annotations

import os
import enum
import math
import time
import typing
import asyncio
import logging
import os.path
import functools

import click
import gpiozero
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
from .gecko_bootloader import NoFirmwareError, GeckoBootloaderProtocol

patch_pyserial_asyncio()

_LOGGER = logging.getLogger(__name__)
LOG_LEVELS = ["WARNING", "INFO", "DEBUG"]


def click_coroutine(f: typing.Callable) -> typing.Callable:
    @functools.wraps(f)
    def inner(*args: tuple[typing.Any], **kwargs: typing.Any) -> typing.Any:
        return asyncio.run(f(*args, **kwargs))

    return inner


def click_enum_validator_factory(
    enum_cls: type[enum.Enum],
) -> typing.Callable[[click.Context, typing.Any, typing.Any], typing.Any]:
    """Click enum validator factory."""

    def validator_callback(
        ctx: click.Context, param: typing.Any, value: typing.Any
    ) -> typing.Any:
        values = []

        for v in value:
            try:
                values.append(enum_cls(v))
            except ValueError:
                expected = [m.value for m in enum_cls]
                raise click.BadParameter(
                    f"{v!r} is invalid, must be one of: {', '.join(expected)}"
                )

        return values

    return validator_callback


class CommunicationMethod(enum.Enum):
    GECKO_BOOTLOADER = "bootloader"
    CPC = "cpc"
    EZSP = "ezsp"


@click.group()
@click.option("-v", "--verbose", count=True)
@click.option("--device", type=click.Path(exists=True), required=True)
@click.option("--baudrate", default=115200, show_default=True)
@click.option("--bootloader-baudrate", default=None, show_default=True)
@click.option(
    "--probe-method",
    multiple=True,
    default=[m.value for m in CommunicationMethod],
    callback=click_enum_validator_factory(CommunicationMethod),
    show_default=True,
)
@click.pass_context
def main(ctx, verbose, device, baudrate, bootloader_baudrate, probe_method):
    coloredlogs.install(level=LOG_LEVELS[min(len(LOG_LEVELS) - 1, verbose)])

    ctx.obj = {
        "device": device,
        "baudrate": baudrate,
        "bootloader_baudrate": bootloader_baudrate or baudrate,
        "probe_methods": probe_method,
    }


def _enter_yellow_bootloader():
    os.environ.setdefault("GPIOZERO_PIN_FACTORY", "native")

    gpio24 = gpiozero.OutputDevice(pin=24, initial_value=True)
    gpio25 = gpiozero.OutputDevice(pin=25, initial_value=True)

    with gpio24, gpio25:
        gpio24.off()  # Assert Reset
        gpio25.off()  # 0=BL mode, 1=Firmware
        time.sleep(0.1)
        gpio25.on()  # Deassert Reset
        time.sleep(0.1)


async def _enter_bootloader(ctx, *, yellow_gpio_reset: bool = False):

    if yellow_gpio_reset:
        await asyncio.get_running_loop().run_in_executor(None, _enter_yellow_bootloader)

    if CommunicationMethod.GECKO_BOOTLOADER in ctx.obj["probe_methods"]:
        click.echo("Probing Gecko bootloader")

        # Try connecting with the bootloader first
        try:
            async with connect_protocol(
                ctx.obj["device"],
                ctx.obj["bootloader_baudrate"],
                GeckoBootloaderProtocol,
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
    if CommunicationMethod.EZSP in ctx.obj["probe_methods"]:
        click.echo("Probing EZSP")

        try:
            async with connect_ezsp(ctx.obj["device"], ctx.obj["baudrate"]) as ezsp:
                try:
                    res = await ezsp.launchStandaloneBootloader(0x01)
                    return
                except asyncio.TimeoutError as e:
                    _LOGGER.info("Failed to probe EZSP: %r", e)
                else:
                    if res[0] != bellows.types.EmberStatus.SUCCESS:
                        click.echo(f"Failed to enter bootloader via EZSP: {res[0]}")

                    return
        except asyncio.TimeoutError as e:
            _LOGGER.info("Failed to probe EZSP: %r", e)

    # Finally, try CPC
    if CommunicationMethod.CPC in ctx.obj["probe_methods"]:
        click.echo("Probing CPC")

        async with connect_protocol(
            ctx.obj["device"], ctx.obj["baudrate"], CPCProtocol
        ) as cpc:
            async with async_timeout.timeout(PROBE_TIMEOUT):
                await cpc.enter_bootloader()


@main.command()
@click.pass_context
@click.option("--yellow-gpio-reset", is_flag=True, default=False, show_default=True)
@click_coroutine
async def bootloader(ctx, yellow_gpio_reset):
    await _enter_bootloader(ctx, yellow_gpio_reset=yellow_gpio_reset)

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
@click.option("--yellow-gpio-reset", is_flag=True, default=False, show_default=True)
@click.pass_context
@click_coroutine
async def flash(
    ctx,
    firmware,
    force,
    allow_downgrades,
    allow_cross_flashing,
    skip_if_version_matches,
    yellow_gpio_reset,
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
    else:
        click.echo(f"Extracted GBL metadata: {metadata}")

    app_version, application_type = await _get_application_version(ctx)

    click.echo(f"Detected running firmware {application_type}, version {app_version}")

    if application_type == CommunicationMethod.EZSP:
        running_image_type = FirmwareImageType.NCP_UART_HW
    else:
        # TODO: how do you distinguish RCP_UART_802154 from ZIGBEE_NCP_RCP_UART_802154?
        running_image_type = FirmwareImageType.ZIGBEE_NCP_RCP_UART_802154

    if not force and app_version is not None and metadata is not None:
        if (
            metadata.image_type is not None
            and metadata.image_type != running_image_type
            and not allow_cross_flashing
        ):
            raise click.ClickException(
                f"Running image type {running_image_type}"
                f" does not match firmware image type {metadata.image_type}"
            )

        if app_version == metadata.get_public_version() and skip_if_version_matches:
            click.echo(f"Firmware version {app_version} is flashed, not upgrading")
            return

        if app_version > metadata.get_public_version() and not allow_downgrades:
            raise click.ClickException(
                f"Firmware version {metadata.get_public_version()} does not upgrade"
                f" current version {app_version}"
            )

    await _enter_bootloader(ctx, yellow_gpio_reset=yellow_gpio_reset)

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
    if CommunicationMethod.GECKO_BOOTLOADER in ctx.obj["probe_methods"]:
        click.echo("Connecting with Gecko bootloader")

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

                try:
                    await gecko.run_firmware()
                except NoFirmwareError:
                    return None, CommunicationMethod.GECKO_BOOTLOADER

    # Next, try EZSP
    if CommunicationMethod.EZSP in ctx.obj["probe_methods"]:
        click.echo("Connecting with EZSP")

        try:
            async with connect_ezsp(ctx.obj["device"], ctx.obj["baudrate"]) as ezsp:
                brd_manuf, brd_name, version = await ezsp.get_board_info()
                return (
                    AwesomeVersion(version.replace(" build ", ".")),
                    CommunicationMethod.EZSP,
                )
        except asyncio.TimeoutError as e:
            _LOGGER.debug("Failed to probe EZSP: %r", e)

    # Finally, try CPC
    if CommunicationMethod.CPC in ctx.obj["probe_methods"]:
        click.echo("Connecting with CPC")

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
