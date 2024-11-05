from unittest.mock import call, patch

import pytest
import zigpy.types as t

from universal_silabs_flasher.cpc import (
    CPCProtocol,
    CPCTransportFrame,
    PropertyCommand,
    UnnumberedFrame,
)
from universal_silabs_flasher.cpc_types import (
    EndpointId,
    PropertyId,
    UnnumberedFrameCommandId,
)

FRAME1 = CPCTransportFrame(
    endpoint=EndpointId.SYSTEM,
    control=t.uint8_t(196),
    payload=UnnumberedFrame(
        command_id=UnnumberedFrameCommandId.PROP_VALUE_IS,
        command_seq=t.uint8_t(0),
        payload=PropertyCommand(
            property_id=PropertyId.SECONDARY_CPC_VERSION,
            value=b"\x04\x00\x00\x00\x04\x00\x00\x00\x03\x00\x00\x00",
        ),
    ),
)
FRAME2 = CPCTransportFrame(
    endpoint=EndpointId.SYSTEM,
    control=t.uint8_t(196),
    payload=UnnumberedFrame(
        command_id=UnnumberedFrameCommandId.PROP_VALUE_IS,
        command_seq=t.uint8_t(1),
        payload=PropertyCommand(
            property_id=PropertyId.SECONDARY_APP_VERSION, value=b"4.4.3-0368642c\x00"
        ),
    ),
)


def test_cpc_serialization() -> None:
    assert FRAME1.serialize() == (
        b"\x14\x00\x16\x00\xc4W\xe5\x06\x00\x10\x00\x03\x00\x00\x00\x04\x00\x00\x00\x04"
        b"\x00\x00\x00\x03\x00\x00\x00}>"
    )
    assert FRAME2.serialize() == (
        b"\x14\x00\x19\x00\xc4f\xc9\x06\x01\x13\x00\x04\x00\x00\x004.4.3-0368642c\x00"
        b"\x0b\xba"
    )


@pytest.mark.parametrize(
    "chunks",
    [
        # One at a time
        [FRAME1.serialize(), FRAME2.serialize()],
        # Byte at a time
        [bytes([b]) for b in FRAME1.serialize() + FRAME2.serialize()],
        # Concatenated
        [FRAME1.serialize() + FRAME2.serialize()],
    ],
)
def test_cpc_deserialization(chunks: list[bytes]) -> None:
    cpc = CPCProtocol()

    with patch.object(cpc, "frame_received") as mock_frame_received:
        for chunk in chunks:
            cpc.data_received(chunk)

    assert mock_frame_received.mock_calls == [
        call(FRAME1),
        call(FRAME2),
    ]


def test_cpc_bad_buffer_deserialization() -> None:
    cpc = CPCProtocol()

    with patch.object(cpc, "frame_received") as mock_frame_received:
        cpc.data_received(b"aaaaaaaaaaaaaaaaaaaaa\r\n")

    assert mock_frame_received.mock_calls == []
    assert cpc._buffer == b""
