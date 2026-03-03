"""CLI integration tests to ensure argument parsing works correctly."""

from dataclasses import dataclass
import io
from pathlib import Path
from unittest.mock import AsyncMock, patch
import zipfile

from aioresponses import aioresponses
import pytest

from universal_silabs_flasher.const import (
    DEFAULT_PROBE_METHODS,
    ApplicationType,
    ResetTarget,
)
from universal_silabs_flasher.flash import main
from universal_silabs_flasher.flasher import BaseFlasher, Flasher, Zbt2Flasher

FIRMWARE_URL = "https://example.com/firmware/skyconnect_zigbee_ncp_7.4.4.0.gbl"
FIRMWARE_PATH = Path("tests/firmwares/skyconnect_zigbee_ncp_7.4.4.0.gbl")


@dataclass
class Result:
    exit_code: str | int
    output: str
    stderr: str
    flasher: BaseFlasher | None


async def invoke_main(argv: list[str]) -> Result:
    """Invoke main() with the given argv, capturing stdout/stderr."""
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code: str | int = 0
    captured_flasher = None

    original_init = BaseFlasher.__init__

    def capture_init(self, **kwargs):
        nonlocal captured_flasher
        original_init(self, **kwargs)
        captured_flasher = self

    async def mock_probe_app_type(self):
        self.app_type = ApplicationType.EZSP
        self.app_version = None

    try:
        with (
            patch(
                "universal_silabs_flasher.flasher.BaseFlasher.__init__", capture_init
            ),
            patch("sys.stdout", stdout),
            patch("sys.stderr", stderr),
            patch(
                "universal_silabs_flasher.flasher.BaseFlasher.probe_app_type",
                mock_probe_app_type,
            ),
            patch(
                "universal_silabs_flasher.flasher.Flasher.dump_emberznet_config",
                new_callable=AsyncMock,
            ),
            patch(
                "universal_silabs_flasher.flasher.BaseFlasher.enter_bootloader",
                new_callable=AsyncMock,
            ),
            patch(
                "universal_silabs_flasher.flasher.BaseFlasher.flash_firmware",
                new_callable=AsyncMock,
            ),
        ):
            await main(argv)
    except SystemExit as e:
        exit_code = e.code if e.code is not None else 0

    return Result(
        exit_code=exit_code,
        output=stdout.getvalue(),
        stderr=stderr.getvalue(),
        flasher=captured_flasher,
    )


@pytest.mark.parametrize(
    "args,expected_device,expected_probe_methods,expected_reset",
    [
        # Basic flash command (uses defaults)
        (
            [
                "--device",
                "/dev/ttyUSB0",
                "flash",
                "--firmware",
                "tests/firmwares/skyconnect_zigbee_ncp_7.4.4.0.gbl",
            ],
            "/dev/ttyUSB0",
            DEFAULT_PROBE_METHODS,
            [],
        ),
        # With verbose flags (uses defaults)
        (
            [
                "-vvv",
                "--device",
                "/dev/ttyUSB1",
                "flash",
                "--firmware",
                "tests/firmwares/skyconnect_zigbee_ncp_7.4.4.0.gbl",
            ],
            "/dev/ttyUSB1",
            DEFAULT_PROBE_METHODS,
            [],
        ),
        # With single bootloader reset method
        (
            [
                "--device",
                "/dev/ttyUSB0",
                "--bootloader-reset",
                "rts_dtr",
                "flash",
                "--firmware",
                "tests/firmwares/skyconnect_zigbee_ncp_7.4.4.0.gbl",
            ],
            "/dev/ttyUSB0",
            DEFAULT_PROBE_METHODS,
            [ResetTarget.RTS_DTR],
        ),
        # With multiple bootloader reset methods (chained)
        (
            [
                "--device",
                "/dev/ttyUSB0",
                "--bootloader-reset",
                "rts_dtr,baudrate",
                "flash",
                "--firmware",
                "tests/firmwares/skyconnect_zigbee_ncp_7.4.4.0.gbl",
            ],
            "/dev/ttyUSB0",
            DEFAULT_PROBE_METHODS,
            [ResetTarget.RTS_DTR, ResetTarget.BAUDRATE],
        ),
        # With socket device
        (
            [
                "--device",
                "socket://192.168.1.100:1234",
                "flash",
                "--firmware",
                "tests/firmwares/skyconnect_zigbee_ncp_7.4.4.0.gbl",
            ],
            "socket://192.168.1.100:1234",
            DEFAULT_PROBE_METHODS,
            [],
        ),
    ],
)
async def test_flash_command_argument_parsing(
    args,
    expected_device,
    expected_probe_methods,
    expected_reset,
):
    """Test that flash command correctly parses various argument combinations."""
    result = await invoke_main(args)

    assert result.exit_code == 0
    assert isinstance(result.flasher, Flasher)

    assert result.flasher._device == expected_device
    assert set(result.flasher._probe_methods) == set(expected_probe_methods)
    assert result.flasher._reset_targets == expected_reset


