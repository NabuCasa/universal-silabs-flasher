import asyncio
from unittest.mock import call, patch

import zigpy.types as t

from universal_silabs_flasher.common import Version
from universal_silabs_flasher.flasher import Flasher, ProbeResult


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
