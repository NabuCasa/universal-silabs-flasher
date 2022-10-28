import asyncio

import pytest

from universal_silabs_flasher.common import StateMachine


async def test_state_machine_bad_initial_state():
    with pytest.raises(ValueError):
        StateMachine(states={"a", "b"}, initial="c")


async def test_state_machine():
    sm = StateMachine(states={"a", "b", "c"}, initial="a")
    assert sm.state == "a"

    sm.state = "b"
    assert sm.state == "b"

    # Invalid states are ignored
    with pytest.raises(ValueError):
        sm.state = "x"

    assert sm.state == "b"

    asyncio.get_running_loop().call_later(0.01, setattr, sm, "state", "a")
    await asyncio.gather(sm.wait_for_state("a"), sm.wait_for_state("a"))

    assert sm.state == "a"
