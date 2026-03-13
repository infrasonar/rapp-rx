import asyncio
import logging


class Protocol(asyncio.subprocess.SubprocessStreamProtocol):

    def pipe_data_received(self, fd, data):
        super().pipe_data_received(fd, data)

        line = ''
        if fd in (1, 2):
            try:
                line = data.decode().rstrip()
            except UnicodeDecodeError:
                try:
                    line = data.decode('latin-1').rstrip()
                except Exception:
                    pass
            if line:
                if fd == 1:
                    logging.debug(line)
                elif fd == 2:
                    logging.error(line)
