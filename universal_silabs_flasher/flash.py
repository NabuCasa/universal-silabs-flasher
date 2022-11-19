from __future__ import annotations

import enum
import typing
import asyncio
import logging
import os.path
import pathlib
import functools
import urllib.parse

import click
import coloredlogs
import zigpy.types
import bellows.types

from .gbl import GBLImage, FirmwareImageType
from .common import patch_pyserial_asyncio
from .flasher import Flasher, ApplicationType
from .xmodemcrc import BLOCK_SIZE as XMODEM_BLOCK_SIZE, ReceiverCancelled

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


class SerialPort(click.ParamType):
    """Click validator that accepts serial ports."""

    name = "path_or_url"

    def convert(self, value, param, ctx):
        if isinstance(value, tuple):
            return value

        # File
        path = pathlib.Path(value)

        if path.exists():
            return value

        # Windows COM port
        if value.startswith("COM") and value[3:].isdigit():
            return value

        # Socket URI
        try:
            parsed = urllib.parse.urlparse(value)
        except ValueError:
            self.fail(f"Invalid URI: {path}", param, ctx)
        else:
            if parsed.scheme == "socket":
                return value

            self.fail(
                f"invalid URL scheme {parsed.scheme!r}, only `socket://` is accepted",
                param,
                ctx,
            )

        # Fallback
        self.fail(f"{path} does not exist", param, ctx)


@click.group()
@click.option("-v", "--verbose", count=True)
@click.option("--device", type=SerialPort(), required=True)
@click.option("--baudrate", default=115200, show_default=True)
@click.option("--bootloader-baudrate", default=115200, show_default=True)
@click.option(
    "--probe-method",
    multiple=True,
    default=[m.value for m in ApplicationType],
    callback=click_enum_validator_factory(ApplicationType),
    show_default=True,
)
@click.pass_context
def main(ctx, verbose, device, baudrate, bootloader_baudrate, probe_method):
    coloredlogs.install(level=LOG_LEVELS[min(len(LOG_LEVELS) - 1, verbose)])

    ctx.obj = {
        "verbosity": verbose,
        "flasher": Flasher(
            device=device,
            bootloader_baudrate=bootloader_baudrate,
            app_baudrate=baudrate,
            probe_methods=probe_method,
        ),
    }


@main.command()
@click.pass_context
@click.option("--ieee", required=True, type=zigpy.types.EUI64.convert)
@click_coroutine
async def write_ieee(ctx, ieee):
    new_eui64 = bellows.types.EmberEUI64(ieee)

    try:
        await ctx.obj["flasher"].write_emberznet_eui64(new_eui64)
    except (ValueError, RuntimeError) as e:
        raise click.ClickException(str(e)) from e


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
    flasher = ctx.obj["flasher"]

    # Parse and validate the firmware image
    firmware_data = firmware.read()
    firmware.close()

    gbl_image = GBLImage.from_bytes(firmware_data)

    try:
        metadata = gbl_image.get_nabucasa_metadata()
    except KeyError:
        metadata = None
    else:
        _LOGGER.info("Extracted GBL metadata: %s", metadata)

    try:
        await flasher.probe_app_type(yellow_gpio_reset=yellow_gpio_reset)
    except RuntimeError as e:
        raise click.ClickException(str(e)) from e

    _LOGGER.info(
        "Detected running firmware %s, version %s",
        flasher.app_type,
        flasher.app_version,
    )

    if flasher.app_type == ApplicationType.EZSP:
        running_image_type = FirmwareImageType.NCP_UART_HW
    else:
        # TODO: how do you distinguish RCP_UART_802154 from ZIGBEE_NCP_RCP_UART_802154?
        running_image_type = FirmwareImageType.ZIGBEE_NCP_RCP_UART_802154

    # Ensure the firmware versions and image types are consistent
    if not force and flasher.app_version is not None and metadata is not None:
        is_cross_flashing = (
            metadata.fw_type is not None and metadata.fw_type != running_image_type
        )

        if is_cross_flashing and not allow_cross_flashing:
            raise click.ClickException(
                f"Running image type {running_image_type}"
                f" does not match firmware image type {metadata.fw_type}"
            )

        if (
            flasher.app_version == metadata.get_public_version()
            and not allow_reflash_same_version
        ):
            _LOGGER.info(
                "Firmware version %s is flashed, not upgrading", flasher.app_version
            )
            return

        if (
            not is_cross_flashing
            and flasher.app_version > metadata.get_public_version()
            and not allow_downgrades
        ):
            _LOGGER.info(
                "Firmware version %s does not upgrade current version %s",
                metadata.get_public_version(),
                flasher.app_version,
            )
            return

    await flasher.enter_bootloader()

    pbar = click.progressbar(
        label=os.path.basename(firmware.name),
        length=len(firmware_data),
        show_eta=True,
        show_percent=True,
    )

    # Only show the progress bar if verbose logging won't interfere
    if ctx.obj["verbosity"] > 1:
        pbar.is_hidden = True

    with pbar:
        try:
            await flasher.flash_firmware(
                gbl_image,
                run_firmware=True,
                progress_callback=lambda current, _: pbar.update(XMODEM_BLOCK_SIZE),
            )
        except ReceiverCancelled:
            raise click.ClickException(
                "Firmware image was rejected by the device. Ensure this is the correct"
                " image for this device."
            )
