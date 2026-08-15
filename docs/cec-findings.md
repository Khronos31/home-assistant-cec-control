# Measured behaviour

Everything here was observed against a Pulse-Eight USB-CEC adapter (firmware 12,
2020-04-28) on libcec 7.0.0 with python-cec 0.2.8, driving a MAXZEN J32CH06
television, on 2026-08-16. Numbers that came from a datasheet rather than a
measurement are marked as such.

## The bus in this house

```
device #0  TV            0.0.0.0   CEC 1.4   vendor unknown
device #1  Recorder 1    1.0.0.0   CEC 1.4   the Pulse-Eight adapter itself
device #4  Playback 1    1.1.0.0   CEC 2.0   Google TV Streamer
```

The adapter takes **logical address 1**, not 4. This matters: an earlier
generation of this code assumed 4 (Playback 1) and built its frames from that,
which is why the frames recorded in `lua-remote-hub` all begin `10:` — source 1,
destination 0. The integration never hardcodes a source; libcec fills in
whatever address it actually holds.

## Timing

| Action | Time until the bus reports the new state |
| --- | --- |
| Power on | ~1 second |
| Standby | ~8 seconds |

The asymmetry is why `media_player` reports an optimistic state for
`OPTIMISTIC_STATE_SECONDS` after a command. Without it, turning the television
off leaves the UI showing "on" for the better part of ten seconds, and people
press the button again.

## A libcec quirk that aborts the process

**A `cec.Device` object that is still alive when the interpreter shuts down
kills the process**:

```
FATAL: exception not rethrown
Aborted (core dumped)
```

Narrowed down by elimination:

| What the script did | Exit |
| --- | --- |
| `import cec` | clean |
| `cec.init()` | clean |
| `cec.init()`, `cec.list_devices()` | **abort** |
| `cec.init()`, `cec.list_devices()`, then `devices.clear(); del devices` | clean |
| `cec.init()`, `cec.transmit(...)` (no `Device` created) | clean |

So the rule both halves of this repository follow:

* Send through the **module-level** `cec.transmit()`, which never creates a
  `Device`.
* When a `Device` is unavoidable — `power_on()` is the only case, because
  powering a television on is more than one message — obtain it, use it, and
  clear the mapping before returning. Never store one on an object that outlives
  the call.

Getting this wrong would not show up in testing. It only bites at shutdown,
which for Home Assistant means a crash during restart, at the least convenient
possible moment.

## Keys

The table in `custom_components/cec_control/keymap.py` came from
`lua-remote-hub`, where it had been verified against this same television. The
keys re-checked here (`guide`, `return`, `screen_display`) transmit and are
acknowledged.

`lua-remote-hub` also records keys this television **ignores**, which is the
more valuable half of that file — they are carried over into `UNSUPPORTED_KEYS`
rather than deleted, so nobody rediscovers them:

`power` (toggle — the set only honours the discrete `power_on` / `power_off`),
`mode_digital`, `mode_bs`, `mode_cs`, `menu`, `exit`, `program_info`,
`subtitle`, `audio_select`, `back_10s`, `skip_30s`.

## One adapter, one process

libcec opens the adapter for the whole process, and only one process may hold a
given adapter at a time. Running the daemon on the same machine as a local-backend
config entry will therefore not work — whichever starts second cannot open the
device. This is a constraint of the hardware, not of this code, and it is why the
two transports are alternatives rather than a fallback chain.

## Environment note

Home Assistant 2026.3 and later require Python 3.14.2. The Studio Code Server
add-on this repository is developed in ships Python 3.13, so the test suite pins
`homeassistant==2026.2.3` — the newest release that still installs there. The
integration is *run* against the production version; only the unit tests use the
older one.
