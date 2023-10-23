import asyncio

import pytest

from universal_silabs_flasher.common import StateMachine, Version, put_first


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


@pytest.mark.parametrize(
    "version",
    [
        "2.00.01",
        "7.2.2.0 build 190",
        "4.2.2",
        "SL-OPENTHREAD/2.2.2.0_GitHub-91fa1f455",
    ],
)
def test_version_parsing(version):
    v1 = Version(version)
    v2 = Version(version)

    assert v1.compatible_with(v2)
    assert v1 == v2
    assert v1 >= v2
    assert not v1 > v2


def test_version_comparison_simple():
    assert Version("2.00.01") > Version("2.00.00")
    assert Version("2.10.01") > Version("2.00.02")
    assert Version("2.00.01") >= Version("2.00.00")
    assert Version("2.00.01") != Version("2.00.00")


def test_version_comparison_thread():
    assert Version("SL-OPENTHREAD/2.2.2.0_GitHub-91fa1f455").compatible_with(
        Version("SL-OPENTHREAD/2.2.2.0_GitHub-asdfoo")
    )

    assert not Version("SL-OPENTHREAD/2.2.2.1_GitHub-91fa1f455").compatible_with(
        Version("SL-OPENTHREAD/2.2.2.0_GitHub-asdfoo")
    )

    assert Version("SL-OPENTHREAD/2.2.2.1_GitHub-91fa1f455") > Version(
        "SL-OPENTHREAD/2.2.2.0_GitHub-asdfoo"
    )


def test_version_comparison_ezsp():
    assert Version("7.2.2.0 build 191") > Version("7.2.2.0 build 190")
    assert Version("7.2.2.0").compatible_with(Version("7.2.2.0 build 190"))
    assert not Version("7.2.2.0 build 191").compatible_with(
        Version("7.2.2.0 build 190")
    )
