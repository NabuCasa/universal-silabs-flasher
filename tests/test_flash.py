from argparse import ArgumentTypeError

import pytest

from universal_silabs_flasher.const import ResetTarget
from universal_silabs_flasher.flash import parse_reset_methods, parse_serial_port


def test_click_serialport_validation():
    assert parse_serial_port("/dev/null") == "/dev/null"
    assert parse_serial_port("socket://1.2.3.4") == "socket://1.2.3.4"
    assert parse_serial_port("COM1") == "COM1"
    assert parse_serial_port("\\\\.\\COM123") == "\\\\.\\COM123"

    with pytest.raises(ArgumentTypeError):
        assert parse_serial_port("COM10")

    with pytest.raises(ArgumentTypeError) as exc_info:
        assert parse_serial_port("http://1.2.3.4")

    assert "invalid URL scheme" in str(exc_info.value)

    with pytest.raises(ArgumentTypeError) as exc_info:
        assert parse_serial_port("/dev/serial/by-id/does-not-exist")

    assert "does not exist" in str(exc_info.value)


def test_enum_with_separator_single_value() -> None:
    result = parse_reset_methods("rts_dtr")
    assert result == [ResetTarget.RTS_DTR]


def test_enum_with_separator_multiple_values() -> None:
    result = parse_reset_methods("rts_dtr,baudrate")
    assert result == [ResetTarget.RTS_DTR, ResetTarget.BAUDRATE]


def test_enum_with_separator_invalid_value() -> None:
    with pytest.raises(ArgumentTypeError) as exc_info:
        parse_reset_methods("invalid_target")

    assert "'invalid_target' is invalid, must be one of:" in str(exc_info.value)
    assert "yellow" in str(exc_info.value)
    assert "rts_dtr" in str(exc_info.value)
