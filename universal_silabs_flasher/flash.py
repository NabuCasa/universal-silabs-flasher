from __future__ import annotations

import asyncio
import enum
import functools
import json
import logging
import os.path
import pathlib
import re
import typing
import urllib.parse

import click
import coloredlogs
import zigpy.ota.validators
import zigpy.types

from .common import CommaSeparatedNumbers, put_first
from .const import (
    DEFAULT_BAUDRATES,
    DEFAULT_PROBE_METHODS,
    FW_IMAGE_TYPE_TO_APPLICATION_TYPE,
    ApplicationType,
    ResetTarget,
)
from .firmware import FirmwareImageType, parse_firmware_image
from .flasher import Flasher
from .gecko_bootloader import XMODEM_BLOCK_SIZE, ReceiverCancelled

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
        ctx: click.Context, param: click.Parameter, value: tuple[str]
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


class EnumWithSeparator(click.ParamType):
    """Click validator that accepts enum values separated by plus signs."""

    name = "enum_with_separator"

    def __init__(self, enum_cls: type[enum.Enum], separator: str = ",") -> None:
        self._enum_cls = enum_cls
        self._separator = separator

    def convert(
        self, value: str | list[enum.Enum], param: click.Parameter, ctx: click.Context
    ) -> list[enum.Enum]:
        if isinstance(value, list):
            return value

        values = value.split(self._separator)
        enums = []

        for v in values:
            try:
                enums.append(self._enum_cls(v))
            except ValueError:
                expected = [m.value for m in self._enum_cls]
                self.fail(
                    f"{v!r} is invalid, must be one of: {', '.join(expected)}",
                    param,
                    ctx,
                )

        return enums


class ClickProbeMethods(click.ParamType):
    """Click validator that accepts probe methods in the format
    '<application_type>:<baudrate>,<application_type>:<baudrate>,...'
    """

    name = "probe_methods"

    def convert(
        self,
        value: str | list[tuple[ApplicationType, int]],
        param: click.Parameter,
        ctx: click.Context,
    ) -> list[tuple[ApplicationType, int]]:
        if isinstance(value, list):
            return value

        methods = value.split(",")
        result = []

        for method in methods:
            parts = method.split(":")

            if len(parts) != 2:
                self.fail(
                    f"invalid probe method {method!r}, must be in the format"
                    f" '<application_type>:<baudrate>'",
                    param,
                    ctx,
                )

            app_type_str, baudrate_str = parts

            try:
                app_type = ApplicationType(app_type_str)
            except ValueError:
                expected = [m.value for m in ApplicationType]
                self.fail(
                    f"invalid application type {app_type_str!r}, must be one of: "
                    f"{', '.join(expected)}",
                    param,
                    ctx,
                )

            try:
                baudrate = int(baudrate_str)
            except ValueError:
                self.fail(
                    f"invalid baudrate {baudrate_str!r}, must be an integer",
                    param,
                    ctx,
                )

            result.append((app_type, baudrate))

        return result


