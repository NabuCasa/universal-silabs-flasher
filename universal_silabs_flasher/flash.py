from __future__ import annotations

import argparse
import asyncio
from io import BytesIO
import json
import logging
import os.path
from pathlib import Path
import sys
import zipfile

import aiohttp
import coloredlogs
import tqdm
import zigpy.ota.validators
import zigpy.types

from .const import DEFAULT_PROBE_METHODS, ApplicationType, ResetTarget
from .firmware import parse_firmware_image
from .flasher import DEVICE_SPECIFIC_FLASHERS, BaseFlasher, Flasher
from .gecko_bootloader import XMODEM_BLOCK_SIZE, ReceiverCancelled

_LOGGER = logging.getLogger(__name__)
LOG_LEVELS = ["INFO", "DEBUG"]


async def _load_firmware_data(source: str) -> tuple[bytes, str]:
    """Load firmware bytes from a local file path or an HTTP/HTTPS URL.

    If the loaded file is a ZIP archive, the first .gbl entry is used;
    falling back to the very first entry if none have a .gbl extension.
    """
    if source.startswith(("http://", "https://")):
        async with aiohttp.ClientSession() as session:
            async with session.get(source, allow_redirects=True) as resp:
                resp.raise_for_status()
                data = await resp.read()
    else:
        data = await asyncio.to_thread(Path(source).read_bytes)

    data_io = BytesIO(data)

    if zipfile.is_zipfile(data_io):
        with zipfile.ZipFile(data_io) as zf:
            names = zf.namelist()

            # Prefer the first .gbl file, if one exists
            entry = next((n for n in names if n.lower().endswith(".gbl")), names[0])
            _LOGGER.debug("Extracting %r from ZIP archive %r", entry, source)

            return zf.read(entry), entry

    return data, source


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
        type=str,
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
        required=True,
    )
    flash_parser.add_argument(
        "--profile",
        choices=list(reversed(DEVICE_SPECIFIC_FLASHERS)),
        default=argparse.SUPPRESS,
        help=(
            "Use a predefined flashing profile. Cannot be used with"
            " --probe-methods or --bootloader-reset."
        ),
    )

    args = parser.parse_args(argv)

    coloredlogs.install(
        fmt=("%(asctime)s.%(msecs)03d %(hostname)s %(name)s %(levelname)s %(message)s"),
        level=LOG_LEVELS[min(len(LOG_LEVELS) - 1, getattr(args, "verbose", 0))],
    )

    # --device is required for all subcommands except dump-gbl-metadata
    if not hasattr(args, "device") and args.command != "dump-gbl-metadata":
        parser.error("Missing option '--device'")

    if args.command == "flash" and hasattr(args, "profile"):
        incompatible_args = []

        if hasattr(args, "probe_methods"):
            incompatible_args.append("--probe-methods")

        if hasattr(args, "bootloader_reset"):
            incompatible_args.append("--bootloader-reset")

        if incompatible_args:
            parser.error(
                "--profile cannot be used with " + ", ".join(incompatible_args)
            )

        flasher_cls = DEVICE_SPECIFIC_FLASHERS[args.profile]
        flasher: BaseFlasher = flasher_cls(device=args.device)
    else:
        flasher = Flasher(
            device=getattr(args, "device", None),
            probe_methods=list(getattr(args, "probe_methods", DEFAULT_PROBE_METHODS)),
            bootloader_reset=tuple(getattr(args, "bootloader_reset", [])),
        )

    if args.command == "dump-gbl-metadata":
        await _cmd_dump_gbl_metadata(args)
    elif args.command == "probe":
        assert isinstance(flasher, Flasher)
        await _cmd_probe(flasher)
    elif args.command == "write-ieee":
        assert isinstance(flasher, Flasher)
        await _cmd_write_ieee(args, flasher)
    elif args.command == "flash":
        await _cmd_flash(args, flasher, getattr(args, "verbose", 0))


async def _cmd_dump_gbl_metadata(args: argparse.Namespace) -> None:
    try:
        firmware_data, firmware_name = await _load_firmware_data(args.firmware)
    except (OSError, aiohttp.ClientResponseError) as e:
        print(f"Error: Failed to load firmware {args.firmware!r}: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        fw_image = parse_firmware_image(firmware_data)
    except zigpy.ota.validators.ValidationError as e:
        print(
            f"Error: {firmware_name!r} does not appear to be a valid firmware"
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
    args: argparse.Namespace, flasher: BaseFlasher, verbosity: int
) -> None:
    try:
        firmware_data, firmware_name = await _load_firmware_data(args.firmware)
    except (OSError, aiohttp.ClientResponseError) as e:
        print(f"Error: Failed to load firmware {args.firmware!r}: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        fw_image = parse_firmware_image(firmware_data)
    except (zigpy.ota.validators.ValidationError, ValueError) as e:
        print(
            f"Error: {firmware_name!r} does not appear to be a valid firmware"
            f" image: {e!r}",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        metadata = fw_image.get_nabucasa_metadata()
    except Exception as exc:
        _LOGGER.info(f"Failed to read firmware metadata: {exc!r}")
        metadata = None
    else:
        _LOGGER.info("Extracted GBL metadata: %s", metadata)

    try:
        await flasher.probe_app_type()
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    await flasher.enter_bootloader()

    with tqdm.tqdm(
        total=len(firmware_data),
        desc=os.path.basename(firmware_name),
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
