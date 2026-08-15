# CEC Control

Control your television from Home Assistant over HDMI-CEC, with a UI-driven
setup and an adapter that does not have to be plugged into the machine running
Home Assistant.

Current version: **0.1.0**.

Home Assistant already ships an `hdmi_cec` integration. This one exists because
that integration is YAML-only, has no config flow, and exposes little of what
CEC can actually do. `cec_control` is a separate integration with its own
domain — it does **not** replace or shadow the built-in one, and the two can
coexist.

## What you get

* A **media player** for the television: on, standby, input selection, channel
  entry, volume steps and mute.
* A **remote** for everything else, taking named keys (`guide`, `num_3`,
  `volume_up`, `power_off`, …) and a `raw:` escape hatch for opcodes the key
  table does not name.
* Real state: the television's power status is polled from the bus, so the
  entity reflects the set being switched on by its own remote.

## Two ways to reach the bus

The integration talks to a CEC adapter through one of two transports. They are
interchangeable: the entities cannot tell which is in use.

| | When to use it |
| --- | --- |
| **Local adapter** | The adapter is plugged into the machine running Home Assistant. Uses libcec's Python bindings, which the Home Assistant OS image already provides. |
| **Daemon** | The adapter is plugged into some other machine — a Raspberry Pi next to the television, say. Run `daemon/cec_daemon.py` there; the integration reaches it over HTTP. |

Neither is a bridge: nothing is translated between protocols, they are just two
routes to the same CEC bus.

## Installing the integration

Add this repository to HACS as a custom repository, or copy
`custom_components/cec_control/` into your Home Assistant `config` directory and
restart. Then add **CEC Control** from *Settings → Devices & services*, and pick
whether the adapter is local or remote.

For a local adapter, the setup form lists the adapters libcec can see. For a
remote one, give the daemon's host and port; setup fails early and clearly if
nothing answers, if the thing that answers is not this daemon, or if the daemon
is running but its adapter is not usable.

## Running the daemon

On the machine holding the adapter:

```bash
sudo apt install libcec-dev libcec7 python3-dev g++
git clone https://github.com/Khronos31/home-assistant-cec-control.git
cd home-assistant-cec-control
pip install -r daemon/requirements.txt
python3 daemon/cec_daemon.py --port 8080
```

Then copy `daemon/cec-control-daemon.service` to `/etc/systemd/system/`, adjust
the user and path inside it, and `systemctl enable --now cec-control-daemon`.

The HTTP contract is written down in [`docs/daemon-contract.md`](docs/daemon-contract.md).

## One version for both halves

The integration and the daemon are released together under a single version and
a single tag. The daemon reports its version from `GET /health`, so an
integration talking to a daemon left behind by an upgrade is a detectable
condition rather than a confusing bug. `scripts/version.py check` enforces that
every version representation in the repository agrees, and it runs in CI.

## Hardware notes

What was measured against a Pulse-Eight USB-CEC adapter and the television in
the author's study — including the key codes this television ignores, and a
libcec quirk that will crash your process if you hold the wrong object — is in
[`docs/cec-findings.md`](docs/cec-findings.md).

## Credits

The key table was lifted from
[Khronos31/lua-remote-hub](https://github.com/Khronos31/lua-remote-hub), where
it had already been verified against this television, and re-checked here.

## License

MIT.
