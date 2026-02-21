from argparse import ArgumentTypeError

import pytest

from universal_silabs_flasher.const import ResetTarget
from universal_silabs_flasher.flash import parse_reset_methods


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
