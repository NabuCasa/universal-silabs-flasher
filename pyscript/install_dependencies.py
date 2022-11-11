from __future__ import annotations

import micropip


async def install() -> None:
    await micropip.install(
        [
            # All `aio-libs` packages have been compiled as pure-Python modules
            "./multidict-4.7.6-py3-none-any.whl",
            "./yarl-1.8.1-py3-none-any.whl",
            "./frozenlist-1.3.1-py3-none-any.whl",
            "./aiosignal-1.2.0-py3-none-any.whl",
            "./aiohttp-3.8.3-py3-none-any.whl",
            # This one also did not seem to have a wheel despite being pure-Python
            "./pure_pcapy3-1.0.1-py3-none-any.whl",
            # Tweaked bellows
            "./bellows-0.35.0.dev0-py3-none-any.whl",
            # Finally, install the main module
            "./universal_silabs_flasher-0.0.4-py3-none-any.whl",
        ]
    )
