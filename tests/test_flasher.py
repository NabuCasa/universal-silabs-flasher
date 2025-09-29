import asyncio
from unittest.mock import MagicMock, call, patch

import zigpy.types as t

from universal_silabs_flasher.common import FlowControlSerialProtocol, Version
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
    flasher = Flasher(device="/dev/ttyMOCK", bootloader_reset="baudrate")

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
        # Connect with 600 baud
        call("/dev/ttyMOCK", 600, FlowControlSerialProtocol),
        call().__aenter__(),
        call().__aenter__()._transport.write(b"BZ"),
        call().__aexit__(None, None, None),
        # Probe
        call("/dev/ttyMOCK", 115200, GeckoBootloaderProtocol),
        call().__aenter__(),
        call().__aenter__().probe(),
        call().__aexit__(None, None, None),
    ]
