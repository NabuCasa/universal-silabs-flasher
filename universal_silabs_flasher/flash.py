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
import zigpy.types
import bellows.ezsp
import async_timeout
import bellows.types
import bellows.config
from awesomeversion import AwesomeVersion

from .cpc import CPCProtocol
from .gbl import GBLImage, FirmwareImageType
from .common import PROBE_TIMEOUT, connect_protocol, patch_pyserial_asyncio
from .emberznet import connect_ezsp
from .xmodemcrc import BLOCK_SIZE as XMODEM_BLOCK_SIZE, ReceiverCancelled
from .gecko_bootloader import NoFirmwareError, GeckoBootloaderProtocol

patch_pyserial_asyncio()

_LOGGER = logging.getLogger(__name__)
LOG_LEVELS = ["INFO", "DEBUG"]


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


async def _enter_bootloader(
    ctx, method: CommunicationMethod, *, yellow_gpio_reset: bool = False
) -> None:
    if yellow_gpio_reset:
        await asyncio.get_running_loop().run_in_executor(None, _enter_yellow_bootloader)

    if method == CommunicationMethod.GECKO_BOOTLOADER:
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
                    _LOGGER.debug("Failed to probe Gecko Bootloader: %r", e)
                else:
                    return
        except asyncio.TimeoutError as e:
            _LOGGER.debug("Failed to probe bootloader: %r", e)
    elif method == CommunicationMethod.EZSP:
        try:
            async with connect_ezsp(ctx.obj["device"], ctx.obj["baudrate"]) as ezsp:
                try:
                    res = await ezsp.launchStandaloneBootloader(0x01)
                    return
                except asyncio.TimeoutError as e:
                    _LOGGER.debug("Failed to probe EZSP: %r", e)
                else:
                    if res[0] != bellows.types.EmberStatus.SUCCESS:
                        _LOGGER.warning(
                            "Failed to enter bootloader via EZSP: %r", res[0]
                        )

                    return
        except asyncio.TimeoutError as e:
            _LOGGER.debug("Failed to probe EZSP: %r", e)

    # Finally, try CPC
    elif method == CommunicationMethod.CPC:
        async with connect_protocol(
            ctx.obj["device"], ctx.obj["baudrate"], CPCProtocol
        ) as cpc:
            async with async_timeout.timeout(PROBE_TIMEOUT):
                await cpc.enter_bootloader()
    else:
        raise ValueError(f"Invalid communication method: {method!r}")


async def _get_application_version(ctx) -> tuple[AwesomeVersion, CommunicationMethod]:
    # If we are in the bootloader, start the application
    if CommunicationMethod.GECKO_BOOTLOADER in ctx.obj["probe_methods"]:
        _LOGGER.info("Probing Gecko bootloader")

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
        _LOGGER.info("Probing EZSP")

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
        _LOGGER.info("Probing CPC")

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


@main.command()
@click.pass_context
@click.option("--ieee", required=True, type=zigpy.types.EUI64.convert)
@click_coroutine
async def write_ieee(ctx, ieee):
    new_eui64 = bellows.types.EmberEUI64(ieee)

    async with connect_ezsp(ctx.obj["device"], ctx.obj["baudrate"]) as ezsp:
        (current_eui64,) = await ezsp.getEui64()
        _LOGGER.info("Current device IEEE: %s", current_eui64)

        if current_eui64 == new_eui64:
            _LOGGER.info("Device IEEE address already matches, not overwriting")
            return

        if not await ezsp.can_write_custom_eui64():
            raise click.ClickException(
                "IEEE address has already been written once, cannot write again"
            )

        (status,) = await ezsp.setMfgToken(
            bellows.types.EzspMfgTokenId.MFG_CUSTOM_EUI_64, new_eui64.serialize()
        )

        if status != bellows.types.EmberStatus.SUCCESS:
            raise click.ClickException(f"Failed to write IEEE address: {status}")


@main.command()
@click.option("--firmware", type=click.File("rb"), required=True, show_default=True)
@click.option("--force", is_flag=True, default=False, show_default=True)
@click.option("--allow-downgrades", is_flag=True, default=False, show_default=True)
@click.option("--allow-cross-flashing", is_flag=True, default=False, show_default=True)
@click.option(
    "--allow-reflash-same-version", is_flag=True, default=False, show_default=True
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
    allow_reflash_same_version,
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
        _LOGGER.info("Extracted GBL metadata: %s", metadata)

    app_version, communication_method = await _get_application_version(ctx)

    _LOGGER.info(
        "Detected running firmware %s, version %s", communication_method, app_version
    )

    if communication_method == CommunicationMethod.EZSP:
        running_image_type = FirmwareImageType.NCP_UART_HW
    else:
        # TODO: how do you distinguish RCP_UART_802154 from ZIGBEE_NCP_RCP_UART_802154?
        running_image_type = FirmwareImageType.ZIGBEE_NCP_RCP_UART_802154

    if not force and app_version is not None and metadata is not None:
        cross_flashing = (
            metadata.fw_type is not None and metadata.fw_type != running_image_type
        )

        if cross_flashing and not allow_cross_flashing:
            raise click.ClickException(
                f"Running image type {running_image_type}"
                f" does not match firmware image type {metadata.fw_type}"
            )

        if (
            app_version == metadata.get_public_version()
            and not allow_reflash_same_version
        ):
            _LOGGER.info("Firmware version %s is flashed, not upgrading", app_version)
            return

        if (
            not cross_flashing
            and app_version > metadata.get_public_version()
            and not allow_downgrades
        ):
            _LOGGER.info(
                "Firmware version %s does not upgrade current version %s",
                metadata.get_public_version(),
                app_version,
            )
            return

    await _enter_bootloader(
        ctx,
        method=communication_method,
        yellow_gpio_reset=yellow_gpio_reset,
    )

    # Flash the image
    async with connect_protocol(
        ctx.obj["device"], ctx.obj["bootloader_baudrate"], GeckoBootloaderProtocol
    ) as gecko:
        await gecko.probe()

        _LOGGER.info("Flashing firwmare")

        with click.progressbar(
            label=os.path.basename(firmware.name),
            length=len(firmware_data),
            show_eta=True,
            show_percent=True,
        ) as pbar:
            try:
                await gecko.upload_firmware(
                    firmware_data,
                    progress_callback=lambda current, _: pbar.update(XMODEM_BLOCK_SIZE),
                )
            except ReceiverCancelled:
                raise click.ClickException(
                    "Firmware image was rejected by the device. Ensure this is the"
                    " correct image for this device."
                )

        _LOGGER.info("Launching application")

        await gecko.run_firmware()
