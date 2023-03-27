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
import zigpy.ota.validators
from awesomeversion.exceptions import AwesomeVersionCompareException

from .gbl import GBLImage, FirmwareImageType
from .const import DEFAULT_BAUDRATES, FW_IMAGE_TYPE_TO_APPLICATION_TYPE, ApplicationType
from .common import CommaSeparatedNumbers, put_first, patch_pyserial_asyncio
from .flasher import Flasher
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
@click.option("--baudrate", hidden=True)
@click.option(
    "--bootloader-baudrate",
    default=DEFAULT_BAUDRATES[ApplicationType.GECKO_BOOTLOADER],
    type=CommaSeparatedNumbers(),
    show_default=True,
)
@click.option(
    "--cpc-baudrate",
    default=DEFAULT_BAUDRATES[ApplicationType.CPC],
    type=CommaSeparatedNumbers(),
    show_default=True,
)
@click.option(
    "--ezsp-baudrate",
    default=DEFAULT_BAUDRATES[ApplicationType.EZSP],
    type=CommaSeparatedNumbers(),
    show_default=True,
)
@click.option(
    "--spinel-baudrate",
    default=DEFAULT_BAUDRATES[ApplicationType.SPINEL],
    type=CommaSeparatedNumbers(),
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
        raise click.ClickException(
            "The `--baudrate` flag is deprecated. Remove it to rely on auto baudrate"
            " probing, or replace it with an application-specific baudrate flag"
            " (see `--help`)"
        )

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

    try:
        gbl_image = GBLImage.from_bytes(firmware_data)
    except zigpy.ota.validators.ValidationError as e:
        raise click.ClickException(
            f"{firmware.name!r} does not appear to be a valid GBL image: {e!r}"
        )

    try:
        metadata = gbl_image.get_nabucasa_metadata()
    except KeyError:
        metadata = None
    else:
        _LOGGER.info("Extracted GBL metadata: %s", metadata)

    # Prefer to probe with the current firmware's settings to speed up startup after the
    # firmware is flashed for the first time
    if metadata is not None and metadata.fw_type is not None:
        app_type = FW_IMAGE_TYPE_TO_APPLICATION_TYPE[metadata.fw_type]

        # Probe with the firmware's app type first
        if (
            ctx.parent.get_parameter_source("probe_method")
            == click.core.ParameterSource.DEFAULT
        ):
            _LOGGER.debug("Probing app type %s first", app_type)
            flasher._probe_methods = put_first(
                flasher._probe_methods, [ApplicationType.GECKO_BOOTLOADER, app_type]
            )

        # Probe with the firmware's baudrate first
        if (
            metadata.baudrate is not None
            and ctx.parent.get_parameter_source(app_type.name)
            == click.core.ParameterSource.DEFAULT
        ):
            _LOGGER.debug("Probing with %s baudrate first", app_type)
            flasher._baudrates[app_type] = put_first(
                flasher._baudrates[app_type], [metadata.baudrate]
            )

    try:
        await flasher.probe_app_type(yellow_gpio_reset=yellow_gpio_reset)
    except RuntimeError as e:
        raise click.ClickException(str(e)) from e

    if flasher.app_type == ApplicationType.EZSP:
        running_image_type = FirmwareImageType.NCP_UART_HW
    elif flasher.app_type == ApplicationType.SPINEL:
        running_image_type = FirmwareImageType.OT_RCP
    elif flasher.app_type == ApplicationType.CPC:
        # TODO: how do you distinguish RCP_UART_802154 from ZIGBEE_NCP_RCP_UART_802154?
        running_image_type = FirmwareImageType.RCP_UART_802154
    elif flasher.app_type == ApplicationType.GECKO_BOOTLOADER:
        running_image_type = None
    else:
        raise RuntimeError(f"Unknown application type {flasher.app_type!r}")

    # Ensure the firmware versions and image types are consistent
    if not force and flasher.app_version is not None and metadata is not None:
        app_version = flasher.app_version
        fw_version = metadata.get_public_version()

        try:
            app_version > fw_version  # noqa: B015
        except AwesomeVersionCompareException:
            can_compare_versions = False
        else:
            can_compare_versions = True

        is_cross_flashing = (
            metadata.fw_type is not None
            and running_image_type is not None
            and metadata.fw_type != running_image_type
        ) or not can_compare_versions

        if is_cross_flashing and not allow_cross_flashing:
            raise click.ClickException(
                f"Running image type {running_image_type}"
                f" does not match firmware image type {metadata.fw_type}."
                f" If you intend to cross-flash, run with `--allow-cross-flashing`."
            )

        if not is_cross_flashing:
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
