import asyncio
import logging
import os
from aiohttp import web
from .loop import loop


_HTTP_PORT = int(os.getenv('HTTP_PORT', 80))


class Protocol(asyncio.subprocess.SubprocessStreamProtocol):
    def __init__(self, name, limit, loop):
        super().__init__(limit=limit, loop=loop)
        self.name = name
        self._out = []
        self._err = []

    def pipe_data_received(self, fd, data):
        super().pipe_data_received(fd, data)
        logging.debug(data)

        if fd == 1:
            try:
                self._out.append(data.decode().rstrip())
            except UnicodeDecodeError:
                try:
                    self._out.append(data.decode('latin-1').rstrip())
                except Exception:
                    pass
        elif fd == 2:
            try:
                self._err.append(data.decode().rstrip())
            except UnicodeDecodeError:
                try:
                    self._err.append(data.decode('latin-1').rstrip())
                except Exception:
                    pass

    def process_exited(self):
        super().process_exited()
        logging.info('script exited')


async def run(request: web.Request) -> web.Response:
    data = await request.json()
    script = data['script']
    timeout = data['timeout']
    body = data['body']
    logging.info(f'script `{script}`')

    _, ext = os.path.splitext(script)
    if ext == '.py':
        cmd = 'python'
    elif ext == '.sh':
        cmd = 'bash'
    elif ext == '.ps1':
        # TODO
        cmd = ''
    else:
        msg = f'unsupported ext: `{ext}`'
        return web.Response(status=400, text=msg)

    env = os.environ.copy()
    env.update(data['env'])

    transport, protocol = await loop.subprocess_shell(
        lambda: Protocol(script, 2**16, loop),
        cmd=cmd,
        # cwd=cwd,
        # executable='',
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    process = asyncio.subprocess.Process(transport, protocol, loop)

    process.stdin.write(body.encode())
    process.stdin.write_eof()
    await process.stdin.drain()

    # TODO OR
    # await process.communicate(body.encode())

    # try:
    #     await asyncio.wait_for(process.wait(), timeout=timeout)
    # except asyncio.TimeoutError:
    #     logging.warning(f'script `{script}` timed out after {timeout} seconds')
    #     return web.json_response({'error': 'Script Execution timeout'})
    # if process.returncode:
    #     return web.json_response({'error': 'Script Execution failed'})
    # return web.json_response({'error': None})
    return web.Response(status=204)


async def init_http_server() -> web.AppRunner:
    app = web.Application()
    app.add_routes([
        web.post('/run', run),
    ])

    logging.info(f"Listening to HTTP requests on port {_HTTP_PORT}")
    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, '0.0.0.0', _HTTP_PORT)
    await site.start()

    return runner
