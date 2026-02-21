import asyncio

from .flash import main as async_main


def main():
    """Main entry point."""
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