class SerialPort(click.ParamType):
    """Click validator that accepts serial ports."""

    name = "path_or_url"

    def convert(self, value: tuple | str, param: click.Parameter, ctx: click.Context):
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
@click.option("--device", type=SerialPort())
@click.option(
    "--probe-methods",
    show_default=True,
    type=ClickProbeMethods(),
    default=",".join(
        f"{method.value}:{baudrate}" for method, baudrate in DEFAULT_PROBE_METHODS
    ),
    help=(
        "Comma-separated list of application type and baudrate pairs to use when"
        " probing the device. Each pair should be in the format"
        " '<application_type>:<baudrate>'. Valid application types: "
        f"{', '.join([m.value for m in ApplicationType])}. Example: "
        "'ezsp:115200,ezsp:460800,spinel:460800'"
    ),
)
@click.option(
    "--bootloader-reset",
    default=[],
    type=EnumWithSeparator(ResetTarget),
    help=(
        f"Reset methods to attempt when triggering bootloader mode. Multiple methods"
        f" can be chained by separating them with a comma. Valid values: "
        f" {', '.join([m.value for m in ResetTarget])}"
    ),
)
# Begin deprecated flags
@click.option(
    "--bootloader-baudrate",
    "deprecated_bootloader_baudrate",
    default=DEFAULT_BAUDRATES[ApplicationType.GECKO_BOOTLOADER],
    type=CommaSeparatedNumbers(),
    show_default=True,
    hidden=True,
    deprecated=True,
)
@click.option(
    "--cpc-baudrate",
    "deprecated_cpc_baudrate",
    default=DEFAULT_BAUDRATES[ApplicationType.CPC],
    type=CommaSeparatedNumbers(),
    show_default=True,
    hidden=True,
    deprecated=True,
)
@click.option(
    "--ezsp-baudrate",
    "deprecated_ezsp_baudrate",
    default=DEFAULT_BAUDRATES[ApplicationType.EZSP],
    type=CommaSeparatedNumbers(),
    show_default=True,
    hidden=True,
    deprecated=True,
)
@click.option(
    "--router-baudrate",
    "deprecated_router_baudrate",
    default=DEFAULT_BAUDRATES[ApplicationType.ROUTER],
    type=CommaSeparatedNumbers(),
    show_default=True,
    hidden=True,
    deprecated=True,
)
@click.option(
    "--spinel-baudrate",
    "deprecated_spinel_baudrate",
    default=DEFAULT_BAUDRATES[ApplicationType.SPINEL],
    type=CommaSeparatedNumbers(),
    show_default=True,
    hidden=True,
    deprecated=True,
)
@click.option(
    "--probe-method",
    "deprecated_probe_methods",
    multiple=True,
    default=[m.value for m in ApplicationType],
    callback=click_enum_validator_factory(ApplicationType),
    show_default=True,
    hidden=True,
    deprecated=True,
)
# End deprecated flags
@click.pass_context
def main(
    ctx: click.Context,
    verbose: bool,
    device: str,
    probe_methods: list[tuple[ApplicationType, int]],
    bootloader_reset: list[ResetTarget],
    deprecated_bootloader_baudrate: list[int],
    deprecated_cpc_baudrate: list[int],
    deprecated_ezsp_baudrate: list[int],
    deprecated_router_baudrate: list[int],
    deprecated_spinel_baudrate: list[int],
    deprecated_probe_methods: list[ApplicationType],
) -> None:
    coloredlogs.install(
        fmt=(
            "%(asctime)s.%(msecs)03d"
            " %(hostname)s"
            " %(name)s"
            " %(levelname)s %(message)s"
        ),
        level=LOG_LEVELS[min(len(LOG_LEVELS) - 1, verbose)],
    )

    # To maintain some backwards compatibility, make `--device` required only when we
    # are actually invoking a command that interacts with a device
    if ctx.get_parameter_source(
        "device"
    ) == click.core.ParameterSource.DEFAULT and ctx.invoked_subcommand not in (
        dump_gbl_metadata.name
    ):
        # Replicate the "Error: Missing option" traceback
        param = next(p for p in ctx.command.params if p.name == "device")
        raise click.MissingParameter(ctx=ctx, param=param)

    # Finally, deprecated baudrate baudrate flags should be converted
    if any(
        ctx.get_parameter_source(param) != click.core.ParameterSource.DEFAULT
        for param in (
            "deprecated_bootloader_baudrate",
            "deprecated_cpc_baudrate",
            "deprecated_ezsp_baudrate",
            "deprecated_router_baudrate",
            "deprecated_spinel_baudrate",
            "deprecated_probe_methods",
        )
    ):
        if (
            ctx.get_parameter_source("probe_methods")
            != click.core.ParameterSource.DEFAULT
        ):
            raise click.ClickException(
                "`--probe-methods` cannot be used with deprecated baudrate flags"
            )

        baudrates = {
            ApplicationType.GECKO_BOOTLOADER: deprecated_bootloader_baudrate,
            ApplicationType.CPC: deprecated_cpc_baudrate,
            ApplicationType.EZSP: deprecated_ezsp_baudrate,
            ApplicationType.ROUTER: deprecated_router_baudrate,
            ApplicationType.SPINEL: deprecated_spinel_baudrate,
        }

        probe_methods = []

        for method in deprecated_probe_methods:
            for baudrate in baudrates[method]:
                probe_methods.append((method, baudrate))

    ctx.obj = {
        "verbosity": verbose,
        "flasher": Flasher(
            device=device,
            probe_methods=probe_methods,
            bootloader_reset=tuple(bootloader_reset),
        ),
    }


@main.command()
@click.pass_context
@click.option("--firmware", type=click.File("rb"), required=True, show_default=True)
@click_coroutine
async def dump_gbl_metadata(ctx: click.Context, firmware: typing.BinaryIO) -> None:
    # Parse and validate the firmware image
    firmware_data = firmware.read()
    firmware.close()

    try:
        fw_image = parse_firmware_image(firmware_data)
    except zigpy.ota.validators.ValidationError as e:
        raise click.ClickException(
            f"{firmware.name!r} does not appear to be a valid firmware image: {e!r}"
        )

    try:
        metadata = fw_image.get_nabucasa_metadata()
    except KeyError:
        metadata_obj = None
    else:
        metadata_obj = metadata.original_json
        _LOGGER.info("Extracted firmware metadata: %s", metadata)

    print(json.dumps(metadata_obj))


@main.command()
@click.pass_context
@click_coroutine
async def probe(ctx: click.Context) -> None:
    flasher = ctx.obj["flasher"]

    try:
        await flasher.probe_app_type()
    except RuntimeError as e:
        raise click.ClickException(str(e)) from e

    if flasher.app_type == ApplicationType.EZSP:
        _LOGGER.info("Dumping EmberZNet Config")
        try:
            await flasher.dump_emberznet_config()
        except RuntimeError as e:
            raise click.ClickException(str(e)) from e


@main.command()
@click.pass_context
@click.option("--ieee", required=True, type=zigpy.types.EUI64.convert)
@click.option("--force", default=False, type=bool)
@click_coroutine
async def write_ieee(ctx: click.Context, ieee: zigpy.types.EUI64, force: bool) -> None:
    try:
        await ctx.obj["flasher"].write_emberznet_eui64(ieee, force=force)
    except (ValueError, RuntimeError) as e:
        raise click.ClickException(str(e)) from e


