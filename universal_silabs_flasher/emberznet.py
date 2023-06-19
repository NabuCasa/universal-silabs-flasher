import asyncio
import contextlib

import bellows.config as config
import bellows.ezsp
import bellows.types

AFTER_DISCONNECT_DELAY = 0.1


@contextlib.asynccontextmanager
async def connect_ezsp(port: str, baudrate: int = 115200) -> bellows.ezsp.EZSP:
    """Context manager to return a connected EZSP instance for a serial port."""
    app_config = config.CONFIG_SCHEMA(
        {
            config.CONF_DEVICE: {
                config.CONF_DEVICE_PATH: port,
                config.CONF_DEVICE_BAUDRATE: baudrate,
            },
            config.CONF_EZSP_CONFIG: {
                # Do not set any configuration on startup
                "CONFIG_END_DEVICE_POLL_TIMEOUT": None,
                "CONFIG_INDIRECT_TRANSMISSION_TIMEOUT": None,
                "CONFIG_TC_REJOINS_USING_WELL_KNOWN_KEY_TIMEOUT_S": None,
                "CONFIG_SECURITY_LEVEL": None,
                "CONFIG_APPLICATION_ZDO_FLAGS": None,
                "CONFIG_SUPPORTED_NETWORKS": None,
                "CONFIG_PAN_ID_CONFLICT_REPORT_THRESHOLD": None,
                "CONFIG_TRUST_CENTER_ADDRESS_CACHE_SIZE": None,
                "CONFIG_SOURCE_ROUTE_TABLE_SIZE": None,
                "CONFIG_MULTICAST_TABLE_SIZE": None,
                "CONFIG_ADDRESS_TABLE_SIZE": None,
                "CONFIG_PACKET_BUFFER_COUNT": None,
                "CONFIG_STACK_PROFILE": None,
            },
            config.CONF_USE_THREAD: False,
        }
    )

    ezsp = await bellows.ezsp.EZSP.initialize(app_config)

    try:
        yield ezsp
    finally:
        ezsp.close()
        await asyncio.sleep(AFTER_DISCONNECT_DELAY)
