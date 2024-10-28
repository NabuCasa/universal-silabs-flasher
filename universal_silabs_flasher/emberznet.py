import contextlib

import bellows.config
import bellows.ezsp
import bellows.types
from bellows.zigbee.application import ControllerApplication
import zigpy.config


@contextlib.asynccontextmanager
async def connect_ezsp(port: str, baudrate: int = 115200) -> bellows.ezsp.EZSP:
    """Context manager to return a connected EZSP instance for a serial port."""

    ezsp = bellows.ezsp.EZSP(
        # We use this roundabout way to construct the device schema to make sure that
        # we are compatible with future changes to the zigpy device config schema.
        ControllerApplication.SCHEMA(
            {
                zigpy.config.CONF_DEVICE: {
                    zigpy.config.CONF_DEVICE_PATH: port,
                    zigpy.config.CONF_DEVICE_BAUDRATE: baudrate,
                }
            }
        )[zigpy.config.CONF_DEVICE]
    )

    await ezsp.connect(use_thread=False)

    try:
        yield ezsp
    finally:
        await ezsp.disconnect()
