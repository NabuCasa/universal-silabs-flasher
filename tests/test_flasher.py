import asyncio
from unittest.mock import MagicMock, call, patch

import zigpy.types as t

from universal_silabs_flasher.common import FlowControlSerialProtocol, Version
from universal_silabs_flasher.const import ApplicationType, ResetTarget
from universal_silabs_flasher.flasher import Flasher, ProbeResult
from universal_silabs_flasher.gecko_bootloader import GeckoBootloaderProtocol


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
        await flasher.trigger_bootloader_reset()

    assert mock_connect_protocol.mock_calls == [
        # Connect with 150 baud
        call("/dev/ttyMOCK", 150, FlowControlSerialProtocol),
        call().__aenter__(),
        call().__aexit__(None, None, None),
        # Connect with 300 baud
        call("/dev/ttyMOCK", 300, FlowControlSerialProtocol),
        call().__aenter__(),
        call().__aexit__(None, None, None),
        # Connect with 1200 baud
        call("/dev/ttyMOCK", 1200, FlowControlSerialProtocol),
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
        patch.object(flasher, "trigger_bootloader") as mock_trigger,
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
        result = await flasher.trigger_bootloader_reset()

    assert result is not None
    assert result.version == Version("1.0.0")
    assert result.baudrate == 115200

    # Only first reset target should be attempted
    assert mock_trigger.mock_calls == [call(ResetTarget.RTS_DTR)]
    # Only one probe attempt for the first reset target
    assert mock_probe.mock_calls == [call(run_firmware=False, baudrate=115200)]


async def test_trigger_bootloader_reset_second_probe_succeeds():
    flasher = Flasher(
        device="/dev/ttyMOCK",
        bootloader_reset=(ResetTarget.RTS_DTR, ResetTarget.BAUDRATE),
    )

    with (
        patch.object(flasher, "trigger_bootloader") as mock_trigger,
        patch.object(
            flasher,
            "probe_gecko_bootloader",
            side_effect=[
                asyncio.TimeoutError,  # First reset target fails
                ProbeResult(  # Second reset target succeeds
                    version=Version("1.0.0"),
                    continue_probing=False,
                    baudrate=115200,
                ),
            ],
        ) as mock_probe,
    ):
        result = await flasher.trigger_bootloader_reset()

    assert result is not None
    assert result.version == Version("1.0.0")
    assert result.baudrate == 115200

    # Both reset targets should be attempted
    assert mock_trigger.mock_calls == [
        call(ResetTarget.RTS_DTR),
        call(ResetTarget.BAUDRATE),
    ]
    # Two probe attempts - one for each reset target
    assert mock_probe.mock_calls == [
        call(run_firmware=False, baudrate=115200),
        call(run_firmware=False, baudrate=115200),
    ]


async def test_trigger_bootloader_reset_all_probes_fail():
    flasher = Flasher(
        device="/dev/ttyMOCK",
        bootloader_reset=(ResetTarget.RTS_DTR, ResetTarget.BAUDRATE),
    )

    with (
        patch.object(flasher, "trigger_bootloader") as mock_trigger,
        patch.object(
            flasher,
            "probe_gecko_bootloader",
            side_effect=asyncio.TimeoutError,  # All probes fail
        ) as mock_probe,
    ):
        result = await flasher.trigger_bootloader_reset()

    assert result is None

    # Both reset targets should be attempted
    assert mock_trigger.mock_calls == [
        call(ResetTarget.RTS_DTR),
        call(ResetTarget.BAUDRATE),
    ]
    # Two probe attempts - one for each reset target
    assert mock_probe.mock_calls == [
        call(run_firmware=False, baudrate=115200),
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
