# Measured behaviour

Everything here was observed on 2026-08-16 against a Pulse-Eight USB-CEC adapter
(firmware 12, built 2020-04-28) driving a MAXZEN J32CH06 television. Numbers that
came from a datasheet rather than a measurement are marked as such.

## Two different modules are both called `cec`

This is the finding that shaped the whole design, and the one most likely to
waste someone's evening.

| | Home Assistant OS image | `pip install cec` |
| --- | --- | --- |
| Module | libcec's own **SWIG binding** | the **python-cec** package |
| File | `/usr/local/lib/python3.14/site-packages/cec.py` | `cec.cpython-*.so` |
| libcec | 7.1.1 | 7.0.0 (Debian trixie) |
| Entry points | `cec.ICECAdapter.Create(config)`, `cec.libcec_configuration` | `cec.init()`, `cec.list_devices()` |
| Keys | `SendKeypress` / `SendKeyRelease` | build `USER_CONTROL_PRESSED` / `RELEASE` by hand |

They share an import name and nothing else. Code written against one raises
`AttributeError` on the other — which is exactly what happened here: a config
flow that called `cec.list_adapters()` worked in the development container and
failed inside Home Assistant with `module 'cec' has no attribute
'list_adapters'`.

Debian packages neither binding; `libcec7` contains no Python files at all. The
SWIG binding appears when libcec is built with Python enabled, which the Home
Assistant image does. So `custom_components/cec_control/libcec_driver.py`
supports both and picks one by feature detection, because both are genuinely in
use in this house: the integration gets the SWIG binding, and the daemon on
another machine will realistically be running python-cec from pip.

### Traps inside the SWIG binding

* **`strDeviceName` is `char[13]`** and SWIG *rejects* a longer string rather
  than truncating it — `TypeError: argument 2 of type 'char [(13)]'`. This is
  why the adapter announces itself as `HA CEC`.
* **`Open()` returns false for adapters that work.** Khronos31/lua-remote-hub
  had already found this and settled on retrying and treating a successful
  `PingAdapter()` as success. The driver does the same.
* `DetectAdapters()` returns a tuple of descriptors; the path is `strComName`.

## The bus in this house

```
device #0  TV            0.0.0.0   CEC 1.4   vendor unknown   (MAXZEN J32CH06)
device #1  Recorder 1    1.0.0.0   CEC 1.4   Pulse Eight      (the adapter)
device #4  Playback 1    1.1.0.0   CEC 2.0   Google           (Google TV Streamer)
```

The adapter takes **logical address 1**, not 4. An earlier generation of this
code assumed 4 (Playback 1) and built its frames from that; the frames recorded
in `lua-remote-hub` all begin `10:` — source 1, destination 0 — because that is
what the hardware actually does. The driver never hardcodes a source: it asks
libcec with `GetLogicalAddresses().primary`.

## Timing

| Action | Time until the bus reports the new state |
| --- | --- |
| Power on | ~1 second |
| Standby | ~8 seconds |

The asymmetry is why `media_player` reports an optimistic state for
`OPTIMISTIC_STATE_SECONDS` after a command. Without it, turning the television
off leaves the UI showing "on" for the better part of ten seconds, and people
press the button again.

## A python-cec quirk that aborts the process

**A `cec.Device` object still alive when the interpreter shuts down kills the
process**:

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

So `PythonCecDriver` sends through the module-level `cec.transmit()`, which
never creates a `Device`, and clears the mapping from `list_devices()` before it
can escape. `power_on()` is the one place a `Device` is unavoidable, and it is
dropped in a `finally`. `tests/test_libcec_driver.py` asserts the clearing
happens, because getting this wrong would only bite at shutdown — for Home
Assistant, a crash during restart, at the least convenient possible moment.

## Keys

