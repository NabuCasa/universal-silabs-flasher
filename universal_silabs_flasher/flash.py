from __future__ import annotations

import argparse
import json
import logging
import os.path
import pathlib
import re
import sys
import urllib.parse

import coloredlogs
import tqdm
import zigpy.ota.validators
import zigpy.types

from .common import put_first
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


def parse_serial_port(value: str) -> str:
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
        raise argparse.ArgumentTypeError(f"Invalid URI: {path}")

    if parsed.scheme == "socket":
        return value
    elif parsed.scheme != "":
        raise argparse.ArgumentTypeError(
            f"invalid URL scheme {parsed.scheme!r}, only `socket://` is accepted"
        )
    else:
        raise argparse.ArgumentTypeError(f"{path} does not exist")


def parse_probe_methods(value: str) -> list[tuple[ApplicationType, int]]:
    result = []

    for method in value.split(","):
        parts = method.split(":")

        if len(parts) != 2:
            raise argparse.ArgumentTypeError(
                f"invalid probe method {method!r}, must be in the format"
                f" '<application_type>:<baudrate>'"
            )

        app_type_str, baudrate_str = parts

        try:
            app_type = ApplicationType(app_type_str)
        except ValueError:
            expected = [m.value for m in ApplicationType]
            raise argparse.ArgumentTypeError(
                f"invalid application type {app_type_str!r}, must be one of: "
                f"{', '.join(expected)}"
            )

        try:
            baudrate = int(baudrate_str)
        except ValueError:
            raise argparse.ArgumentTypeError(
                f"invalid baudrate {baudrate_str!r}, must be an integer"
            )

        result.append((app_type, baudrate))

    return result


def parse_reset_methods(value: str) -> list[ResetTarget]:
    enums = []

    for v in value.split(","):
        try:
            enums.append(ResetTarget(v))
        except ValueError:
            expected = [m.value for m in ResetTarget]
            raise argparse.ArgumentTypeError(
                f"{v!r} is invalid, must be one of: {', '.join(expected)}"
            )

    return enums


def parse_comma_separated_numbers(value: str) -> list[int]:
    values = []

    for v in value.split(","):
        if not v.strip():
            continue
        try:
            values.append(int(v, 10))
        except ValueError:
            raise argparse.ArgumentTypeError(
                f"Comma-separated list of numbers contains bad value: {v!r}"
            )

    return values


def parse_application_type(value: str) -> ApplicationType:
    try:
        return ApplicationType(value)
    except ValueError:
        expected = [m.value for m in ApplicationType]
        raise argparse.ArgumentTypeError(
            f"{value!r} is invalid, must be one of: {', '.join(expected)}"
        )


