from __future__ import annotations

import enum
import time
import typing
import asyncio
import logging

import gpiod
import bellows.ezsp
import async_timeout
import bellows.types
import bellows.config
from awesomeversion import AwesomeVersion

from .cpc import CPCProtocol
from .gbl import GBLImage
from .common import PROBE_TIMEOUT, connect_protocol
from .emberznet import connect_ezsp
from .xmodemcrc import BLOCK_SIZE as XMODEM_BLOCK_SIZE
from .gecko_bootloader import NoFirmwareError, GeckoBootloaderProtocol

_LOGGER = logging.getLogger(__name__)


def _send_gpio_pattern(pin_states: dict[int, list[bool]], toggle_delay: float) -> None:
    chip = gpiod.chip(0, gpiod.chip.OPEN_BY_NUMBER)
    lines = {pin: chip.get_line(pin) for pin in pin_states.keys()}

    config = gpiod.line_request()
    config.consumer = "universal-silabs-flasher"
    config.request_type = gpiod.line_request.DIRECTION_OUTPUT

    try:
        # Open the pins and set their initial states
        for pin, line in lines.items():
            state = pin_states[pin][0]
            line.request(config, int(state))

        time.sleep(toggle_delay)

        # Send all subsequent states
        for i in range(1, len(pin_states[pin])):
            for pin, line in lines.items():
                line.set_value(int(pin_states[pin][i]))

            time.sleep(toggle_delay)
    finally:
        # Clean up and ensure the GPIO pins are reset to inputs
        for line in lines.values():
            line.set_direction_input()


async def send_gpio_pattern(
    pin_states: dict[int, list[bool]], toggle_delay: float
) -> None:
    await asyncio.get_running_loop().run_in_executor(
        None, _send_gpio_pattern, pin_states, toggle_delay
    )


class ApplicationType(enum.Enum):
    GECKO_BOOTLOADER = "bootloader"
    CPC = "cpc"
    EZSP = "ezsp"


class Flasher:
    def __init__(
        self,
        *,
        bootloader_baudrate: int = 115200,
        app_baudrate: int = 115200,
        probe_methods: tuple[ApplicationType, ...] = (
            ApplicationType.GECKO_BOOTLOADER,
            ApplicationType.CPC,
            ApplicationType.EZSP,
        ),
        device: str,
    ):
        self._bootloader_baudrate = bootloader_baudrate
        self._app_baudrate = app_baudrate
        self._probe_methods = probe_methods
        self._device = device

        self._app_type: ApplicationType | None = None
        self._app_version: AwesomeVersion | None = None

    @property
    def app_type(self) -> ApplicationType | None:
        return self._app_type

    @property
    def app_version(self) -> AwesomeVersion | None:
        return self._app_version

    async def enter_yellow_bootloader(self):
        await send_gpio_pattern(
            pin_states={
                24: [True, False, False],
                25: [True, False, True],
            },
            toggle_delay=0.1,
        )

    def _connect_gecko_bootloader(self):
        return connect_protocol(
            self._device, self._bootloader_baudrate, GeckoBootloaderProtocol
        )

    def _connect_cpc(self):
        return connect_protocol(self._device, self._app_baudrate, CPCProtocol)

    def _connect_ezsp(self):
        return connect_ezsp(self._device, self._app_baudrate)

    async def probe_gecko_bootloader(self) -> AwesomeVersion | None:
        try:
            async with self._connect_gecko_bootloader() as gecko:
                bootloader_version = await gecko.probe()
                await gecko.run_firmware()
        except NoFirmwareError:
            _LOGGER.warning("No application can be launched")
            return bootloader_version
        else:
            return None

    async def probe_cpc(self) -> AwesomeVersion:
        async with self._connect_cpc() as cpc:
            return await cpc.probe()

    async def probe_ezsp(self) -> AwesomeVersion:
        async with self._connect_ezsp() as ezsp:
            _, _, version = await ezsp.get_board_info()

        return AwesomeVersion(version.replace(" build ", "."))

    async def probe_app_type(self, *, force: bool = False) -> None:
        if not force and self._app_type is not None:
            return

        self._app_type = None
        self._app_version = None

        for probe_method in self._probe_methods:
            func = {
                ApplicationType.GECKO_BOOTLOADER: self.probe_gecko_bootloader,
                ApplicationType.CPC: self.probe_cpc,
                ApplicationType.EZSP: self.probe_ezsp,
            }[probe_method]

            _LOGGER.info("Probing %s", probe_method)

            try:
                version = await func()
            except asyncio.TimeoutError:
                continue

            if probe_method is ApplicationType.GECKO_BOOTLOADER and version is None:
                _LOGGER.debug("Launched application from bootloader, continuing")
                continue

            self._app_type = probe_method
            self._app_version = version
            break
        else:
            raise RuntimeError("Failed to probe running application type")

        _LOGGER.info("Detected %s, version %s", self._app_type, self._app_version)

    async def enter_bootloader(self) -> None:
        if self._app_type is None:
            await self.probe_app_type()

        if self._app_type is ApplicationType.GECKO_BOOTLOADER:
            # No firmware
            pass
        elif self._app_type is ApplicationType.CPC:
            async with self._connect_cpc() as cpc:
                async with async_timeout.timeout(PROBE_TIMEOUT):
                    await cpc.enter_bootloader()
        elif self._app_type is ApplicationType.EZSP:
            async with self._connect_ezsp() as ezsp:
                res = await ezsp.launchStandaloneBootloader(0x01)

                if res[0] != bellows.types.EmberStatus.SUCCESS:
                    raise RuntimeError(
                        f"EmberZNet could not enter the bootloader: {res[0]!r}"
                    )
        else:
            raise RuntimeError(f"Invalid application type: {self._app_type}")

    async def flash_firmware(
        self,
        firmware: GBLImage,
        run_firmware: bool = True,
        progress_callback: typing.Callable[[int, int], typing.Any] | None = None,
    ) -> None:
        data = firmware.serialize()

        # Pad the image to the XMODEM block size
        if len(data) % XMODEM_BLOCK_SIZE != 0:
            num_complete_blocks = len(data) // XMODEM_BLOCK_SIZE
            padded_size = XMODEM_BLOCK_SIZE * (num_complete_blocks + 1)
            data += b"\xFF" * (padded_size - len(data))

        async with self._connect_gecko_bootloader() as gecko:
            await gecko.probe()
            await gecko.upload_firmware(data, progress_callback=progress_callback)

            if run_firmware:
                await gecko.run_firmware()

    async def write_emberznet_eui64(self, new_eui64: bellows.types.EUI64) -> bool:
        await self.probe_app_type()

        if self.app_type != ApplicationType.EZSP:
            raise RuntimeError(f"Device is not running EmberZNet: {self.app_type}")

        async with self._connect_ezsp() as ezsp:
            (current_eui64,) = await ezsp.getEui64()
            _LOGGER.info("Current device IEEE: %s", current_eui64)

            if current_eui64 == new_eui64:
                _LOGGER.info("Device IEEE address already matches, not overwriting")
                return False

            if not await ezsp.can_write_custom_eui64():
                raise ValueError(
                    "IEEE address has already been written, it cannot be written again"
                )

            (status,) = await ezsp.setMfgToken(
                bellows.types.EzspMfgTokenId.MFG_CUSTOM_EUI_64, new_eui64.serialize()
            )

            if status != bellows.types.EmberStatus.SUCCESS:
                raise RuntimeError(f"Failed to write IEEE address: {status}")

        return True
