import asyncio
import contextlib

import bellows.ezsp
import bellows.types
import bellows.config as config

AFTER_DISCONNECT_DELAY = 0.1


@contextlib.asynccontextmanager
async def connect_ezsp(port: str, baudrate: int = 115200) -> bellows.ezsp.EZSP:
    """Context manager to return a connected EZSP instance for a serial port."""
    app_config = config.CONFIG_SCHEMA(
        {
            config.CONF_DEVICE: {
                config.CONF_DEVICE_PATH: port,
                config.CONF_DEVICE_BAUDRATE: baudrate,
            }
        }
    )

    ezsp = await bellows.ezsp.EZSP.initialize(app_config)

    try:
        yield ezsp
    finally:
        ezsp.close()
        await asyncio.sleep(AFTER_DISCONNECT_DELAY)