@pytest.mark.parametrize(
    "args,expected_device",
    [
        (["--device", "/dev/ttyUSB0", "probe"], "/dev/ttyUSB0"),
        (["-v", "--device", "/dev/ttyUSB1", "probe"], "/dev/ttyUSB1"),
        (["--device", "socket://localhost:5000", "probe"], "socket://localhost:5000"),
    ],
)
async def test_probe_command_argument_parsing(args, expected_device):
    """Test that probe command correctly parses arguments."""
    result = await invoke_main(args)

    assert result.exit_code == 0
    assert result.flasher is not None
    assert result.flasher._device == expected_device


@pytest.mark.parametrize(
    "args,expected_ieee,expected_force",
    [
        (
            [
                "--device",
                "/dev/ttyUSB0",
                "write-ieee",
                "--ieee",
                "11:22:33:44:55:66:77:88",
            ],
            "11:22:33:44:55:66:77:88",
            False,
        ),
        (
            [
                "--device",
                "/dev/ttyUSB0",
                "write-ieee",
                "--ieee",
                "11:22:33:44:55:66:77:88",
                "--force",
            ],
            "11:22:33:44:55:66:77:88",
            True,
        ),
    ],
)
async def test_write_ieee_command_argument_parsing(args, expected_ieee, expected_force):
    """Test that write-ieee command correctly parses arguments."""
    with patch(
        "universal_silabs_flasher.flasher.Flasher.write_emberznet_eui64",
        new_callable=AsyncMock,
    ) as mock_write:
        result = await invoke_main(args)

        assert result.exit_code == 0

        mock_write.assert_called_once()
        call_args = mock_write.call_args

        assert str(call_args.args[0]) == expected_ieee
        assert call_args.kwargs["force"] == expected_force


async def test_dump_gbl_metadata_command():
    """Test that dump-gbl-metadata command works without --device."""
    result = await invoke_main(
        [
            "dump-gbl-metadata",
            "--firmware",
            "tests/firmwares/skyconnect_zigbee_ncp_7.4.4.0.gbl",
        ]
    )

    assert result.exit_code == 0
    assert '{"' in result.output or result.output.strip().endswith("null")


async def test_flash_profile_uses_registered_flasher():
    result = await invoke_main(
        [
            "--device",
            "/dev/ttyUSB0",
            "flash",
            "--profile",
            "zbt2",
            "--firmware",
            "tests/firmwares/skyconnect_zigbee_ncp_7.4.4.0.gbl",
        ]
    )

    assert result.exit_code == 0
    assert isinstance(result.flasher, Zbt2Flasher)


