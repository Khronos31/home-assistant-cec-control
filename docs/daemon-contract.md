# Daemon HTTP contract

What `daemon/cec_daemon.py` promises and what `custom_components/cec_control`
relies on. `tests/test_daemon.py` checks the daemon's side, and
`tests/test_backend.py` wires the real client to the real daemon so the two
cannot drift apart while each looks correct alone.

## Scope

The daemon is a transport. It carries CEC addresses, opcodes and payload bytes,
and knows nothing about televisions, key names or Home Assistant. The key table
lives in the integration, so the two halves never have to agree on a vocabulary
— only on numbers. That is deliberate: it is what lets the daemon stay stable
while the integration grows.

## Status codes

The integration branches on exactly these, so they carry the meaning rather
than the message:

| Code | Meaning | What the integration does |
| --- | --- | --- |
| `200` | Done. | Continue. |
| `400` | The request was malformed. | Surface as a bug; retrying will not help. |
| `404` | No such endpoint. | Same. Usually a version mismatch. |
| `409` | An exclusive resource is busy. | Not currently emitted; reserved so a future daemon that owns something exclusive can say so without inventing a code. |
| `503` | The request was fine; the CEC side is not usable. | Treat as temporary — the adapter is unplugged, the bus did not acknowledge, the television is off. Retry later. |
| `500` | A bug in the daemon. | Surface with the message. |

The distinction that matters is **400 versus 503**: one says the caller is
wrong, the other says the hardware is not cooperating. Collapsing them would
make the integration either retry forever on a real bug or give up on a
television that was merely unplugged for a minute.

## Error body

Every non-2xx response, including `404`, is:

```json
{"error": "one human-readable sentence"}
```

One shape for every failure means one parser on the client side. Extra keys may
be added alongside `error`; clients must ignore what they do not know.

## Endpoints

### `GET /health`

```json
{
  "service": "cec-control-daemon",
  "version": "0.1.0",
  "device_ok": true,
  "adapter": "/dev/ttyACM0",
  "detail": ""
}
```

**Always `200` while the daemon is running**, even when the adapter is missing —
in that case `device_ok` is `false` and `detail` says why. "The daemon is down"
and "the adapter is unplugged" are different problems with different fixes, and
a client that cannot tell them apart will report the wrong one. A dead daemon is
a connection error; there is no status code needed to express it.

`service` lets the config flow reject something else listening on that port.
`version` lets it reject a daemon left behind by an upgrade.

### `GET /devices`

```json
{"devices": [
  {"address": 0, "osd_string": "TV", "vendor": "000000",
   "physical_address": "0.0.0.0", "cec_version": "1.4", "is_on": true}
]}
```

One entry per logical address that answered the poll. `is_on` is what the
coordinator turns into entity state.

### `POST /transmit`

```json
{"destination": 0, "opcode": 68, "params": "35"}
```

`params` is a hex string and may use colons (`"10:00"`), because CEC is written
with colons everywhere else. Omit it for an empty payload. The source address is
whatever the adapter holds and is never named by the caller.

### `POST /power`

```json
{"destination": 0, "action": "on"}
```

`on` goes through libcec's own power-on, which is more than one message on most
televisions. `off` sends a plain `STANDBY`.

### `POST /active_source`

No body. Declares the daemon's adapter to be the active source, which on most
televisions switches the input to whatever the adapter is plugged into.

## What is deliberately absent

**Authentication.** The daemon is a LAN service with no credentials to protect;
CEC can turn a television on and off, which is the same authority as the remote
sitting on the sofa. If that changes, the natural extension is an optional
`X-Bridge-Token` header — but building it before it is needed would be
speculative, so it is not there.

**A key vocabulary.** See *Scope*.