@main.command()
@click.option("--firmware", type=click.File("rb"), required=True, show_default=True)
@click.option("--force", is_flag=True, default=False, show_default=True)
@click.option("--ensure-exact-version", is_flag=True, default=False, show_default=True)
@click.option("--allow-downgrades", is_flag=True, default=False, show_default=True)
@click.option("--allow-cross-flashing", is_flag=True, default=False, show_default=True)
@click.option("--yellow-gpio-reset", is_flag=True, default=False, show_default=True)
@click.option("--sonoff-reset", is_flag=True, default=False, show_default=True)
@click.pass_context
@click_coroutine
async def flash(
    ctx: click.Context,
    firmware: typing.BinaryIO,
    force: bool,
    ensure_exact_version: bool,
    allow_downgrades: bool,
    allow_cross_flashing: bool,
    yellow_gpio_reset: bool,
    sonoff_reset: bool,
) -> None:
    flasher = ctx.obj["flasher"]

    # Parse and validate the firmware image
    firmware_data = firmware.read()
    firmware.close()

    try:
        fw_image = parse_firmware_image(firmware_data)
    except (zigpy.ota.validators.ValidationError, ValueError) as e:
        raise click.ClickException(
            f"{firmware.name!r} does not appear to be a valid firmware image: {e!r}"
        )

    try:
        metadata = fw_image.get_nabucasa_metadata()
    except Exception:
        _LOGGER.info("Failed to read firmware metadata: {exc!r}")
        metadata = None
    else:
        _LOGGER.info("Extracted GBL metadata: %s", metadata)

    # Prefer to probe with the current firmware's settings to speed up startup after the
    # firmware is flashed for the first time
    if metadata is not None and metadata.fw_type is not None:
        app_type = FW_IMAGE_TYPE_TO_APPLICATION_TYPE[metadata.fw_type]

        _LOGGER.debug(
            "Probing app type %s at %s baud first", app_type, metadata.baudrate
        )
        flasher._probe_methods = put_first(
            flasher._probe_methods, [(app_type, metadata.baudrate)]
        )

    # Maintain backward compatibility with the deprecated reset flags
    reset_msg = (
        "The '%s' flag is deprecated. Use '--bootloader-reset' "
        "instead, see --help for details."
    )
    if yellow_gpio_reset:
        flasher._reset_targets = [ResetTarget.YELLOW]
        _LOGGER.info(reset_msg, "--yellow-gpio-reset")
    elif sonoff_reset:
        flasher._reset_targets = [ResetTarget.RTS_DTR]
        _LOGGER.info(reset_msg, "--sonoff-reset")

    try:
        await flasher.probe_app_type()
    except RuntimeError as e:
        raise click.ClickException(str(e)) from e

    if flasher.app_type == ApplicationType.EZSP:
        running_image_type = FirmwareImageType.ZIGBEE_NCP
    elif flasher.app_type == ApplicationType.ROUTER:
        running_image_type = FirmwareImageType.ZIGBEE_ROUTER
    elif flasher.app_type == ApplicationType.SPINEL:
        running_image_type = FirmwareImageType.OPENTHREAD_RCP
    elif flasher.app_type == ApplicationType.CPC:
        # TODO: how do you distinguish RCP_UART_802154 from ZIGBEE_NCP_RCP_UART_802154?
        running_image_type = FirmwareImageType.MULTIPAN
    elif flasher.app_type == ApplicationType.GECKO_BOOTLOADER:
        running_image_type = None
    else:
        raise RuntimeError(f"Unknown application type {flasher.app_type!r}")

    # Ensure the firmware versions and image types are consistent
    if not force and flasher.app_version is not None and metadata is not None:
        app_version = flasher.app_version
        fw_version = metadata.get_public_version()

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
            if (
                metadata.baudrate is not None
                and metadata.baudrate != flasher.app_baudrate
            ):
                _LOGGER.info(
                    "Firmware baudrate %s differs from expected baudrate %s",
                    flasher.app_baudrate,
                    metadata.baudrate,
                )
            elif ensure_exact_version and app_version != fw_version:
                _LOGGER.info(
                    "Firmware version %s does not match expected version %s",
                    fw_version,
                    app_version,
                )
            elif app_version.compatible_with(fw_version):
                _LOGGER.info(
                    "Firmware version %s is flashed, not re-installing", app_version
                )
                return
            elif not allow_downgrades and app_version > fw_version:
                _LOGGER.info(
                    "Firmware version %s does not upgrade current version %s",
                    fw_version,
                    app_version,
                )
                return
        else:
            _LOGGER.info(
                "Cross-flashing from %s to %s", running_image_type, metadata.fw_type
            )

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
                fw_image,
                run_firmware=True,
                progress_callback=lambda current, _: pbar.update(XMODEM_BLOCK_SIZE),
            )
        except ReceiverCancelled:
            raise click.ClickException(
                "Firmware image was rejected by the device. Ensure this is the correct"
                " image for this device."
            )
