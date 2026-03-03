import asyncio
import logging
import signal
from lib.loop import loop
from lib.version import __version__ as version
from lib.http import init_http_server
from lib.logger import setup_logger


def stop(signame, *args):
    logging.warning(f'Signal \'{signame}\' received, stop RX')
    for task in asyncio.all_tasks():
        task.cancel()
    raise Exception


if __name__ == '__main__':
    setup_logger()
    logging.warning(f"Starting RX version {version}")

    http_server = loop.run_until_complete(init_http_server())

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    try:
        loop.run_forever()
    except Exception:
        loop.run_until_complete(loop.shutdown_asyncgens())
        pass

    loop.run_until_complete(http_server.cleanup())

    loop.close()

    logging.warning(f"Stopped RX version {version}")
