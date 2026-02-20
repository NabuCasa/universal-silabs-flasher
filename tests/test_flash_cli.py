"""CLI integration tests to ensure argument parsing works correctly."""

import io
from unittest.mock import AsyncMock, patch

import pytest

from universal_silabs_flasher.const import (
    DEFAULT_PROBE_METHODS,
    ApplicationType,
    ResetTarget,
)
from universal_silabs_flasher.flash import main
from universal_silabs_flasher.flasher import Flasher


def invoke_main(argv, *, catch_exit=True):
    """Invoke main() with the given argv, capturing stdout/stderr."""
    stdout = io.StringIO()
    stderr = io.StringIO()
    exit_code = 0
    captured_flasher = None

    original_init = Flasher.__init__

    def capture_init(self, **kwargs):
        nonlocal captured_flasher
        original_init(self, **kwargs)
        captured_flasher = self

    try:
        with (
            patch("universal_silabs_flasher.flasher.Flasher.__init__", capture_init),
            patch("sys.stdout", stdout),
            patch("sys.stderr", stderr),
        ):
            main(argv)
    except SystemExit as e:
        if not catch_exit:
            raise
        exit_code = e.code if e.code is not None else 0

    result = type(
        "Result",
        (),
        {
            "exit_code": exit_code,
            "output": stdout.getvalue(),
            "stderr": stderr.getvalue(),
            "flasher": captured_flasher,
        },
    )()
    return result


@pytest.fixture
def mock_connections():
    """Mock network connections to prevent actual hardware communication."""

    async def mock_probe_app_type(self):
        self.app_type = ApplicationType.EZSP
        self.app_version = None

    with (
        patch("universal_silabs_flasher.flasher.connect_protocol"),
        patch("universal_silabs_flasher.flasher.Flasher._connect_ezsp"),
        patch(
            "universal_silabs_flasher.flasher.Flasher.probe_app_type",
            mock_probe_app_type,
        ),
        patch(
            "universal_silabs_flasher.flasher.Flasher.dump_emberznet_config",
            new_callable=AsyncMock,
        ),
        patch(
            "universal_silabs_flasher.flasher.Flasher.write_emberznet_eui64",
            new_callable=AsyncMock,
        ),
        patch(
            "universal_silabs_flasher.flasher.Flasher.enter_bootloader",
            new_callable=AsyncMock,
        ),
        patch(
            "universal_silabs_flasher.flasher.Flasher.flash_firmware",
            new_callable=AsyncMock,
        ),
        patch(
            "universal_silabs_flasher.flash._parse_serial_port",
            side_effect=lambda v: v,
        ),
    ):
        yield


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
        # With custom bootloader baudrate (deprecated flag)
        (
            [
                "--device",
                "/dev/ttyUSB0",
                "--bootloader-baudrate",
                "115200",
                "flash",
                "--firmware",
                "tests/firmwares/skyconnect_zigbee_ncp_7.4.4.0.gbl",
            ],
            "/dev/ttyUSB0",
            [
                (ApplicationType.GECKO_BOOTLOADER, 115200),
                (ApplicationType.CPC, 460800),
                (ApplicationType.CPC, 115200),
                (ApplicationType.CPC, 230400),
                (ApplicationType.EZSP, 115200),
                (ApplicationType.EZSP, 460800),
                (ApplicationType.ROUTER, 115200),
                (ApplicationType.SPINEL, 460800),
            ],
            [],
        ),
        # With multiple custom baudrates (deprecated flags)
        (
            [
                "--device",
                "/dev/ttyUSB0",
                "--bootloader-baudrate",
                "115200,230400",
                "--ezsp-baudrate",
                "115200",
                "flash",
                "--firmware",
                "tests/firmwares/skyconnect_zigbee_ncp_7.4.4.0.gbl",
            ],
            "/dev/ttyUSB0",
            [
                (ApplicationType.GECKO_BOOTLOADER, 115200),
                (ApplicationType.GECKO_BOOTLOADER, 230400),
                (ApplicationType.CPC, 460800),
                (ApplicationType.CPC, 115200),
                (ApplicationType.CPC, 230400),
                (ApplicationType.EZSP, 115200),
                (ApplicationType.ROUTER, 115200),
                (ApplicationType.SPINEL, 460800),
            ],
            [],
        ),
        # With custom probe methods (deprecated flag)
        (
            [
                "--device",
                "/dev/ttyUSB0",
                "--probe-method",
                "ezsp",
                "--probe-method",
                "cpc",
                "flash",
                "--firmware",
                "tests/firmwares/skyconnect_zigbee_ncp_7.4.4.0.gbl",
            ],
            "/dev/ttyUSB0",
            [
                (ApplicationType.EZSP, 115200),
                (ApplicationType.EZSP, 460800),
                (ApplicationType.CPC, 460800),
                (ApplicationType.CPC, 115200),
                (ApplicationType.CPC, 230400),
            ],
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
def test_flash_command_argument_parsing(
    mock_connections,
    args,
    expected_device,
    expected_probe_methods,
    expected_reset,
):
    """Test that flash command correctly parses various argument combinations."""
    result = invoke_main(args + ["--force"])

    assert result.exit_code == 0
    assert result.flasher is not None

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
def test_probe_command_argument_parsing(mock_connections, args, expected_device):
    """Test that probe command correctly parses arguments."""
    result = invoke_main(args)

    assert result.exit_code == 0
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
                "true",
            ],
            "11:22:33:44:55:66:77:88",
            True,
        ),
    ],
)
def test_write_ieee_command_argument_parsing(
    mock_connections, args, expected_ieee, expected_force
):
    """Test that write-ieee command correctly parses arguments."""
    with patch(
        "universal_silabs_flasher.flasher.Flasher.write_emberznet_eui64",
        new_callable=AsyncMock,
    ) as mock_write:
        result = invoke_main(args)

        assert result.exit_code == 0

        mock_write.assert_called_once()
        call_args = mock_write.call_args

        assert str(call_args.args[0]) == expected_ieee
        assert call_args.kwargs["force"] == expected_force


