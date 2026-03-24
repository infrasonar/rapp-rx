#!/usr/bin/env python
from __future__ import annotations
from base64 import b64encode
from typing import Any
from winrm import Response
from winrm.protocol import Protocol
import sys
import os
import re
import time
import warnings
import xml.etree.ElementTree as ET

# Default port 5986 (HTTPS); Use 5985 for HTTP.
DEFAULT_PORT = 5986

# Defined error codes 100..199
ERR_NUM_ARGS = 100
ERR_NO_PS1_SCRIPT = 101
ERR_READING_FILE = 102
ERR_INVALID_OR_MISSING_CONNECTION_STR = 103
ERR_MISSING_ENV_VAR = 104
ERR_INVALID_PORT_NUMBER = 105
ERR_FAILED_TO_EXEC = 106

# Ignore common env vars
IGNORE_ENV = set((
    'BUNDLED_DEBUGPY_PATH',
    'CHROME_DESKTOP',
    'CLUTTER_BACKEND',
    'COLORTERM',
    'CONDA_DEFAULT_ENV',
    'CONDA_EXE',
    'CONDA_PREFIX',
    'CONDA_PREFIX_1',
    'CONDA_PROMPT_MODIFIER',
    'CONDA_PYTHON_EXE',
    'CONDA_SHLVL',
    'DBUS_SESSION_BUS_ADDRESS',
    'DEBUGINFOD_URLS',
    'DESKTOP_SESSION',
    'DISPLAY',
    'FC_FONTATIONS',
    'GDK_BACKEND',
    'GDMSESSION',
    'GDM_LANG',
    'GIT_ASKPASS',
    'GNOME_KEYRING_CONTROL',
    'GPG_AGENT_INFO',
    'GTK_OVERLAY_SCROLLING',
    'HOME',
    'HOMEBREW_CELLAR',
    'HOMEBREW_PREFIX',
    'HOMEBREW_REPOSITORY',
    'INFOPATH',
    'LANG',
    'LANGUAGE',
    'LESSCLOSE',
    'LESSOPEN',
    'LS_COLORS',
    'PANEL_GDK_CORE_DEVICE_EVENTS',
    'PATH',
    'PWD',
    'PYDEVD_DISABLE_FILE_VALIDATION',
    'PYTHONSTARTUP',
    'PYTHON_BASIC_REPL',
    'QT_ACCESSIBILITY',
    'QT_QPA_PLATFORMTHEME',
    'SESSION_MANAGER',
    'SHELL',
    'SHLVL',
    'SSH_AUTH_SOCK',
    'TERM',
    'TERM_PROGRAM',
    'TERM_PROGRAM_VERSION',
    'VSCODE_DEBUGPY_ADAPTER_ENDPOINTS',
    'VSCODE_GIT_ASKPASS_EXTRA_ARGS',
    'VSCODE_GIT_ASKPASS_MAIN',
    'VSCODE_GIT_ASKPASS_NODE',
    'VSCODE_GIT_IPC_HANDLE',
    'VSCODE_PYTHON_AUTOACTIVATE_GUARD',
    'XAUTHORITY',
    'XDG_CONFIG_DIRS',
    'XDG_CURRENT_DESKTOP',
    'XDG_DATA_DIRS',
    'XDG_GREETER_DATA_DIR',
    'XDG_MENU_PREFIX',
    'XDG_RUNTIME_DIR',
    'XDG_SEAT',
    'XDG_SEAT_PATH',
    'XDG_SESSION_CLASS',
    'XDG_SESSION_DESKTOP',
    'XDG_SESSION_ID',
    'XDG_SESSION_PATH',
    'XDG_SESSION_TYPE',
    'XDG_VTNR',
    '_',
    '_CE_CONDA',
    '_CE_M',
    '_CONDA_EXE',
    '_CONDA_ROOT',
))

# Regular expression for matching connection string
_RE_CONN = re.compile(
    r'^#\s*winrm://'
    r'([^:@]+):([^@]+)@'
    r'([^:/]+)'
    r'(:(\{[A-Z_]+\}|\d{1,5}))?$')

# Regular expression for env
_RE_ENV = re.compile(r'$\{[A-Z_]+\}$')


class Session:
    def __init__(self, username: str, password: str,
                 host: str, port: int = DEFAULT_PORT):
        self.host = host
        self.username = username
        self.password = password
        self.port = port
        self.last_access = time.time()

        self.protocol: Protocol | None = None
        self.shell_id: str | None = None

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

    def query(self, script: str) -> Any:
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
            except Exception:
                msg = f'status code: {rs.status_code} (no message)'
            print(msg, file=sys.stderr)
        else:
            try:
                msg = self._decode(rs.std_out)
            except Exception:
                msg = f'status code: {rs.status_code} (no message)'
            print(msg, file=sys.stdout)

        return rs.status_code

    def _open_shell(self):
        assert self.protocol is None and self.shell_id is None
        # WinRM port 5985 is http and 5986 is https
        proto = 'http' if self.port == 5985 else 'https'
        env_vars = {
            k: v for k, v in dict(os.environ).items()
            if k not in IGNORE_ENV
        }

        self.protocol = Protocol(
            endpoint=f'{proto}://{self.host}:{self.port}/wsman',
            transport='ntlm',
            username=self.username,
            password=self.password,
            server_cert_validation='ignore')
        try:
            self.shell_id = self.protocol.open_shell(env_vars=env_vars)
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
            ps_script = f.read()
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
    port_str = from_env((m.group(5) or f'{DEFAULT_PORT}').strip())
    try:
        port = int(port_str or f'{DEFAULT_PORT}')
        assert 1 <= port < 2**16
    except Exception:
        print(
            'Port must be a number between 1 and 65535.\n'
            'Default is 5986 (HTTPS), use 5985 for HTTP.).\n'
            f'Got: {port_str}', file=sys.stderr)
        sys.exit(ERR_INVALID_PORT_NUMBER)

    sess = Session(username, password, hostname, port)
    try:
        rc = sess.query(ps_script)
    except Exception as e:
        msg = str(e) or type(e).__name__
        print(msg, file=sys.stderr)
        sys.exit(ERR_FAILED_TO_EXEC)
    else:
        sys.exit(rc)
