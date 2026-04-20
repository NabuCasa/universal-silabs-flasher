import asyncio
from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
import zigpy.serial
import zigpy.types as t

from universal_silabs_flasher.common import Version
from universal_silabs_flasher.const import (
    RESET_CONFIGS,
    ApplicationType,
    BaudrateResetConfig,
    GpioPattern,
    GpioResetConfig,
    ModemPinPattern,
    ModemPinResetConfig,
    ResetTarget,
)
from universal_silabs_flasher.flasher import (
    Flasher,
    ProbeResult,
    YellowFlasher,
    Zbt1Flasher,
    Zbt2Flasher,
)
from universal_silabs_flasher.gecko_bootloader import GeckoBootloaderProtocol


@pytest.fixture(autouse=True)
def reduce_timeouts() -> Generator[None, None, None]:
    with patch(
        "universal_silabs_flasher.flasher.BaseFlasher._bootloader_launch_delay", 0.05
    ):
        yield


async def test_write_emberznet_eui64():
    flasher = Flasher(device="/dev/ttyMOCK")

    with (
        patch.object(
            flasher, "probe_gecko_bootloader", side_effect=asyncio.TimeoutError
        ),
        patch.object(
            flasher,
            "probe_ezsp",
            return_value=ProbeResult(
                version=Version("7.4.4.0 build 0"),
                continue_probing=False,
                baudrate=115200,
            ),
        ),
        patch.object(flasher, "_connect_ezsp") as mock_connect_ezsp,
    ):
        ezsp = mock_connect_ezsp.return_value.__aenter__.return_value

        ezsp.getEui64.return_value = (t.EUI64.convert("00:11:22:33:44:55:66:77"),)
        ezsp.write_custom_eui64.return_value = None

        await flasher.write_emberznet_eui64(
            new_ieee=t.EUI64.convert("11:22:33:44:55:66:77:88"), force=True
        )

    assert ezsp.write_custom_eui64.mock_calls == [
        call(ieee=t.EUI64.convert("11:22:33:44:55:66:77:88"), burn_into_userdata=True)
    ]


async def test_baudrate_reset_pattern():
    flasher = Flasher(device="/dev/ttyMOCK", bootloader_reset=(ResetTarget.BAUDRATE,))

    with patch(
        "universal_silabs_flasher.flasher.connect_protocol"
    ) as mock_connect_protocol:
        mock_uart = mock_connect_protocol.return_value.__aenter__.return_value
        mock_uart._transport.write = MagicMock()
        await flasher.trigger_bootloader_reset(run_firmware=False)

    assert mock_connect_protocol.mock_calls == [
        # Connect with 150 baud
        call("/dev/ttyMOCK", 150, zigpy.serial.SerialProtocol),
        call().__aenter__(),
        call().__aexit__(None, None, None),
        # Connect with 300 baud
        call("/dev/ttyMOCK", 300, zigpy.serial.SerialProtocol),
        call().__aenter__(),
        call().__aexit__(None, None, None),
        # Connect with 1200 baud
        call("/dev/ttyMOCK", 1200, zigpy.serial.SerialProtocol),
        call().__aenter__(),
        call().__aenter__()._transport.write(b"BZ"),
        call().__aexit__(None, None, None),
        # Probe
        call("/dev/ttyMOCK", 115200, GeckoBootloaderProtocol),
        call().__aenter__(),
        call().__aenter__().probe(),
        call().__aexit__(None, None, None),
    ]


async def test_trigger_bootloader_reset_first_probe_succeeds():
    flasher = Flasher(
        device="/dev/ttyMOCK",
        bootloader_reset=(ResetTarget.RTS_DTR, ResetTarget.BAUDRATE),
    )

    with (
        patch.object(flasher, "_trigger_baudrate_reset") as mock_trigger_baudrate,
        patch.object(flasher, "_trigger_modem_pin_reset") as mock_trigger_modem_pin,
        patch.object(
            flasher,
            "probe_gecko_bootloader",
            return_value=ProbeResult(
                version=Version("1.0.0"),
                continue_probing=False,
                baudrate=115200,
            ),
        ) as mock_probe,
    ):
        result = await flasher.trigger_bootloader_reset(run_firmware=False)

    assert result is not None
    assert result.version == Version("1.0.0")
    assert result.baudrate == 115200

    # All reset targets are triggered upfront
    assert len(mock_trigger_baudrate.mock_calls) == 1
    assert len(mock_trigger_modem_pin.mock_calls) == 1
    # Only one probe attempt since the first one succeeds
    assert mock_probe.mock_calls == [call(run_firmware=False, baudrate=115200)]


