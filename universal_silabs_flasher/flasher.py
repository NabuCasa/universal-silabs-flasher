from __future__ import annotations

import asyncio
import dataclasses
import logging
import typing

import bellows.config
import bellows.ezsp
import bellows.types
import zigpy.types

from .common import (
    PROBE_TIMEOUT,
    FlowControlSerialProtocol,
    Version,
    asyncio_timeout,
    connect_protocol,
    pad_to_multiple,
)
from .const import (
    DEFAULT_PROBE_METHODS,
    RESET_CONFIGS,
    ApplicationType,
    BaudrateResetConfig,
    GpioResetConfig,
    ResetTarget,
)
from .cpc import CPCProtocol
from .emberznet import connect_ezsp
from .firmware import FirmwareImage
from .gecko_bootloader import (
    XMODEM_BLOCK_SIZE,
    GeckoBootloaderProtocol,
    NoFirmwareError,
)
from .gpio import find_gpiochip_by_label, send_gpio_pattern
from .router import RouterProtocol
from .spinel import SpinelProtocol

_LOGGER = logging.getLogger(__name__)

BOOTLOADER_LAUNCH_DELAY = 3


class FailedToEnterBootloaderError(Exception):
    """Failed to enter the bootloader."""


@dataclasses.dataclass(frozen=True)
class ProbeResult:
    version: Version | None
    continue_probing: bool
    baudrate: int