async def main(argv: list[str] | None = None) -> None:
    global_parser = argparse.ArgumentParser(add_help=False)
    global_parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=argparse.SUPPRESS,
    )
    global_parser.add_argument(
        "--device",
        type=parse_serial_port,
        default=argparse.SUPPRESS,
    )
    global_parser.add_argument(
        "--probe-methods",
        dest="probe_methods",
        type=parse_probe_methods,
        default=argparse.SUPPRESS,
        help=(
            "Comma-separated list of application type and baudrate pairs to use when"
            " probing the device. Each pair should be in the format"
            " '<application_type>:<baudrate>'. Valid application types: "
            f"{', '.join([m.value for m in ApplicationType])}. Example: "
            "'ezsp:115200,ezsp:460800,spinel:460800'"
        ),
    )
    global_parser.add_argument(
        "--bootloader-reset",
        dest="bootloader_reset",
        type=parse_reset_methods,
        default=argparse.SUPPRESS,
        help=(
            f"Reset methods to attempt when triggering bootloader mode. Multiple"
            f" methods can be chained by separating them with a comma. Valid values:"
            f" {', '.join([m.value for m in ResetTarget])}"
        ),
    )
    # Deprecated flags
    global_parser.add_argument(
        "--bootloader-baudrate",
        dest="deprecated_bootloader_baudrate",
        type=parse_comma_separated_numbers,
        default=argparse.SUPPRESS,
        help=argparse.SUPPRESS,
    )
    global_parser.add_argument(
        "--cpc-baudrate",
        dest="deprecated_cpc_baudrate",
        type=parse_comma_separated_numbers,
        default=argparse.SUPPRESS,
        help=argparse.SUPPRESS,
    )
    global_parser.add_argument(
        "--ezsp-baudrate",
        dest="deprecated_ezsp_baudrate",
        type=parse_comma_separated_numbers,
        default=argparse.SUPPRESS,
        help=argparse.SUPPRESS,
    )
    global_parser.add_argument(
        "--router-baudrate",
        dest="deprecated_router_baudrate",
        type=parse_comma_separated_numbers,
        default=argparse.SUPPRESS,
        help=argparse.SUPPRESS,
    )
    global_parser.add_argument(
        "--spinel-baudrate",
        dest="deprecated_spinel_baudrate",
        type=parse_comma_separated_numbers,
        default=argparse.SUPPRESS,
        help=argparse.SUPPRESS,
    )
    global_parser.add_argument(
        "--probe-method",
        dest="deprecated_probe_methods",
        action="append",
        type=parse_application_type,
        default=argparse.SUPPRESS,
        help=argparse.SUPPRESS,
    )

    parser = argparse.ArgumentParser(
        prog="universal-silabs-flasher",
        parents=[global_parser],
        allow_abbrev=False,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # dump-gbl-metadata: --device not required
    dump_parser = subparsers.add_parser("dump-gbl-metadata", parents=[global_parser])
    dump_parser.add_argument(
        "--firmware",
        type=argparse.FileType("rb"),
        required=True,
    )

    # probe
    subparsers.add_parser("probe", parents=[global_parser])

    # write-ieee
    write_ieee_parser = subparsers.add_parser("write-ieee", parents=[global_parser])
    write_ieee_parser.add_argument(
        "--ieee",
        required=True,
        type=zigpy.types.EUI64.convert,
    )
    write_ieee_parser.add_argument(
        "--force",
        action="store_true",
        default=False,
    )

    # flash
    flash_parser = subparsers.add_parser("flash", parents=[global_parser])
    flash_parser.add_argument(
        "--firmware",
        type=argparse.FileType("rb"),
        required=True,
    )
    flash_parser.add_argument(
        "--force",
        action="store_true",
        default=False,
    )
    flash_parser.add_argument(
        "--ensure-exact-version",
        action="store_true",
        default=False,
        dest="ensure_exact_version",
    )
    flash_parser.add_argument(
        "--allow-downgrades",
        action="store_true",
        default=False,
        dest="allow_downgrades",
    )
    flash_parser.add_argument(
        "--allow-cross-flashing",
        action="store_true",
        default=False,
        dest="allow_cross_flashing",
    )
    flash_parser.add_argument(
        "--yellow-gpio-reset",
        action="store_true",
        default=False,
        dest="yellow_gpio_reset",
    )
    flash_parser.add_argument(
        "--sonoff-reset",
        action="store_true",
        default=False,
        dest="sonoff_reset",
    )

    args = parser.parse_args(argv)

    coloredlogs.install(
        fmt=(
            "%(asctime)s.%(msecs)03d"
            " %(hostname)s"
            " %(name)s"
            " %(levelname)s %(message)s"
        ),
        level=LOG_LEVELS[min(len(LOG_LEVELS) - 1, getattr(args, "verbose", 0))],
    )

    # --device is required for all subcommands except dump-gbl-metadata
    if not hasattr(args, "device") and args.command != "dump-gbl-metadata":
        parser.error("Missing option '--device'")

    # Handle deprecated baudrate/probe-method flags
    _DEPRECATED_ATTRS = (
        "deprecated_bootloader_baudrate",
        "deprecated_cpc_baudrate",
        "deprecated_ezsp_baudrate",
        "deprecated_router_baudrate",
        "deprecated_spinel_baudrate",
        "deprecated_probe_methods",
    )
    probe_methods = list(getattr(args, "probe_methods", DEFAULT_PROBE_METHODS))

    if any(hasattr(args, attr) for attr in _DEPRECATED_ATTRS):
        if hasattr(args, "probe_methods"):
            parser.error(
                "`--probe-methods` cannot be used with deprecated baudrate flags"
            )

        baudrates = {
            ApplicationType.GECKO_BOOTLOADER: getattr(
                args,
                "deprecated_bootloader_baudrate",
                DEFAULT_BAUDRATES[ApplicationType.GECKO_BOOTLOADER],
            ),
            ApplicationType.CPC: getattr(
                args,
                "deprecated_cpc_baudrate",
                DEFAULT_BAUDRATES[ApplicationType.CPC],
            ),
            ApplicationType.EZSP: getattr(
                args,
                "deprecated_ezsp_baudrate",
                DEFAULT_BAUDRATES[ApplicationType.EZSP],
            ),
            ApplicationType.ROUTER: getattr(
                args,
                "deprecated_router_baudrate",
                DEFAULT_BAUDRATES[ApplicationType.ROUTER],
            ),
            ApplicationType.SPINEL: getattr(
                args,
                "deprecated_spinel_baudrate",
                DEFAULT_BAUDRATES[ApplicationType.SPINEL],
            ),
        }

        deprecated_methods = getattr(
            args, "deprecated_probe_methods", list(ApplicationType)
        )
        probe_methods = [
            (method, baudrate)
            for method in deprecated_methods
            for baudrate in baudrates[method]
        ]

    flasher = Flasher(
        device=getattr(args, "device", None),
        probe_methods=probe_methods,
        bootloader_reset=tuple(getattr(args, "bootloader_reset", [])),
    )

    if args.command == "dump-gbl-metadata":
        await _cmd_dump_gbl_metadata(args)
    elif args.command == "probe":
        await _cmd_probe(flasher)
    elif args.command == "write-ieee":
        await _cmd_write_ieee(args, flasher)
    elif args.command == "flash":
        await _cmd_flash(args, flasher, getattr(args, "verbose", 0))


async def _cmd_dump_gbl_metadata(args: argparse.Namespace) -> None:
    firmware_data = args.firmware.read()
    args.firmware.close()

    try:
        fw_image = parse_firmware_image(firmware_data)
    except zigpy.ota.validators.ValidationError as e:
        print(
            f"Error: {args.firmware.name!r} does not appear to be a valid firmware"
            f" image: {e!r}",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        metadata = fw_image.get_nabucasa_metadata()
    except KeyError:
        metadata_obj = None
    else:
        metadata_obj = metadata.original_json
        _LOGGER.info("Extracted firmware metadata: %s", metadata)

    print(json.dumps(metadata_obj))


async def _cmd_probe(flasher: Flasher) -> None:
    try:
        await flasher.probe_app_type()
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if flasher.app_type == ApplicationType.EZSP:
        _LOGGER.info("Dumping EmberZNet Config")
        try:
            await flasher.dump_emberznet_config()
        except RuntimeError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)


async def _cmd_write_ieee(args: argparse.Namespace, flasher: Flasher) -> None:
    try:
        await flasher.write_emberznet_eui64(args.ieee, force=args.force)
    except (ValueError, RuntimeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


async def _cmd_flash(
    args: argparse.Namespace, flasher: Flasher, verbosity: int
) -> None:
    firmware_data = args.firmware.read()
    args.firmware.close()

    try:
        fw_image = parse_firmware_image(firmware_data)
    except (zigpy.ota.validators.ValidationError, ValueError) as e:
        print(
            f"Error: {args.firmware.name!r} does not appear to be a valid firmware"
            f" image: {e!r}",
            file=sys.stderr,
        )
        sys.exit(1)

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
    if args.yellow_gpio_reset:
        flasher._reset_targets = [ResetTarget.YELLOW]
        _LOGGER.info(reset_msg, "--yellow-gpio-reset")
    elif args.sonoff_reset:
        flasher._reset_targets = [ResetTarget.RTS_DTR]
        _LOGGER.info(reset_msg, "--sonoff-reset")

    try:
        await flasher.probe_app_type()
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

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
    if not args.force and flasher.app_version is not None and metadata is not None:
        app_version = flasher.app_version
        fw_version = metadata.get_public_version()

        is_cross_flashing = (
            metadata.fw_type is not None
            and running_image_type is not None
            and metadata.fw_type != running_image_type
        )

        if is_cross_flashing and not args.allow_cross_flashing:
            print(
                f"Error: Running image type {running_image_type}"
                f" does not match firmware image type {metadata.fw_type}."
                f" If you intend to cross-flash, run with `--allow-cross-flashing`.",
                file=sys.stderr,
            )
            sys.exit(1)

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
            elif args.ensure_exact_version and app_version != fw_version:
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
            elif not args.allow_downgrades and app_version > fw_version:
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

    with tqdm.tqdm(
        total=len(firmware_data),
        desc=os.path.basename(args.firmware.name),
        unit="B",
        unit_scale=True,
        disable=verbosity > 1,
    ) as pbar:
        try:
            await flasher.flash_firmware(
                fw_image,
                run_firmware=True,
                progress_callback=lambda current, _: pbar.update(XMODEM_BLOCK_SIZE),
            )
        except ReceiverCancelled:
            print(
                "Error: Firmware image was rejected by the device. Ensure this is"
                " the correct image for this device.",
                file=sys.stderr,
            )
            sys.exit(1)
