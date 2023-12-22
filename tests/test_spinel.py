import pytest

from universal_silabs_flasher.common import crc16_kermit
import universal_silabs_flasher.spinel as spinel


@pytest.mark.parametrize(
    "data, crc",
    [
        (b"", 0x0000),
        (b"foobar", 0x147B),
        (b"\xfa\x9b\x51\xb9\xf2\x53\xe3\xbd", 0x6782),
    ],
)
def test_hdlc_lite_crc(data, crc):
    assert crc16_kermit(data) == crc


@pytest.mark.parametrize(
    "encoded, decoded",
    [
        (bytes.fromhex("7e810243d3d37e"), bytes.fromhex("810243")),
        (bytes.fromhex("7e8103367d5e7d5d6af97e"), bytes.fromhex("8103367e7d")),
        (bytes.fromhex("7e810365010b287e"), bytes.fromhex("81036501")),
        (bytes.fromhex("7e8103862a01547d5e7e"), bytes.fromhex("8103862a01")),
        (
            bytes.fromhex(
                "7e8106024f50454e5448524541442f366666316163302d64697274793b204546523332"
                "3b2044656320323320323032322031383a30383a303000fa8c7e"
            ),
            bytes.fromhex(
                "8106024f50454e5448524541442f366666316163302d64697274793b2045465233323b"
                "2044656320323320323032322031383a30383a303000"
            ),
        ),
    ],
)
def test_hdlc_lite_encoding_decoding(encoded, decoded):
    assert spinel.HDLCLiteFrame(data=decoded).serialize() == encoded
    assert spinel.HDLCLiteFrame.from_bytes(encoded).data == decoded


@pytest.mark.parametrize(
    "encoded, decoded",
    [
        (
            bytes.fromhex(
                "8106024f50454e5448524541442f366666316163302d64697274793b2045465233323b"
                "2044656320323320323032322031383a30383a303000"
            ),
            spinel.SpinelFrame(
                header=spinel.SpinelHeader(
                    transaction_id=1,
                    network_link_id=0,
                    flag=0b10,
                ),
                command_id=spinel.CommandID.PROP_VALUE_IS,
                data=b"\x02OPENTHREAD/6ff1ac0-dirty; EFR32; Dec 23 2022 18:08:00\x00",
            ),
        ),
    ],
)
def test_spinel_parsing(encoded, decoded):
    assert spinel.SpinelFrame.from_bytes(encoded) == decoded
    assert decoded.serialize() == encoded
