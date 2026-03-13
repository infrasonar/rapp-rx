import asyncio
import logging
import os
from aiohttp import web
from .loop import loop
from .protocol import Protocol


_HTTP_PORT = int(os.getenv('HTTP_PORT', 80))


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
        lambda: Protocol(2**16, loop),
        cmd=cmd,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    process = asyncio.subprocess.Process(transport, protocol, loop)

    process.stdin.write(body.encode())
    process.stdin.write_eof()
    await process.stdin.drain()

    try:
        await asyncio.wait_for(process.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            process.kill()
            # below is a fix for Python 3.12 (for some reason close is not
            # reached on the transport after calling kill or terminate)
            transport.close()
        except Exception:
            pass
        logging.warning(f'script `{script}` timed out after {timeout} seconds')
        return web.json_response({'error': 'Script Execution timeout'})
    if process.returncode:
        nr = process.returncode
        logging.warning(f'script `{script}` failed ({nr})')
        return web.json_response({'error': f'Script Execution failed ({nr})'})
    logging.debug(f'script `{script}` done')
    return web.json_response({'error': None})


async def init_http_server() -> web.AppRunner:
    app = web.Application()
    app.add_routes([
        web.post('/run', run),
    ])

    # TODO less aiohttp.access logging?
    # logger = logging.getLogger('aiohttp.access')
    # logger.setLevel(logging.WARNING)

    logging.info(f"Listening to HTTP requests on port {_HTTP_PORT}")
    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, '0.0.0.0', _HTTP_PORT)
    await site.start()

    return runner
