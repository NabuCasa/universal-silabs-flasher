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
        raise NotImplementedError()

elif hasattr(gpiod, "line_request"):
    # gpiod <= 1.5.4
    def _send_gpio_pattern(
        chip: str, pin_states: dict[int, list[bool]], toggle_delay: float
    ) -> None:
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
        finally:
            # Clean up and ensure the GPIO pins are reset to inputs
            for line in lines.values():
                line.set_direction_input()

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
            # Send all subsequent states
            for i in range(1, num_states):
                time.sleep(toggle_delay)
                request.set_values(
                    {
                        pin: gpiod.line.Value(int(pin_states[pin][i]))
                        for pin, states in pin_states.items()
                    }
                )


async def send_gpio_pattern(
    chip: str, pin_states: dict[int, list[bool]], toggle_delay: float
) -> None:
    await asyncio.get_running_loop().run_in_executor(
        None, _send_gpio_pattern, chip, pin_states, toggle_delay
    )
