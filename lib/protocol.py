import sys
import asyncio
import logging


class Protocol(asyncio.subprocess.SubprocessStreamProtocol):

    def pipe_data_received(self, fd: int, data: bytes | str):
        super().pipe_data_received(fd, data)

        logfunc = {
            sys.stdout.fileno(): logging.debug,
            sys.stderr.fileno(): logging.error
        }.get(fd)

        if logfunc is not None:
            line = ''
            if isinstance(data, bytes):
                try:
                    line = data.decode().rstrip()
                except UnicodeDecodeError:
                    try:
                        line = data.decode('latin-1').rstrip()
                    except Exception:
                        pass
            else:
                line = data

            if line:
                logfunc(line)
