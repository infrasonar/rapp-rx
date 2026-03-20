#!/usr/bin/env python
from __future__ import annotations
from base64 import b64encode
from typing import Any
from winrm import Response
from winrm.protocol import Protocol
import sys
import os
import asyncio
import json
import re
import time
import warnings
import xml.etree.ElementTree as ET


DEFAULT_PORT = 5986

ERR_NUM_ARGS = 1000
ERR_NO_PS1_SCRIPT = 1001
ERR_READING_FILE = 1002
ERR_INVALID_OR_MISSING_CONNECTION_STR = 1003
ERR_MISSING_ENV_VAR = 1004

# Regular expression for matching connection string
_RE_CONN = re.compile(
    r'^#\s*winrm://'
    r'([^:@]+):([^@]+)@'
    r'([^:/]+)'
    r'(:(\{[A-Z_]+\}|\d{1,5}))?$')

# Regular expression for env
_RE_ENV = re.compile(r'$\{[A-Z_]+\}$')


class Session:
    sessions: dict[tuple[str, str, str, int], Session] = dict()
    loop: asyncio.AbstractEventLoop | None = None
    task: asyncio.Future | None = None
    max_idle: float = 60.0

    def __init__(self, username: str, password: str,
                 host: str, port: int = DEFAULT_PORT):
        self.lock = asyncio.Lock()
        self.host = host
        self.username = username
        self.password = password
        self.port = port
        self.last_access = time.time()

        self.protocol: Protocol | None = None
        self.shell_id: str | None = None
        if self.loop is None:
            self.__class__.loop = asyncio.get_running_loop()
            self.__class__.task = asyncio.ensure_future(self.close_sessions())

    @classmethod
    async def close_sessions(cls):
        while True:
            min_threshold = time.time() - cls.max_idle
            for key, sess in cls.sessions.copy().items():
                if sess.last_access < min_threshold:
                    async with sess.lock:
                        if sess.protocol and sess.shell_id:
                            sess._close_shell()
                            del cls.sessions[key]
            for _ in range(20):
                await asyncio.sleep(0.5)

    @staticmethod
    def _decode(b: bytes) -> str:
        try:
            msg = b.decode('utf-8')
        except Exception:
            msg = b.decode('cp1252')

        return msg

    @staticmethod
    def _truncate(msg: str, n: int) -> str:
        return f'{msg[:n-3]}...' if len(msg) > n else msg

    def _strip_namespace(self, xml: bytes) -> bytes:
        """strips any namespaces from an xml string"""
        p = re.compile(b'xmlns=*[""][^""]*[""]')
        allmatches = p.finditer(xml)
        for match in allmatches:
            xml = xml.replace(match.group(), b"")
        return xml

    def _clean_error_msg(self, msg: bytes) -> bytes:
        """converts a Powershell CLIXML message to a more human readable
        string"""
        # if the msg does not start with this, return it as is
        if msg.startswith(b"#< CLIXML\r\n"):
            # for proper xml, we need to remove the CLIXML part
            # (the first line)
            msg_xml = msg[11:]
            try:
                # remove the namespaces from the xml for easier processing
                msg_xml = self._strip_namespace(msg_xml)
                root = ET.fromstring(msg_xml)
                # the S node is the error message, find all S nodes
                nodes = root.findall("./S")
                new_msg = ""
                for s in nodes:
                    # append error msg string to result, also
                    # the hex chars represent CRLF so we replace with newline
                    if s.text:
                        new_msg += s.text.replace("_x000D__x000A_", "\n")
            except Exception as e:
                # if any of the above fails, the msg was not true xml
                # print a warning and return the original string
                warnings.warn(
                    "There was a problem converting the Powershell error "
                    f"message: {e}")
            else:
                # if new_msg was populated, that's our error message
                # otherwise the original error message will be used
                if len(new_msg):
                    # remove leading and trailing whitespace while we are here
                    return new_msg.strip().encode("utf-8")

        # either failed to decode CLIXML or there was nothing to decode
        # just return the original message
        return msg

    def _query(self, script: str) -> Any:
        encoded_ps = b64encode(script.encode("utf_16_le")).decode("ascii")
        command = f"powershell -encodedcommand {encoded_ps}"
        if self.protocol is None:
            self._open_shell()

        assert self.protocol, 'protocol is missing'
        assert self.shell_id, 'shell ID is missing'
        try:
            command_id = self.protocol.run_command(self.shell_id, command)
            try:
                rs = Response(self.protocol.get_command_output(
                    self.shell_id,
                    command_id))
            finally:
                self.protocol.cleanup_command(self.shell_id, command_id)
        finally:
            self._close_shell()

        if rs.status_code:
            # if there was an error, clean it it up and make it human
            # readable
            try:
                b = self._clean_error_msg(rs.std_err)
                msg = self._decode(b)
                assert msg  # only raise when we have a message
            except Exception:
                raise Exception(f'status code: {rs.status_code} (no message)')
            raise Exception(msg)

        msg = self._decode(rs.std_out)
        print(msg, file=sys.stdout)

    def _open_shell(self):
        assert self.protocol is None and self.shell_id is None
        # WinRM port 5985 is http and 5986 is https
        proto = 'http' if self.port == 5985 else 'https'

        self.protocol = Protocol(
            endpoint=f'{proto}://{self.host}:{self.port}/wsman',
            transport='ntlm',
            username=self.username,
            password=self.password,
            server_cert_validation='ignore')
        try:
            self.shell_id = self.protocol.open_shell()
        except Exception:
            self.protocol = None
            raise

    def _close_shell(self):
        assert self.protocol and self.shell_id
        try:
            self.protocol.close_shell(self.shell_id)
        finally:
            self.protocol = None
            self.shell_id = None

    async def query(self, script: str) -> Any:
        assert self.loop
        async with self.lock:
            res = await self.loop.run_in_executor(None, self._query, script)
            self.last_access = time.time()  # update on no exception
            return res

    @classmethod
    async def get(cls, username: str, password: str,
                  host: str, port: int = 5986) -> Session:
        key = (host, username, password, port)
        sess = cls.sessions.get(key)
        if sess is None:
            sess = cls.sessions[key] = cls(username, password, host, port)

        return sess


