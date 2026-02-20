from __future__ import annotations

import zigpy.types as t


class MessageType(t.enum8):
    REQUEST = 0x00
    RESPONSE = 0x01


class FunctionID(t.enum8):
    SERIAL_API_GET_CAPABILITIES = 0x07
    SERIAL_API_ENTER_BOOTLOADER = 0x27
