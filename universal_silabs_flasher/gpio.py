from __future__ import annotations

import asyncio
import time

try:
    import gpiod
except ImportError:
    gpiod = None

if gpiod is None:
    # No gpiod library
    def _send_gpio_pattern(
        chip: str, pin_states: dict[int, list[bool]], toggle_delay: float
    ) -> None:
        raise NotImplementedError("GPIO not supported on this platform")

elif hasattr(gpiod.chip, "OPEN_BY_PATH"):
    # gpiod <= 1.5.4
    def _send_gpio_pattern(
        chip: str, pin_states: dict[int, list[bool]], toggle_delay: float
    ) -> None:
        num_states = len(next(iter(pin_states.values())))

        chip = gpiod.chip(chip, gpiod.chip.OPEN_BY_PATH)
        lines = chip.get_lines(pin_states.keys())

        config = gpiod.line_request()
        config.consumer = "universal-silabs-flasher"
        config.request_type = gpiod.line_request.DIRECTION_OUTPUT

        try:
            # Open the pins and set their initial states
            lines.request(config, [int(states[0]) for states in pin_states.values()])

            # Send all subsequent states
            for i in range(1, num_states):
                time.sleep(toggle_delay)
                lines.set_values([int(states[i]) for states in pin_states.values()])
        finally:
            # Clean up and ensure the GPIO pins are reset to inputs
            lines.set_direction_input()
            lines.release()

else:
    # gpiod >= 2.0.2
    def _send_gpio_pattern(
        chip: str, pin_states: dict[int, list[bool]], toggle_delay: float
    ) -> None:
        # `gpiod` isn't available on Windows
        num_states = len(next(iter(pin_states.values())))

        with gpiod.request_lines(
            path=chip,
            consumer="universal-silabs-flasher",
            config={
                # Set initial states
                pin: gpiod.LineSettings(
                    direction=gpiod.line.Direction.OUTPUT,
                    output_value=gpiod.line.Value(states[0]),
                )
                for pin, states in pin_states.items()
            },
        ) as request:
            try:
                # Send all subsequent states
                for i in range(1, num_states):
                    time.sleep(toggle_delay)
                    request.set_values(
                        {
                            pin: gpiod.line.Value(int(pin_states[pin][i]))
                            for pin, states in pin_states.items()
                        }
                    )
            finally:
                # Clean up and ensure the GPIO pins are reset to inputs
                request.reconfigure_lines(
                    {
                        pin: gpiod.LineSettings(direction=gpiod.line.Direction.INPUT)
                        for pin, states in pin_states.items()
                    }
                )


async def send_gpio_pattern(
    chip: str, pin_states: dict[int, list[bool]], toggle_delay: float
) -> None:
    await asyncio.get_running_loop().run_in_executor(
        None, _send_gpio_pattern, chip, pin_states, toggle_delay
    )