def from_env(inp: str) -> str:
    m = _RE_ENV.match(inp)
    if m is None:
        return inp
    evar = m.group(1)
    value = os.getenv(evar)
    if value is None:
        print(f'Environment variable `{evar}` is missing', file=sys.stderr)
        sys.exit(ERR_MISSING_ENV_VAR)
    return value


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print('Expecting a single argument.\n'
              'For example: \n'
              '  winrm.py my-script.ps1', file=sys.stderr)
        sys.exit(ERR_NUM_ARGS)

    script_name = sys.argv[1]
    _, ext = os.path.splitext(script_name)
    if ext.lower() != '.ps1':
        print('Expecting a PowerShell script (.ps1). \n'
              f'Got: {ext or "unknown script type"}', file=sys.stderr)
        sys.exit(ERR_NO_PS1_SCRIPT)

    try:
        with open(script_name, 'r') as f:
            conn_str = f.readline()
    except Exception:
        print(f"Failed to read file: {script_name}", file=sys.stderr)
        sys.exit(ERR_READING_FILE)

    m = _RE_CONN.match(conn_str)
    if m is None:
        print(
            "RX PowerShell script must start with a valid connection string.\n"
            "For example:\n"
            "# winrm://alice:{PASSWORD}@{ASSET_NAME}", file=sys.stderr)
        sys.exit(ERR_INVALID_OR_MISSING_CONNECTION_STR)

    username = from_env(m.group(1).strip())
    password = from_env(m.group(2).strip())
    hostname = from_env(m.group(3).strip())
    port_str = from_env((m.group(4) or f'{DEFAULT_PORT}').strip())
    try:
        port = int(port_str or f'{DEFAULT_PORT}')
        assert 1 <= port < 2**16
    except Exception:
        print(
            'Port must be a number between 1 and 65535.\n'
            f'Got: {port_str}', file=sys.stderr)





