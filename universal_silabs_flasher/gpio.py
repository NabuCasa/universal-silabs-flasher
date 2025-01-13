from __future__ import annotations

import asyncio
import logging
from os import scandir
import time
import typing

from .const import GpioPattern

_LOGGER = logging.getLogger(__name__)

try:
    import gpiod

    is_gpiod_v1 = hasattr(gpiod.chip, "OPEN_BY_PATH")
except ImportError:
    gpiod = None

if gpiod is None:
    # No gpiod library
    def _send_gpio_pattern(chip: str, pattern: list[GpioPattern]) -> None:
        raise NotImplementedError("GPIO not supported on this platform")

elif is_gpiod_v1:
    # gpiod <= 1.5.4
    def _send_gpio_pattern(chip: str, pattern: list[GpioPattern]) -> None:
        chip = gpiod.chip(chip, gpiod.chip.OPEN_BY_PATH)
        lines = chip.get_lines(pattern[0].pins.keys())

        config = gpiod.line_request()
        config.consumer = "universal-silabs-flasher"
        config.request_type = gpiod.line_request.DIRECTION_OUTPUT

        try:
            # Open the pins and set their initial states
            _LOGGER.debug("Sending GPIO pattern %r", pattern[0])
            lines.request(config, [int(v) for v in pattern[0].pins.values()])
            time.sleep(pattern[0].delay_after)

            # Send all subsequent states
            for p in pattern[1:]:
                _LOGGER.debug("Sending GPIO pattern %r", p)
                lines.set_values([int(v) for v in p.pins.values()])
                time.sleep(p.delay_after)
        finally:
            # Clean up and ensure the GPIO pins are reset to inputs
            lines.set_direction_input()
            lines.release()

else:
    # gpiod >= 2.0.2
    def _send_gpio_pattern(chip: str, pattern: list[GpioPattern]) -> None:
        _LOGGER.debug("Sending GPIO pattern %r", pattern[0])

        with gpiod.request_lines(
            path=chip,
            consumer="universal-silabs-flasher",
            config={
                # Set initial states
                pin: gpiod.LineSettings(
                    direction=gpiod.line.Direction.OUTPUT,
                    output_value=gpiod.line.Value(state),
                )
                for pin, state in pattern[0].pins.items()
            },
        ) as request:
            time.sleep(pattern[0].delay_after)

            try:
                # Send all subsequent states
                for p in pattern[1:]:
                    _LOGGER.debug("Sending GPIO pattern %r", p)
                    request.set_values(
                        {
                            pin: gpiod.line.Value(int(state))
                            for pin, state in p.pins.items()
                        }
                    )
                    time.sleep(p.delay_after)
            finally:
                # Clean up and ensure the GPIO pins are reset to inputs
                request.reconfigure_lines(
                    {
                        pin: gpiod.LineSettings(direction=gpiod.line.Direction.INPUT)
                        for pin in pattern[0].pins.keys()
                    }
                )


def _generate_gpio_chips() -> typing.Iterable[str]:
    for entry in scandir("/dev/"):
        if is_gpiod_v1:
            if entry.name.startswith("gpiochip"):
                yield entry.path
        else:
            if gpiod.is_gpiochip_device(entry.path):
                yield entry.path


def _find_gpiochip_by_label(label: str) -> str:
    for path in _generate_gpio_chips():
        try:
            if is_gpiod_v1:
                chip = gpiod.chip(path, gpiod.chip.OPEN_BY_PATH)
                if chip.label == label:
                    return path
            else:
                with gpiod.Chip(path) as chip:
                    if chip.get_info().label == label:
                        return path
        except PermissionError:
            pass
    raise RuntimeError("No matching gpiochip device found")


async def find_gpiochip_by_label(label: str) -> str:
    result = await asyncio.get_running_loop().run_in_executor(
        None, _find_gpiochip_by_label, label
    )
    return result


async def send_gpio_pattern(chip: str, pattern: list[GpioPattern]) -> None:
    await asyncio.get_running_loop().run_in_executor(
        None, _send_gpio_pattern, chip, pattern
    )
