import click
import pytest

from universal_silabs_flasher.flash import SerialPort


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
