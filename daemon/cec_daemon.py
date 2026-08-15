#!/usr/bin/env python3
"""CEC Control daemon — expose a locally attached CEC adapter over HTTP.

Run this on the machine the adapter is plugged into when that machine is not
the one running Home Assistant. The `cec_control` integration in this same
repository speaks to it with its daemon backend, and behaves identically to
when it drives an adapter directly.

This is a transport, not a bridge: it carries raw CEC addresses and opcodes and
knows nothing about televisions, key names or Home Assistant. Keeping the
vocabulary out of here is deliberate — the integration owns the key table, so
the two sides never have to agree on more than numbers.

The contract (status codes, error shape, /health) is documented in
`docs/daemon-contract.md`.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any

from aiohttp import web

# Kept in step with the repository VERSION file by scripts/version.py.
VERSION = "0.1.0"

SERVICE = "cec-control-daemon"

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
_LOGGER = logging.getLogger("cec-control-daemon")


# The driver lives with the integration, because that is where it is edited and
# reviewed. Loading it by path rather than importing the package keeps the
# daemon free of Home Assistant, which is not installed on the machine the
# adapter is plugged into.
_DRIVER_PATH = Path(__file__).resolve().parents[1] / (
    "custom_components/cec_control/libcec_driver.py"
)
_spec = importlib.util.spec_from_file_location(
    "cec_control_libcec_driver", _DRIVER_PATH
)
if _spec is None or _spec.loader is None:  # pragma: no cover - packaging error
    raise RuntimeError(f"cannot load the libcec driver from {_DRIVER_PATH}")
libcec_driver = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(libcec_driver)

AdapterUnavailable = libcec_driver.DriverUnavailable
CecRejected = libcec_driver.DriverError


class CecAdapter:
    """Own the process-wide libcec adapter.

    libcec opens per process and only one process may hold a given adapter, so
    this is a singleton by construction. Calls are serialised because libcec is
    not safe to drive from several threads at once, and each one is short.
    """

    def __init__(self, adapter: str | None = None) -> None:
        """Record which adapter to open on first use."""
        self._adapter = adapter
        self._driver: Any = None
        self._lock = asyncio.Lock()
        self._guard = libcec_driver.OpenGuard()

    @property
    def adapter(self) -> str | None:
        """Return the configured adapter path, if one was given."""
        return self._adapter

    def _open_sync(self) -> Any:
        """Create and open the driver, blocking."""
        driver = libcec_driver.create_driver(self._adapter)
        driver.open()
        return driver

    async def _ensure_open(self) -> Any:
        """Open the adapter once, bounding how long we wait for it."""
        if self._driver is not None:
            return self._driver
        blocked = self._guard.blocked()
        if blocked:
            raise AdapterUnavailable(blocked)
        loop = asyncio.get_running_loop()
        try:
            self._driver = await asyncio.wait_for(
                loop.run_in_executor(None, self._open_sync),
                timeout=libcec_driver.OPEN_WAIT_SECONDS,
            )
        except TimeoutError:
            message = libcec_driver.adapter_busy_message(self._adapter)
            self._guard.record_failure(message)
            raise AdapterUnavailable(message) from None
        self._guard.record_success()
        return self._driver

    async def _run(self, method: str, *args: Any) -> Any:
        """Run one driver method in a thread, under the lock."""
        async with self._lock:
            driver = await self._ensure_open()
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, getattr(driver, method), *args)

    async def scan(self) -> list[dict[str, Any]]:
        """Return the devices currently answering on the bus."""
        return await self._run("scan")

    async def transmit(self, destination: int, opcode: int, params: bytes) -> None:
        """Send one raw CEC message."""
        if not await self._run("transmit", destination, opcode, params):
            raise CecRejected(
                f"CEC rejected opcode 0x{opcode:02X} to address {destination}"
            )

    async def power(self, destination: int, turn_on: bool) -> None:
        """Power a device on, or put it into standby."""
        if not await self._run("power", destination, turn_on):
            raise CecRejected(
                f"{'power on' if turn_on else 'standby'} to address "
                f"{destination} was rejected"
            )

    async def set_active_source(self) -> None:
        """Declare this adapter to be the active source."""
        if not await self._run("set_active_source"):
            raise CecRejected("setting the active source was rejected")

    async def health(self) -> dict[str, Any]:
        """Report whether the adapter is usable, without failing the request."""
        try:
            await self._run("scan")
        except AdapterUnavailable as err:
            return {"device_ok": False, "detail": str(err)}
        except CecRejected as err:
            return {"device_ok": False, "detail": str(err)}
        return {"device_ok": True, "detail": ""}


def _json(status: int, payload: dict[str, Any]) -> web.Response:
    """Return one JSON response."""
    return web.json_response(payload, status=status)


def _error(status: int, message: str) -> web.Response:
    """Return one error in the shape the contract requires."""
    return _json(status, {"error": message})


async def _read_json(request: web.Request) -> dict[str, Any]:
    """Parse a JSON object body, or raise a 400."""
    if not request.can_read_body:
        return {}
    try:
        data = await request.json()
    except ValueError as err:
        raise web.HTTPBadRequest(
            text=f'{{"error": "invalid JSON body: {err}"}}',
            content_type="application/json",
        ) from err
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise web.HTTPBadRequest(
            text='{"error": "JSON body must be an object"}',
            content_type="application/json",
        )
    return data


def _require_int(data: dict[str, Any], key: str, minimum: int, maximum: int) -> int:
    """Return one integer field, or raise a 400."""
    value = data.get(key)
    if value is None:
        raise web.HTTPBadRequest(
            text=f'{{"error": "{key} is required"}}', content_type="application/json"
        )
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise web.HTTPBadRequest(
            text=f'{{"error": "{key} must be an integer"}}',
            content_type="application/json",
        )
    try:
        number = int(value)
    except ValueError:
        raise web.HTTPBadRequest(
            text=f'{{"error": "{key} must be an integer"}}',
            content_type="application/json",
        ) from None
    if not minimum <= number <= maximum:
        raise web.HTTPBadRequest(
            text=f'{{"error": "{key} must be between {minimum} and {maximum}"}}',
            content_type="application/json",
        )
    return number


def _require_hex(data: dict[str, Any], key: str) -> bytes:
    """Return one optional hex-string field as bytes, or raise a 400."""
    value = data.get(key, "")
    if value in (None, ""):
        return b""
    if not isinstance(value, str):
        raise web.HTTPBadRequest(
            text=f'{{"error": "{key} must be a hex string"}}',
            content_type="application/json",
        )
    try:
        return bytes.fromhex(value.replace(":", "").replace(" ", ""))
    except ValueError:
        raise web.HTTPBadRequest(
            text=f'{{"error": "{key} must be a hex string, e.g. \\"3500\\""}}',
            content_type="application/json",
        ) from None


def build_app(adapter: CecAdapter) -> web.Application:
    """Return the daemon's HTTP application."""

    async def handle_health(request: web.Request) -> web.Response:
        """Report the service and whether its adapter is usable.

        Always 200 while the daemon itself is running: a missing adapter is
        reported in the body, because "the daemon is down" and "the adapter is
        unplugged" are different problems for the caller.
        """
        state = await adapter.health()
        return _json(
            200,
            {
                "service": SERVICE,
                "version": VERSION,
                "device_ok": state["device_ok"],
                "adapter": adapter.adapter,
                "detail": state["detail"],
            },
        )

    async def handle_devices(request: web.Request) -> web.Response:
        """Return the devices answering on the bus."""
        return _json(200, {"devices": await adapter.scan()})

    async def handle_transmit(request: web.Request) -> web.Response:
        """Send one raw CEC message."""
        data = await _read_json(request)
        destination = _require_int(data, "destination", 0, 15)
        opcode = _require_int(data, "opcode", 0, 0xFF)
        params = _require_hex(data, "params")
        await adapter.transmit(destination, opcode, params)
        return _json(200, {"status": "ok"})

    async def handle_power(request: web.Request) -> web.Response:
        """Power a device on, or put it into standby."""
        data = await _read_json(request)
        destination = _require_int(data, "destination", 0, 15)
        action = data.get("action", "on")
        if action not in ("on", "off"):
            return _error(400, "action must be 'on' or 'off'")
        await adapter.power(destination, action == "on")
        return _json(200, {"status": "ok"})

    async def handle_active_source(request: web.Request) -> web.Response:
        """Declare this adapter to be the active source."""
        await adapter.set_active_source()
        return _json(200, {"status": "ok"})

    @web.middleware
    async def translate_errors(request: web.Request, handler: Any) -> web.Response:
        """Map internal failures onto the contract's status codes."""
        try:
            return await handler(request)
        except web.HTTPException:
            raise
        except AdapterUnavailable as err:
            _LOGGER.warning("adapter unavailable: %s", err)
            return _error(503, str(err))
        except CecRejected as err:
            # The bus did not acknowledge. The request was well formed and the
            # adapter is fine, so this is the adapter's side failing: 503.
            _LOGGER.warning("CEC rejected: %s", err)
            return _error(503, str(err))
        except Exception as err:
            _LOGGER.exception("unhandled error")
            return _error(500, f"{type(err).__name__}: {err}")

    async def handle_not_found(request: web.Request) -> web.Response:
        """Answer unknown paths in the contract's error shape."""
        return _error(404, f"no such endpoint: {request.path}")

    app = web.Application(middlewares=[translate_errors])
    app.router.add_get("/health", handle_health)
    app.router.add_get("/devices", handle_devices)
    app.router.add_post("/transmit", handle_transmit)
    app.router.add_post("/power", handle_power)
    app.router.add_post("/active_source", handle_active_source)
    app.router.add_route("*", "/{tail:.*}", handle_not_found)
    return app


def main(argv: list[str] | None = None) -> int:
    """Run the daemon."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument(
        "--adapter",
        default=None,
        help="CEC adapter to open, e.g. /dev/ttyACM0 (default: auto-detect)",
    )
    parser.add_argument("--version", action="version", version=f"{SERVICE} {VERSION}")
    args = parser.parse_args(argv)

    _LOGGER.info("%s %s listening on %s:%s", SERVICE, VERSION, args.host, args.port)
    web.run_app(
        build_app(CecAdapter(args.adapter)),
        host=args.host,
        port=args.port,
        print=None,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
