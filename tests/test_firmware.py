import pathlib

import pytest

from universal_silabs_flasher import firmware
from universal_silabs_flasher.common import Version

FIRMWARES_DIR = pathlib.Path(__file__).parent / "firmwares"


def test_firmware_ebl_valid():
    data = (FIRMWARES_DIR / "ncp-uart-sw-6.4.1.ebl").read_bytes()
    fw = firmware.parse_firmware_image(data)

    assert isinstance(fw, firmware.EBLImage)
    assert fw.serialize() == data

    with pytest.raises(KeyError):
        fw.get_nabucasa_metadata()


def test_firmware_gbl_valid_with_metadata():
    data = (
        FIRMWARES_DIR / "NabuCasa_SkyConnect_RCP_v4.1.3_rcp-uart-hw-802154_115200.gbl"
    ).read_bytes()
    fw = firmware.parse_firmware_image(data)

    assert isinstance(fw, firmware.GBLImage)
    assert fw.serialize() == data
    assert fw.get_nabucasa_metadata() == firmware.NabuCasaMetadata(
        metadata_version=1,
        sdk_version=Version("4.1.3"),
        ezsp_version=None,
        cpc_version=None,
        fw_type=firmware.FirmwareImageType.RCP_UART_802154,
        ot_rcp_version=None,
        baudrate=None,
        original_json={
            "metadata_version": 1,
            "sdk_version": "4.1.3",
            "fw_type": "rcp-uart-802154",
        },
    )


def test_firmware_gbl_valid_no_metadata():
    data = (
        FIRMWARES_DIR / "NabuCasa_EZSP_v6.10.3.0_PB32_ncp-uart-hw_115200.gbl"
    ).read_bytes()
    fw = firmware.parse_firmware_image(data)

    assert isinstance(fw, firmware.GBLImage)
    assert fw.serialize() == data

    with pytest.raises(KeyError):
        fw.get_nabucasa_metadata()
