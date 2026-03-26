import sys
import asyncio
import logging


class Protocol(asyncio.subprocess.SubprocessStreamProtocol):
    def __init__(self, *args):
        super().__init__(*args)
        self.buffers = {1: bytearray(), 2: bytearray()}

    def pipe_data_received(self, fd: int, data: bytes | str):
        if fd not in self.buffers:
            return

        if isinstance(data, str):
            data = data.encode('utf-8')

        self.buffers[fd].extend(data)

        while b'\n' in self.buffers[fd]:
            line_bytes, remaining = self.buffers[fd].split(b'\n', 1)
            self.buffers[fd] = remaining
            self._log_line(fd, line_bytes)

    def process_exited(self):
        for fd, buffer in self.buffers.items():
            if buffer:
                self._log_line(fd, buffer)
                buffer.clear()

    def _log_line(self, fd: int, line_raw: bytearray):
        try:
            line = line_raw.decode('utf-8').rstrip()
        except UnicodeDecodeError:
            try:
                line = line_raw.decode('latin-1').rstrip()
            except Exception:
                line = ''

        if line:
            logfunc = logging.debug if fd == 1 else logging.error
            logfunc(line)