The table in `custom_components/cec_control/keymap.py` came from
`lua-remote-hub`, where it had been verified against this same television. All
of it was re-sent here on 2026-08-16 — cursor, menus, all ten digits, channel,
input, volume, mute, transport, the colour buttons and `data` — through libcec's
`SendKeypress`, and every one was acknowledged. Read the next section before
taking that as a stronger result than it is.

`lua-remote-hub` also records keys this television **ignores**, which is the
more valuable half of that file — they are carried over into `UNSUPPORTED_KEYS`
rather than deleted, so nobody rediscovers them:

`power` (toggle), `mode_digital`, `mode_bs`, `mode_cs`, `menu`, `exit`, `program_info`,
`subtitle`, `audio_select`, `back_10s`, `skip_30s`.

## Acknowledged is not the same as acted on

Every key in `keymap.py` was sent to this television on 2026-08-16 and all of
them were acknowledged. That is a weaker statement than it sounds: a CEC ACK
means the frame arrived and was addressed correctly, not that the set did
anything with it.

The clearest demonstration is the discrete power keys. `power_off`
(`USER_CONTROL` 0x6C) is **acknowledged and then ignored** — the television was
still reporting itself as on 28 seconds later. `media_player.turn_off`, which
sends the `STANDBY` opcode (0x36) instead, switches it off in about eight
seconds. So `power_on` / `power_off` remain in the key table because they are
valid CEC codes that other sets do honour, but on this television the opcode
path is the one that works, and that is what the media player uses.

Read the sweep as "no key in the table produces a protocol error", not as "every
key does something".

## Keys sent too soon after power-on are dropped

The first key sent about four seconds after `turn_on` was refused
(`CEC rejected key 0x00`); the same key sent once the set had finished waking
was accepted. The power status flips to on within a second, so it is not a
reliable signal that the television is ready for user-control messages. An
automation that powers the set on and immediately sends a key should expect to
retry.

## One adapter, one process

libcec opens the adapter for the whole process, and only one process may hold a
given adapter at a time. The two transports are therefore alternatives rather
than a fallback chain: switching between them means removing one config entry
before adding the other.

**What happens when you get this wrong is worse than a failure.** Measured on
2026-08-16, with Home Assistant holding the adapter and the daemon started
against the same device:

| | Before the fix | After |
| --- | --- | --- |
| `GET /health` | never answered | 200, `device_ok: false`, 13 ms |
| `POST /transmit` | never answered | 503 with the reason, 5 ms |
| `GET /nope` (touches no CEC) | never answered | 404, 1 ms |
| `SIGTERM` | ignored | honoured |

python-cec's `cec.init()` does not return when the adapter is taken — it blocks
**while holding the GIL**, so the interpreter stops running Python entirely.
That is why every endpoint died, not just the CEC ones, and why the process
needed `SIGKILL`. No timeout written in Python can help: `asyncio.wait_for`
never gets a chance to fire.

The fix is to detect the conflict before libcec is called. libcec takes an
exclusive `flock` on the device, so asking for the same lock non-blockingly
answers the question — and it works across container boundaries, where scanning
`/proc` does not, because the lock lives in the kernel and the process table
does not. See `adapter_is_held()` in `libcec_driver.py`.

The check is advisory: a holder that does not take the lock is invisible to it,
it needs an explicitly configured device path, and there is a race between the
check and `init()`. It converts the common accident from a freeze into a
sentence, which is what it is for.

## Environment note

Home Assistant 2026.3 and later require Python 3.14.2. The Studio Code Server
add-on this repository is developed in ships Python 3.13, so the test suite pins
`homeassistant==2026.2.3` — the newest release that still installs there. The
integration is *run* against the production version (2026.8.1 as of writing);
only the unit tests use the older one.

Service calls that fail validation come back from Home Assistant's REST API as
HTTP 500 regardless of the exception type. `ServiceValidationError` still
carries the useful message, and the websocket path that the UI uses reports it
properly; the REST status code is not something this integration controls.
