import click
import pytest

from universal_silabs_flasher.const import ResetTarget
from universal_silabs_flasher.flash import EnumWithSeparator, SerialPort


def test_click_serialport_validation():
    assert SerialPort().convert("/dev/null", None, None) == "/dev/null"
    assert SerialPort().convert("socket://1.2.3.4", None, None) == "socket://1.2.3.4"
    assert SerialPort().convert("COM1", None, None) == "COM1"
    assert SerialPort().convert("\\\\.\\COM123", None, None) == "\\\\.\\COM123"

    with pytest.raises(click.BadParameter) as exc_info:
        assert SerialPort().convert("COM10", None, None)

    with pytest.raises(click.BadParameter) as exc_info:
        assert SerialPort().convert("http://1.2.3.4", None, None)

    assert "invalid URL scheme" in exc_info.value.message

    with pytest.raises(click.BadParameter) as exc_info:
        assert SerialPort().convert("/dev/serial/by-id/does-not-exist", None, None)

    assert "does not exist" in exc_info.value.message


def test_enum_with_separator_single_value() -> None:
    converter = EnumWithSeparator(ResetTarget)
    result = converter.convert("rts_dtr", None, None)
    assert result == [ResetTarget.RTS_DTR]


def test_enum_with_separator_multiple_values() -> None:
    converter = EnumWithSeparator(ResetTarget)
    result = converter.convert("rts_dtr,baudrate", None, None)
    assert result == [ResetTarget.RTS_DTR, ResetTarget.BAUDRATE]


def test_enum_with_separator_invalid_value() -> None:
    converter = EnumWithSeparator(ResetTarget)

    with pytest.raises(click.BadParameter) as exc_info:
        converter.convert("invalid_target", None, None)

    assert "'invalid_target' is invalid, must be one of:" in str(exc_info.value)
    assert "yellow" in str(exc_info.value)
    assert "rts_dtr" in str(exc_info.value)
