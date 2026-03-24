[![CI](https://github.com/infrasonar/rapp-rx/workflows/CI/badge.svg)](https://github.com/infrasonar/rapp-rx/actions)
[![Release Version](https://img.shields.io/github/release/infrasonar/rapp-rx)](https://github.com/infrasonar/rapp-rx/releases)

# Remote Script Execution (via Rapp)

## Environment variable

Variable            | Default                       | Description
------------------- | ----------------------------- | ------------
`LOG_LEVEL`         | `warning`                     | Log level (`debug`, `info`, `warning`, `error` or `critical`).
`LOG_COLORIZED`     | `0`                           | Log using colors (`0`=disabled, `1`=enabled).
`LOG_FMT`           | `%y%m%d %H:%M:%S`             | Log format prefix.
`HTTP_PORT`         | `6214`                        | RX port
`WINRMX`            | `/code/winrmx.py`             | Script for WinRM (`.ps1`) execution.