async def test_trigger_bootloader_reset_all_probes_fail():
    flasher = Flasher(
        device="/dev/ttyMOCK",
        bootloader_reset=(ResetTarget.RTS_DTR, ResetTarget.BAUDRATE),
    )

    with (
        patch.object(flasher, "_trigger_baudrate_reset") as mock_trigger_baudrate,
        patch.object(flasher, "_trigger_modem_pin_reset") as mock_trigger_modem_pin,
        patch.object(
            flasher,
            "probe_gecko_bootloader",
            side_effect=asyncio.TimeoutError,  # All probes fail
        ) as mock_probe,
    ):
        result = await flasher.trigger_bootloader_reset(run_firmware=False)

    assert result is None

    # All reset targets are triggered upfront
    assert len(mock_trigger_baudrate.mock_calls) == 1
    assert len(mock_trigger_modem_pin.mock_calls) == 1
    # One probe attempt at the only bootloader baudrate
    assert mock_probe.mock_calls == [
        call(run_firmware=False, baudrate=115200),
    ]


async def test_probe_app_type_fallback_to_bootloader() -> None:
    flasher = Flasher(device="/dev/ttyMOCK", bootloader_reset=(ResetTarget.RTS_DTR,))

    bootloader_result = ProbeResult(
        version=Version("1.0.0"),
        continue_probing=False,
        baudrate=115200,
    )

    with (
        patch.object(
            flasher, "trigger_bootloader_reset", return_value=bootloader_result
        ) as mock_trigger_reset,
        patch.object(
            flasher, "probe_gecko_bootloader", side_effect=asyncio.TimeoutError
        ),
        patch.object(flasher, "probe_cpc", side_effect=asyncio.TimeoutError),
        patch.object(flasher, "probe_ezsp", side_effect=asyncio.TimeoutError),
        patch.object(flasher, "probe_router", side_effect=asyncio.TimeoutError),
        patch.object(flasher, "probe_spinel", side_effect=asyncio.TimeoutError),
    ):
        await flasher.probe_app_type()

    # Should fallback to bootloader when no valid application found
    assert flasher.app_type == ApplicationType.GECKO_BOOTLOADER
    assert flasher.app_version == Version("1.0.0")
    assert flasher.app_baudrate == 115200
    assert flasher.bootloader_baudrate == 115200

    # trigger_bootloader_reset should be called twice - once at start and once
    # for fallback
    assert mock_trigger_reset.call_count == 2


async def test_device_specific_probe_app_type_does_not_require_reset_targets() -> None:
    flasher = Zbt2Flasher(device="/dev/ttyMOCK")

    bootloader_result = ProbeResult(
        version=Version("1.0.0"),
        continue_probing=False,
        baudrate=115200,
    )

    with patch.object(
        flasher,
        "trigger_bootloader_reset",
        side_effect=[bootloader_result, bootloader_result],
    ) as mock_trigger_reset:
        await flasher.probe_app_type()

    assert flasher.app_type == ApplicationType.GECKO_BOOTLOADER
    assert flasher.app_version == Version("1.0.0")
    assert flasher.app_baudrate == 115200
    assert flasher.bootloader_baudrate == 115200
    assert mock_trigger_reset.mock_calls == [
        call(run_firmware=True),
        call(run_firmware=False),
    ]


async def test_flasher_init_string_bootloader_reset():
    flasher = Flasher(device="/dev/ttyMOCK", bootloader_reset="yellow")

    assert flasher._reset_targets == [ResetTarget.YELLOW]


async def test_trigger_gpio_reset_cp210x():
    flasher = Flasher(device="/dev/ttyMOCK")

    config = GpioResetConfig(
        chip=None,
        chip_type="cp210x",
        pattern=[GpioPattern(pins={4: True, 5: True}, delay_after=0.1)],
    )

    with (
        patch(
            "universal_silabs_flasher.flasher.find_gpiochip_by_label",
            return_value="/dev/gpiochip_mock",
        ) as mock_find,
        patch("universal_silabs_flasher.flasher.send_gpio_pattern") as mock_send,
    ):
        await flasher._trigger_gpio_reset(config)

    assert mock_find.mock_calls == [call("cp210x")]
    assert mock_send.mock_calls == [call("/dev/gpiochip_mock", config.pattern)]


async def test_trigger_modem_pin_reset():
    flasher = Flasher(device="/dev/ttyMOCK")

    config = ModemPinResetConfig(
        pattern=[
            ModemPinPattern(pins={"dtr": False, "rts": True}, delay_after=0.0),
            ModemPinPattern(pins={"dtr": True, "rts": False}, delay_after=0.0),
        ],
    )

    with patch(
        "universal_silabs_flasher.flasher.connect_protocol"
    ) as mock_connect_protocol:
        mock_uart = mock_connect_protocol.return_value.__aenter__.return_value
        mock_uart._transport.set_modem_pins = AsyncMock()
        await flasher._trigger_modem_pin_reset(config)

    assert mock_connect_protocol.mock_calls[0] == call(
        "/dev/ttyMOCK", 115200, zigpy.serial.SerialProtocol
    )
    assert mock_uart._transport.set_modem_pins.mock_calls == [
        call(dtr=False, rts=True),
        call(dtr=True, rts=False),
    ]