class Flasher:
    def __init__(
        self,
        *,
        probe_methods: typing.Sequence[
            tuple[ApplicationType, int]
        ] = DEFAULT_PROBE_METHODS,
        device: str,
        bootloader_reset: str | tuple[ResetTarget, ...] = (),
        # To restore flasher "state", we can pass these to the constructor
        app_type: ApplicationType | None = None,
        app_version: Version | None = None,
        app_baudrate: int | None = None,
        bootloader_baudrate: int | None = None,
    ):
        self._probe_methods = probe_methods
        self._device = device

        self.app_type = app_type
        self.app_version = app_version
        self.app_baudrate = app_baudrate
        self.bootloader_baudrate = bootloader_baudrate

        if isinstance(bootloader_reset, str):
            bootloader_reset = (ResetTarget(bootloader_reset),)

        self._reset_targets: list[ResetTarget] = [
            ResetTarget(target) for target in bootloader_reset if target
        ]

    async def trigger_bootloader(self, target: ResetTarget) -> None:
        config = RESET_CONFIGS[target]

        if isinstance(config, BaudrateResetConfig):
            # Baudrate command mode uses a pattern of baudrates to enter a command mode
            for baudrate in config.baudrates:
                async with connect_protocol(
                    self._device, baudrate, FlowControlSerialProtocol
                ) as uart:
                    await asyncio.sleep(config.delay_after_each)

                    # Write command on the last baudrate if specified
                    if baudrate == config.baudrates[-1] and config.command:
                        uart._transport.write(config.command)

            await asyncio.sleep(config.delay_after_final)
        elif isinstance(config, GpioResetConfig):
            chip = config.chip

            if config.chip_type == "cp210x":
                _LOGGER.warning(
                    "When using %s bootloader reset ensure no other CP2102 USB serial"
                    " devices are connected.",
                    target.value,
                )

                chip = await find_gpiochip_by_label(config.chip_type)

            if config.chip_type == "uart":
                # The baudrate isn't necessary, since we're just using flow control pins
                async with connect_protocol(
                    self._device, 115200, FlowControlSerialProtocol
                ) as uart:
                    for pattern in config.pattern:
                        await uart.set_signals(**pattern.pins)
                        await asyncio.sleep(pattern.delay_after)
            else:
                await send_gpio_pattern(chip, config.pattern)
        else:
            raise TypeError(f"Invalid reset configuration for {target!r}")

    def _connect_gecko_bootloader(self, baudrate: int):
        return connect_protocol(self._device, baudrate, GeckoBootloaderProtocol)

    def _connect_cpc(self, baudrate: int):
        return connect_protocol(self._device, baudrate, CPCProtocol)

    def _connect_ezsp(self, baudrate: int):
        return connect_ezsp(self._device, baudrate)

    def _connect_router(self, baudrate: int):
        return connect_protocol(self._device, baudrate, RouterProtocol)

    def _connect_spinel(self, baudrate: int):
        return connect_protocol(self._device, baudrate, SpinelProtocol)

    async def probe_gecko_bootloader(
        self, *, baudrate: int, run_firmware: bool = True
    ) -> ProbeResult:
        try:
            async with self._connect_gecko_bootloader(baudrate) as gecko:
                bootloader_version = await gecko.probe()

                if run_firmware:
                    await gecko.run_firmware()
                    _LOGGER.info("Launched application from bootloader")
        except NoFirmwareError:
            _LOGGER.warning("No application can be launched")
            return ProbeResult(
                version=bootloader_version,
                baudrate=baudrate,
                continue_probing=False,
            )
        else:
            return ProbeResult(
                version=bootloader_version,
                baudrate=baudrate,
                continue_probing=run_firmware,
            )

    async def probe_cpc(self, baudrate: int) -> ProbeResult:
        async with self._connect_cpc(baudrate) as cpc:
            version = await cpc.probe()

        return ProbeResult(
            version=version,
            baudrate=baudrate,
            continue_probing=False,
        )

    async def probe_ezsp(self, baudrate: int) -> ProbeResult:
        async with self._connect_ezsp(baudrate) as ezsp:
            _, _, version = await ezsp.get_board_info()

        return ProbeResult(
            version=Version(version),
            baudrate=baudrate,
            continue_probing=False,
        )

    async def probe_router(self, baudrate: int) -> ProbeResult:
        async with self._connect_router(baudrate) as router:
            version = await router.probe()

        return ProbeResult(
            version=version,
            baudrate=baudrate,
            continue_probing=False,
        )

    async def probe_spinel(self, baudrate: int) -> ProbeResult:
        async with self._connect_spinel(baudrate) as spinel:
            version = await spinel.probe()

        return ProbeResult(
            version=version,
            baudrate=baudrate,
            continue_probing=False,
        )

    async def trigger_bootloader_reset(
        self, *, run_firmware: bool
    ) -> ProbeResult | None:
        """Reset into the bootloader by trying the probing methods, one by one."""

        # If we have no way to enter the bootloader, don't try
        if not self._reset_targets:
            return None

        # We don't really care which method works, just try them all at once
        for target in self._reset_targets:
            _LOGGER.info(f"Triggering {target.value} bootloader")
            await self.trigger_bootloader(target)

        await asyncio.sleep(BOOTLOADER_LAUNCH_DELAY)

        return await self._detect_gecko_bootloader(run_firmware=run_firmware)

    async def _detect_gecko_bootloader(
        self, *, run_firmware: bool
    ) -> ProbeResult | None:
        # Try probing the bootloader at all known baudrates
        bootloader_baudrates = [
            baudrate
            for method, baudrate in self._probe_methods
            if method == ApplicationType.GECKO_BOOTLOADER
        ] or [
            baudrate
            for method, baudrate in DEFAULT_PROBE_METHODS
            if method == ApplicationType.GECKO_BOOTLOADER
        ]

        for baudrate in bootloader_baudrates:
            try:
                probe_result = await self.probe_gecko_bootloader(
                    run_firmware=run_firmware, baudrate=baudrate
                )
            except asyncio.TimeoutError:
                continue
            else:
                return probe_result

        return None

    async def probe_app_type(
        self,
        only: typing.Sequence[ApplicationType] | None = None,
    ) -> None:
        if only is None:
            probe_methods = self._probe_methods
        else:
            probe_methods = [
                (method, baudrate)
                for method, baudrate in self._probe_methods
                if method in only
            ]

        # Only run firmware from the bootloader if we have bootloader reset and
        # other probe methods
        only_probe_bootloader = all(
            m == ApplicationType.GECKO_BOOTLOADER for m, _ in probe_methods
        )

        run_firmware = self._reset_targets and not only_probe_bootloader

        probe_funcs = {
            ApplicationType.GECKO_BOOTLOADER: (
                lambda baudrate: self.probe_gecko_bootloader(
                    run_firmware=run_firmware, baudrate=baudrate
                )
            ),
            ApplicationType.CPC: self.probe_cpc,
            ApplicationType.EZSP: self.probe_ezsp,
            ApplicationType.SPINEL: self.probe_spinel,
            ApplicationType.ROUTER: self.probe_router,
        }

        # Reset into bootloader, if possible. Run the firmware so that we can probe the
        # running application afterwards.
        bootloader_probe = await self.trigger_bootloader_reset(run_firmware=True)

        if bootloader_probe is not None:
            self.bootloader_baudrate = bootloader_probe.baudrate

            if not bootloader_probe.continue_probing:
                # If the bootloader can be entered but fails to launch an application
                # there is no point probing further, it'll just waste time
                probe_methods = []

        for probe_method, baudrate in probe_methods:
            # Don't probe the bootloader twice
            if (
                probe_method == ApplicationType.GECKO_BOOTLOADER
                and bootloader_probe is not None
            ):
                _LOGGER.debug("Not probing bootloader twice")
                continue

            _LOGGER.info("Probing %s at %d baud", probe_method, baudrate)

            try:
                result = await probe_funcs[probe_method](baudrate=baudrate)
            except asyncio.TimeoutError:
                _LOGGER.debug("Probe timed out")
                continue

            _LOGGER.debug("Probe result: %s", result)

            # Keep track of the bootloader version for later
            if probe_method == ApplicationType.GECKO_BOOTLOADER:
                _LOGGER.info("Detected bootloader version %s", result.version)
                bootloader_probe = result
                self.bootloader_baudrate = bootloader_probe.baudrate

            if not result.continue_probing:
                self.app_type = probe_method
                self.app_version = result.version
                self.app_baudrate = result.baudrate
                break

        if self.app_type is None:
            if not bootloader_probe or not self._reset_targets:
                raise RuntimeError("Failed to probe running application type")

            # We have no valid application image but can still re-enter the
            # bootloader whenever we want
            await self.trigger_bootloader_reset(run_firmware=False)

            self.app_type = ApplicationType.GECKO_BOOTLOADER
            self.app_version = bootloader_probe.version
            self.app_baudrate = bootloader_probe.baudrate
            self.bootloader_baudrate = bootloader_probe.baudrate
            _LOGGER.debug("Bootloader did not launch a valid application")

        _LOGGER.info(
            "Detected %s, version %s at %s baudrate (bootloader baudrate %s)",
            self.app_type,
            self.app_version,
            self.app_baudrate,
            self.bootloader_baudrate,
        )

    async def enter_bootloader(self) -> None:
        # If we can enter the bootloader externally, do it
        bootloader_probe = await self.trigger_bootloader_reset(run_firmware=False)
        if bootloader_probe is not None:
            self.bootloader_baudrate = bootloader_probe.baudrate
            return

        # Otherwise, probe the application type and enter the bootloader from there
        if self.app_type is None:
            await self.probe_app_type()

        if self.app_type is ApplicationType.GECKO_BOOTLOADER:
            # No firmware
            pass
        elif self.app_type is ApplicationType.CPC:
            async with self._connect_cpc(self.app_baudrate) as cpc:
                async with asyncio_timeout(PROBE_TIMEOUT):
                    await cpc.enter_bootloader()
        elif self.app_type is ApplicationType.SPINEL:
            async with self._connect_spinel(self.app_baudrate) as spinel:
                async with asyncio_timeout(PROBE_TIMEOUT):
                    await spinel.enter_bootloader()
        elif self.app_type is ApplicationType.ROUTER:
            async with self._connect_router(self.app_baudrate) as router:
                async with asyncio_timeout(PROBE_TIMEOUT):
                    await router.enter_bootloader()
        elif self.app_type is ApplicationType.EZSP:
            async with self._connect_ezsp(self.app_baudrate) as ezsp:
                try:
                    res = await ezsp.launchStandaloneBootloader(mode=0x01)
                except asyncio.TimeoutError:
                    _LOGGER.warning(
                        "Application failed to respond to bootloader launching command."
                        " Assuming bootloader has launched."
                    )
                else:
                    if res[0] != bellows.types.EmberStatus.SUCCESS:
                        raise RuntimeError(
                            f"EmberZNet could not enter the bootloader: {res[0]!r}"
                        )
        else:
            raise RuntimeError(f"Invalid application type: {self.app_type}")

        if self.app_type is not ApplicationType.GECKO_BOOTLOADER:
            await asyncio.sleep(BOOTLOADER_LAUNCH_DELAY)

        # Verify the bootloader has launched
        bootloader_probe = await self._detect_gecko_bootloader(run_firmware=False)
        if bootloader_probe is None:
            raise FailedToEnterBootloaderError()

        self.bootloader_baudrate = bootloader_probe.baudrate

    async def flash_firmware(
        self,
        firmware: FirmwareImage,
        run_firmware: bool = True,
        progress_callback: typing.Callable[[int, int], typing.Any] | None = None,
    ) -> None:
        data = firmware.serialize()

        # Pad the image to the XMODEM block size
        data = pad_to_multiple(data, XMODEM_BLOCK_SIZE, b"\xff")

        if self.bootloader_baudrate is None:
            _LOGGER.debug("Bootloader baudrate unknown, assuming 115200")
            bootloader_baudrate = 115200
        else:
            bootloader_baudrate = self.bootloader_baudrate

        async with self._connect_gecko_bootloader(bootloader_baudrate) as gecko:
            await gecko.probe()
            await gecko.upload_firmware(data, progress_callback=progress_callback)

            if run_firmware:
                await gecko.run_firmware()

    async def dump_emberznet_config(self) -> None:
        if self.app_type != ApplicationType.EZSP:
            raise RuntimeError(f"Device is not running EmberZNet: {self.app_type}")

        async with self._connect_ezsp(self.app_baudrate) as ezsp:
            for config in bellows.types.EzspConfigId:
                v = await ezsp.getConfigurationValue(configId=config)
                if v[0] == bellows.types.EzspStatus.ERROR_INVALID_ID:
                    continue
                print(f"{config.name}={v[1]}")

    async def write_emberznet_eui64(
        self, new_ieee: zigpy.types.EUI64, force: bool = False
    ) -> bool:
        await self.probe_app_type()

        if self.app_type != ApplicationType.EZSP:
            raise RuntimeError(f"Device is not running EmberZNet: {self.app_type}")

        async with self._connect_ezsp(self.app_baudrate) as ezsp:
            (current_ieee,) = await ezsp.getEui64()
            _LOGGER.info("Current device IEEE: %s", current_ieee)

            if current_ieee == new_ieee:
                _LOGGER.info("Device IEEE address already matches, not overwriting")
                return False

            await ezsp.write_custom_eui64(ieee=new_ieee, burn_into_userdata=force)
            _LOGGER.info("Wrote new device IEEE: %s", new_ieee)

        return True