def test_dump_gbl_metadata_command():
    """Test that dump-gbl-metadata command works without --device."""
    result = invoke_main(
        [
            "dump-gbl-metadata",
            "--firmware",
            "tests/firmwares/skyconnect_zigbee_ncp_7.4.4.0.gbl",
        ]
    )

    assert result.exit_code == 0
    assert '{"' in result.output or result.output.strip().endswith("null")


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
    ],
)
def test_invalid_argument_combinations_with_mocked_device(
    args, expected_error_fragment
):
    """Test invalid argument combinations with mocked device validator."""
    with patch(
        "universal_silabs_flasher.flash._parse_serial_port", side_effect=lambda v: v
    ):
        result = invoke_main(args)

    assert result.exit_code != 0
    combined = result.output.lower() + result.stderr.lower()
    assert expected_error_fragment.lower() in combined


@pytest.mark.parametrize(
    "args,expected_error_fragment",
    [
        # Invalid device scheme
        (
            [
                "--device",
                "http://example.com",
                "flash",
                "--firmware",
                "tests/firmwares/skyconnect_zigbee_ncp_7.4.4.0.gbl",
            ],
            "invalid URL scheme",
        ),
        # Missing device for flash
        (
            [
                "flash",
                "--firmware",
                "tests/firmwares/skyconnect_zigbee_ncp_7.4.4.0.gbl",
            ],
            "Missing option",
        ),
        # Missing device for probe
        (
            ["probe"],
            "Missing option",
        ),
    ],
)
def test_invalid_argument_combinations_without_mocked_device(
    args, expected_error_fragment
):
    """Test invalid argument combinations without mocked device validator."""
    result = invoke_main(args)

    assert result.exit_code != 0
    combined = result.output.lower() + result.stderr.lower()
    assert expected_error_fragment.lower() in combined


@pytest.mark.parametrize(
    "args",
    [
        [
            "-v",
            "--device",
            "/dev/ttyUSB0",
            "flash",
            "--firmware",
            "tests/firmwares/skyconnect_zigbee_ncp_7.4.4.0.gbl",
            "--force",
        ],
        [
            "-vv",
            "--device",
            "/dev/ttyUSB0",
            "flash",
            "--firmware",
            "tests/firmwares/skyconnect_zigbee_ncp_7.4.4.0.gbl",
            "--force",
            "--ensure-exact-version",
        ],
        [
            "--device",
            "/dev/ttyUSB0",
            "flash",
            "--firmware",
            "tests/firmwares/skyconnect_zigbee_ncp_7.4.4.0.gbl",
            "--force",
            "--allow-downgrades",
        ],
        [
            "--device",
            "/dev/ttyUSB0",
            "flash",
            "--firmware",
            "tests/firmwares/skyconnect_zigbee_ncp_7.4.4.0.gbl",
            "--force",
            "--allow-cross-flashing",
        ],
        [
            "--device",
            "/dev/ttyUSB0",
            "flash",
            "--firmware",
            "tests/firmwares/skyconnect_zigbee_ncp_7.4.4.0.gbl",
            "--force",
            "--allow-downgrades",
            "--ensure-exact-version",
        ],
    ],
)
def test_flash_command_flags(mock_connections, args):
    """Test that flash command boolean flags are parsed correctly."""
    result = invoke_main(args)

    assert result.exit_code == 0


@pytest.mark.parametrize(
    "args,expected_reset_target",
    [
        (
            [
                "--device",
                "/dev/ttyUSB0",
                "flash",
                "--firmware",
                "tests/firmwares/skyconnect_zigbee_ncp_7.4.4.0.gbl",
                "--force",
                "--yellow-gpio-reset",
            ],
            [ResetTarget.YELLOW],
        ),
        (
            [
                "--device",
                "/dev/ttyUSB0",
                "flash",
                "--firmware",
                "tests/firmwares/skyconnect_zigbee_ncp_7.4.4.0.gbl",
                "--force",
                "--sonoff-reset",
            ],
            [ResetTarget.RTS_DTR],
        ),
    ],
)
def test_deprecated_reset_flags(mock_connections, args, expected_reset_target):
    """Test deprecated reset flags set reset targets correctly."""
    result = invoke_main(args)

    assert result.exit_code == 0
    assert result.flasher._reset_targets == expected_reset_target