async def test_reset_config_flasher_trigger_bootloader_reset():
    flasher = YellowFlasher(device="/dev/ttyMOCK")

    assert flasher._can_trigger_bootloader_reset() is True

    probe_result = ProbeResult(
        version=Version("1.0.0"), continue_probing=False, baudrate=115200
    )

    with (
        patch.object(flasher, "_trigger_gpio_reset") as mock_gpio_reset,
        patch.object(
            flasher, "_detect_gecko_bootloader", return_value=probe_result
        ) as mock_detect,
    ):
        result = await flasher.trigger_bootloader_reset(run_firmware=False)

    assert result == probe_result
    assert len(mock_gpio_reset.mock_calls) == 1
    assert mock_detect.mock_calls == [call(run_firmware=False)]


async def test_zbt1_flasher():
    flasher = Zbt1Flasher(device="/dev/ttyMOCK")

    assert flasher._can_trigger_bootloader_reset() is False

    result = await flasher.trigger_bootloader_reset(run_firmware=False)
    assert result is None


async def test_zbt2_flasher_trigger_bootloader_reset_first_attempt_succeeds():
    flasher = Zbt2Flasher(device="/dev/ttyMOCK")

    probe_result = ProbeResult(
        version=Version("1.0.0"), continue_probing=False, baudrate=115200
    )

    with (
        patch.object(flasher, "_trigger_modem_pin_reset") as mock_modem_pin_reset,
        patch.object(flasher, "_trigger_baudrate_reset") as mock_baudrate_reset,
        patch.object(
            flasher, "_detect_gecko_bootloader", return_value=probe_result
        ) as mock_detect,
    ):
        result = await flasher.trigger_bootloader_reset(run_firmware=False)

    assert result == probe_result
    assert mock_modem_pin_reset.mock_calls == [call(RESET_CONFIGS[ResetTarget.RTS_DTR])]
    assert mock_baudrate_reset.mock_calls == [
        call(
            BaudrateResetConfig(
                baudrates=(150, 300, 1200),
                delay_after_each=0.1,
                delay_after_final=0.5,
                command=b"BZ",
            )
        )
    ]
    assert mock_detect.mock_calls == [call(run_firmware=False)]


async def test_zbt2_flasher_trigger_bootloader_reset_hard_reset_fallback():
    flasher = Zbt2Flasher(device="/dev/ttyMOCK")
    flasher._reconnect_timeout = 5

    probe_result = ProbeResult(
        version=Version("1.0.0"), continue_probing=False, baudrate=115200
    )

    with (
        patch.object(flasher, "_trigger_modem_pin_reset") as mock_modem_pin_reset,
        patch.object(
            flasher,
            "_trigger_baudrate_reset",
            side_effect=[
                # Call 1: BZ (initial) - succeeds
                None,
                # Call 2: RE (hard reset) - raises (device disconnects)
                ConnectionError("device disconnected"),
                # Call 3: BZ (reconnect attempt 1) - raises (not ready yet)
                ConnectionError("not ready yet"),
                # Call 4: BZ (reconnect attempt 2) - succeeds
                None,
            ],
        ) as mock_baudrate_reset,
        patch.object(
            flasher,
            "_detect_gecko_bootloader",
            side_effect=[None, probe_result],
        ) as mock_detect,
    ):
        result = await flasher.trigger_bootloader_reset(run_firmware=False)

    assert result == probe_result
    # RTS/DTR reset is only attempted once (before the first BZ)
    assert mock_modem_pin_reset.mock_calls == [call(RESET_CONFIGS[ResetTarget.RTS_DTR])]
    # 4 baudrate reset calls: BZ, RE(fail), BZ(fail), BZ(success)
    assert len(mock_baudrate_reset.mock_calls) == 4
    # 2 detect calls: first returns None, second returns result
    assert len(mock_detect.mock_calls) == 2


async def test_enter_bootloader_from_application():
    flasher = Flasher(device="/dev/ttyMOCK")
    flasher.app_type = ApplicationType.CPC
    flasher.app_baudrate = 115200

    probe_result = ProbeResult(
        version=Version("1.0.0"), continue_probing=False, baudrate=115200
    )

    with (
        patch.object(flasher, "trigger_bootloader_reset", return_value=None),
        patch.object(flasher, "_connect_cpc") as mock_connect_cpc,
        patch.object(flasher, "_detect_gecko_bootloader", return_value=probe_result),
    ):
        mock_cpc = mock_connect_cpc.return_value.__aenter__.return_value
        mock_cpc.enter_bootloader = AsyncMock()
        await flasher.enter_bootloader()

    assert flasher.bootloader_baudrate == 115200
    assert len(mock_cpc.enter_bootloader.mock_calls) == 1
