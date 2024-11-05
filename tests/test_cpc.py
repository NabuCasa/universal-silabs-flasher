from unittest.mock import patch

from universal_silabs_flasher.cpc import CPCProtocol


async def test_cpc_bad_buffer_deserialization() -> None:
    cpc = CPCProtocol()

    with patch.object(cpc, "frame_received") as mock_frame_received:
        cpc.data_received(b"aaaaaaaaaaaaaaaaaaaaa\r\n")

    assert mock_frame_received.mock_calls == []
    assert cpc._buffer == b""
