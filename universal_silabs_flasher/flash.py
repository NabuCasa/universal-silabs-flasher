from __future__ import annotations

import re
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
from .flasher import (
    DEFAULT_BAUDRATES,
    FW_IMAGE_TYPE_TO_APPLICATION_TYPE,
    Flasher,
    ApplicationType,
)
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

        # Windows COM port (COM10+ uses a different syntax)
        if re.match(r"^COM[0-9]$|\\\\\.\\COM[0-9]+$", str(path)):
            return value

        # Socket URI
        try:
            parsed = urllib.parse.urlparse(value)
        except ValueError:
            self.fail(f"Invalid URI: {path}", param, ctx)

        if parsed.scheme == "socket":
            return value
        elif parsed.scheme != "":
            self.fail(
                f"invalid URL scheme {parsed.scheme!r}, only `socket://` is accepted",
                param,
                ctx,
            )
        else:
            # Fallback
            self.fail(f"{path} does not exist", param, ctx)


@click.group()
@click.option("-v", "--verbose", count=True)
@click.option("--device", type=SerialPort(), required=True)
@click.option(
    "--baudrate",
    default=DEFAULT_BAUDRATES[ApplicationType.CPC],
    show_default=True,
)
@click.option(
    "--bootloader-baudrate",
    default=DEFAULT_BAUDRATES[ApplicationType.GECKO_BOOTLOADER],
    show_default=True,
)
@click.option(
    "--cpc-baudrate",
    default=DEFAULT_BAUDRATES[ApplicationType.CPC],
    show_default=True,
)
@click.option(
    "--ezsp-baudrate",
    default=DEFAULT_BAUDRATES[ApplicationType.EZSP],
    show_default=True,
)
@click.option(
    "--spinel-baudrate",
    default=DEFAULT_BAUDRATES[ApplicationType.SPINEL],
    show_default=True,
)
@click.option(
    "--probe-method",
    multiple=True,
    default=[m.value for m in ApplicationType],
    callback=click_enum_validator_factory(ApplicationType),
    show_default=True,
)
@click.pass_context
def main(
    ctx,
    verbose,
    device,
    baudrate,
    bootloader_baudrate,
    cpc_baudrate,
    ezsp_baudrate,
    spinel_baudrate,
    probe_method,
):
    coloredlogs.install(level=LOG_LEVELS[min(len(LOG_LEVELS) - 1, verbose)])

    # Override all application baudrates if a specific value is provided
    if ctx.get_parameter_source("baudrate") != click.core.ParameterSource.DEFAULT:
        cpc_baudrate = baudrate
        ezsp_baudrate = baudrate
        spinel_baudrate = baudrate

    ctx.obj = {
        "verbosity": verbose,
        "flasher": Flasher(
            device=device,
            baudrates={
                ApplicationType.GECKO_BOOTLOADER: bootloader_baudrate,
                ApplicationType.CPC: cpc_baudrate,
                ApplicationType.EZSP: ezsp_baudrate,
                ApplicationType.SPINEL: spinel_baudrate,
            },
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
@click.option("--ensure-exact-version", is_flag=True, default=False, show_default=True)
@click.option("--allow-downgrades", is_flag=True, default=False, show_default=True)
@click.option("--allow-cross-flashing", is_flag=True, default=False, show_default=True)
@click.option("--yellow-gpio-reset", is_flag=True, default=False, show_default=True)
@click.pass_context
@click_coroutine
async def flash(
    ctx,
    firmware,
    force,
    ensure_exact_version,
    allow_downgrades,
    allow_cross_flashing,
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

    # Prefer to probe the expected firmware image type first, if it is known
    if (
        metadata.fw_type is not None
        and ctx.parent.get_parameter_source("probe_method")
        == click.core.ParameterSource.DEFAULT
    ):
        # The bootloader and current firmware type come first
        methods = [
            ApplicationType.GECKO_BOOTLOADER,
            FW_IMAGE_TYPE_TO_APPLICATION_TYPE[metadata.fw_type],
        ]

        # Then come the rest of the probe methods
        flasher._probe_methods = methods + [
            m for m in flasher._probe_methods if m not in methods
        ]

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
    elif flasher.app_type == ApplicationType.SPINEL:
        running_image_type = FirmwareImageType.OT_RCP
    elif flasher.app_type == ApplicationType.GECKO_BOOTLOADER:
        running_image_type = None
    else:
        # TODO: how do you distinguish RCP_UART_802154 from ZIGBEE_NCP_RCP_UART_802154?
        running_image_type = FirmwareImageType.ZIGBEE_NCP_RCP_UART_802154

    # Ensure the firmware versions and image types are consistent
    if not force and flasher.app_version is not None and metadata is not None:
        is_cross_flashing = (
            metadata.fw_type is not None
            and running_image_type is not None
            and metadata.fw_type != running_image_type
        )

        if is_cross_flashing and not allow_cross_flashing:
            raise click.ClickException(
                f"Running image type {running_image_type}"
                f" does not match firmware image type {metadata.fw_type}."
                f" If you intend to cross-flash, run with `--allow-cross-flashing`."
            )

        if not is_cross_flashing:
            app_version = flasher.app_version
            fw_version = metadata.get_public_version()

            if app_version == fw_version:
                _LOGGER.info(
                    "Firmware version %s is flashed, not re-installing", app_version
                )
                return
            elif ensure_exact_version and app_version != fw_version:
                _LOGGER.info(
                    "Firmware version %s does not match expected version %s",
                    fw_version,
                    app_version,
                )
            elif not allow_downgrades and app_version > fw_version:
                _LOGGER.info(
                    "Firmware version %s does not upgrade current version %s",
                    fw_version,
                    app_version,
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
