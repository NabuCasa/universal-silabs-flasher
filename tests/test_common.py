import asyncio

import pytest

from universal_silabs_flasher.common import StateMachine, put_first


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


def test_put_first():
    assert put_first([1, 2, 3], [2]) == [2, 1, 3]
    assert put_first([1, 2, 3], [4]) == [4, 1, 2, 3]
    assert put_first([1, 2, 3], [1]) == [1, 2, 3]
    assert put_first([1, 2, 3], [3]) == [3, 1, 2]