@pytest.mark.parametrize(
    "args,expected_error_fragment",
    [
        # Invalid bootloader reset method
        (
            [
                "--device",
                "/dev/ttyUSB0",
                "--bootloader-reset",
                "invalid_method",
                "flash",
                "--firmware",
                "tests/firmwares/skyconnect_zigbee_ncp_7.4.4.0.gbl",
            ],
            "invalid",
        ),
        # Invalid bootloader reset method in chain
        (
            [
                "--device",
                "/dev/ttyUSB0",
                "--bootloader-reset",
                "rts_dtr,invalid_method",
                "flash",
                "--firmware",
                "tests/firmwares/skyconnect_zigbee_ncp_7.4.4.0.gbl",
            ],
            "invalid",
        ),
        # Invalid probe method
        (
            [
                "--device",
                "/dev/ttyUSB0",
                "--probe-method",
                "invalid_app",
                "flash",
                "--firmware",
                "tests/firmwares/skyconnect_zigbee_ncp_7.4.4.0.gbl",
            ],
            "invalid",
        ),
        # Missing firmware for flash
        (
            ["--device", "/dev/ttyUSB0", "flash"],
            "required",
        ),
        # Missing IEEE for write-ieee
        (
            ["--device", "/dev/ttyUSB0", "write-ieee"],
            "required",
        ),
        # Removed --baudrate flag
        (
            [
                "--device",
                "/dev/ttyUSB0",
                "--baudrate",
                "115200",
                "flash",
                "--firmware",
                "tests/firmwares/skyconnect_zigbee_ncp_7.4.4.0.gbl",
            ],
            "error",
        ),
        (
            [
                "--device",
                "/dev/ttyUSB0",
                "--probe-methods",
                "ezsp:115200",
                "flash",
                "--profile",
                "zbt2",
                "--firmware",
                "tests/firmwares/skyconnect_zigbee_ncp_7.4.4.0.gbl",
            ],
            "--profile cannot be used",
        ),
        (
            [
                "--device",
                "/dev/ttyUSB0",
                "--bootloader-reset",
                "rts_dtr",
                "flash",
                "--profile",
                "zbt2",
                "--firmware",
                "tests/firmwares/skyconnect_zigbee_ncp_7.4.4.0.gbl",
            ],
            "--profile cannot be used",
        ),
    ],
)
async def test_invalid_argument_combinations_with_mocked_device(
    args, expected_error_fragment
):
    """Test invalid argument combinations with mocked device validator."""
    result = await invoke_main(args)

    assert result.exit_code != 0
    combined = result.output.lower() + result.stderr.lower()
    assert expected_error_fragment.lower() in combined


def make_zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()

    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)

    return buf.getvalue()


async def test_firmware_download_gbl():
    """A plain .gbl downloaded over HTTP is flashed successfully."""
    with aioresponses() as m:
        m.get(FIRMWARE_URL, status=200, body=FIRMWARE_PATH.read_bytes())
        result = await invoke_main(
            ["--device", "/dev/ttyUSB0", "flash", "--firmware", FIRMWARE_URL]
        )

    assert result.exit_code == 0


async def test_firmware_download_zip_picks_gbl():
    """A ZIP with a .gbl and another file: the .gbl entry is extracted and flashed."""
    zip_url = "https://example.com/firmware/update.zip"
    zip_bytes = make_zip(
        {
            "readme.txt": b"hello",
            "firmware.gbl": FIRMWARE_PATH.read_bytes(),
        }
    )

    with aioresponses() as m:
        m.get(zip_url, status=200, body=zip_bytes)
        result = await invoke_main(
            ["--device", "/dev/ttyUSB0", "flash", "--firmware", zip_url]
        )

    assert result.exit_code == 0


async def test_firmware_download_failure():
    """An HTTP error status is reported as a CLI error."""
    with aioresponses() as m:
        m.get(FIRMWARE_URL, status=404)
        result = await invoke_main(
            ["--device", "/dev/ttyUSB0", "flash", "--firmware", FIRMWARE_URL]
        )

    assert result.exit_code != 0
    assert "error" in result.stderr.lower()
