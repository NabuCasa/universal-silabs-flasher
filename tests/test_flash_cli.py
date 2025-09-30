"""CLI integration tests to ensure argument parsing works correctly."""

from unittest.mock import AsyncMock, patch

import click
import click.core
from click.testing import CliRunner
import pytest

from universal_silabs_flasher.const import ApplicationType, ResetTarget
from universal_silabs_flasher.flash import main


class CtxCliRunner(CliRunner):
    """CliRunner that captures the Click context in the result."""

    def invoke(self, cli, *args, **kwargs):
        captured = None
        original_make_context = click.core.Command.make_context

        def make_context_and_capture(
            cmd_self, info_name, cmd_args, parent=None, **extra
        ):
            nonlocal captured

            ctx = original_make_context(cmd_self, info_name, cmd_args, parent, **extra)
            if parent is None:
                captured = ctx

            return ctx

        with patch.object(click.core.Command, "make_context", make_context_and_capture):
            result = super().invoke(cli, *args, **kwargs)

        result.ctx = captured

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
            "universal_silabs_flasher.flash.SerialPort.convert",
            side_effect=lambda v, *_: v,
        ),
    ):
        yield


@pytest.mark.parametrize(
    "args,expected_device,expected_baudrate_overrides,expected_probe_methods,expected_reset",
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
            {},
            [
                ApplicationType.GECKO_BOOTLOADER,
                ApplicationType.CPC,
                ApplicationType.EZSP,
                ApplicationType.SPINEL,
                ApplicationType.ROUTER,
            ],
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
            {},
            [
                ApplicationType.GECKO_BOOTLOADER,
                ApplicationType.CPC,
                ApplicationType.EZSP,
                ApplicationType.SPINEL,
                ApplicationType.ROUTER,
            ],
            [],
        ),
        # With custom bootloader baudrate
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
            {ApplicationType.GECKO_BOOTLOADER: [115200]},
            [
                ApplicationType.GECKO_BOOTLOADER,
                ApplicationType.CPC,
                ApplicationType.EZSP,
                ApplicationType.SPINEL,
                ApplicationType.ROUTER,
            ],
            [],
        ),
        # With multiple custom baudrates
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
            {
                ApplicationType.GECKO_BOOTLOADER: [115200, 230400],
                ApplicationType.EZSP: [115200],
            },
            [
                ApplicationType.GECKO_BOOTLOADER,
                ApplicationType.CPC,
                ApplicationType.EZSP,
                ApplicationType.SPINEL,
                ApplicationType.ROUTER,
            ],
            [],
        ),
        # With custom probe methods
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
            {},
            [ApplicationType.EZSP, ApplicationType.CPC],
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
            {},
            [
                ApplicationType.GECKO_BOOTLOADER,
                ApplicationType.CPC,
                ApplicationType.EZSP,
                ApplicationType.SPINEL,
                ApplicationType.ROUTER,
            ],
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
            {},
            [
                ApplicationType.GECKO_BOOTLOADER,
                ApplicationType.CPC,
                ApplicationType.EZSP,
                ApplicationType.SPINEL,
                ApplicationType.ROUTER,
            ],
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
            {},
            [
                ApplicationType.GECKO_BOOTLOADER,
                ApplicationType.CPC,
                ApplicationType.EZSP,
                ApplicationType.SPINEL,
                ApplicationType.ROUTER,
            ],
            [],
        ),
    ],
)
def test_flash_command_argument_parsing(
    mock_connections,
    args,
    expected_device,
    expected_baudrate_overrides,
    expected_probe_methods,
    expected_reset,
):
    """Test that flash command correctly parses various argument combinations."""
    runner = CtxCliRunner()
    result = runner.invoke(main, args + ["--force"], catch_exceptions=False)

    assert result.exit_code == 0
    assert result.ctx is not None

    flasher = result.ctx.obj["flasher"]
    assert flasher._device == expected_device

    # Check baudrate overrides
    for app_type, baudrates in expected_baudrate_overrides.items():
        assert flasher._baudrates[app_type] == baudrates

    assert set(flasher._probe_methods) == set(expected_probe_methods)
    assert flasher._reset_targets == expected_reset


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
    runner = CtxCliRunner()
    result = runner.invoke(main, args, catch_exceptions=False)

    assert result.exit_code == 0
    assert result.ctx is not None

    flasher = result.ctx.obj["flasher"]
    assert flasher._device == expected_device


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
    runner = CliRunner()

    with patch(
        "universal_silabs_flasher.flasher.Flasher.write_emberznet_eui64",
        new_callable=AsyncMock,
    ) as mock_write:
        result = runner.invoke(main, args, catch_exceptions=False)

        assert result.exit_code == 0

        mock_write.assert_called_once()
        call_args = mock_write.call_args

        assert str(call_args.args[0]) == expected_ieee
        assert call_args.kwargs["force"] == expected_force


def test_dump_gbl_metadata_command():
    """Test that dump-gbl-metadata command works without --device."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "dump-gbl-metadata",
            "--firmware",
            "tests/firmwares/skyconnect_zigbee_ncp_7.4.4.0.gbl",
        ],
        catch_exceptions=False,
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
            "Missing option",
        ),
        # Missing IEEE for write-ieee
        (
            ["--device", "/dev/ttyUSB0", "write-ieee"],
            "Missing option",
        ),
        # Deprecated --baudrate flag
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
            "deprecated",
        ),
    ],
)
def test_invalid_argument_combinations_with_mocked_device(
    args, expected_error_fragment
):
    """Test invalid argument combinations with mocked device validator."""
    runner = CliRunner()

    with patch(
        "universal_silabs_flasher.flash.SerialPort.convert", side_effect=lambda v, *_: v
    ):
        result = runner.invoke(main, args)

    assert result.exit_code != 0
    assert expected_error_fragment.lower() in result.output.lower()


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
    runner = CliRunner()
    result = runner.invoke(main, args)

    assert result.exit_code != 0
    assert expected_error_fragment.lower() in result.output.lower()


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
    runner = CliRunner()
    result = runner.invoke(main, args, catch_exceptions=False)

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
    runner = CtxCliRunner()
    result = runner.invoke(main, args, catch_exceptions=False)

    assert result.exit_code == 0
    assert result.ctx is not None

    flasher = result.ctx.obj["flasher"]
    assert flasher._reset_targets == expected_reset_target
