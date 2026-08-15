"""Config flow for CEC Control."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)

from .backend import BackendError, BackendUnavailable, DaemonBackend
from .const import (
    ADDRESS_TV,
    BACKEND_DAEMON,
    BACKEND_LOCAL,
    CONF_ADAPTER,
    CONF_BACKEND,
    CONF_DEVICE_ADDRESS,
    DEFAULT_DAEMON_PORT,
    DOMAIN,
)
from .libcec_driver import detect_adapters

_LOGGER = logging.getLogger(__name__)

_ADAPTER_MANUAL = "__manual__"


def _list_adapters_sync() -> list[str]:
    """Return the CEC adapters attached to this machine."""
    return detect_adapters()


async def async_discover_adapters(hass: HomeAssistant) -> tuple[list[str], str]:
    """Return locally attached adapters, and why the list is empty if it is.

    Discovery failing must not break the flow — entering a path by hand still
    works, and the daemon backend does not need libcec here at all. But it must
    not fail *silently* either: "no adapters found" and "libcec is not installed"
    send someone looking in completely different places.
    """
    try:
        return await hass.async_add_executor_job(_list_adapters_sync), ""
    except BackendError as err:
        _LOGGER.warning("CEC adapter discovery unavailable: %s", err)
        return [], str(err)
    except Exception as err:
        _LOGGER.exception("CEC adapter discovery failed")
        return [], f"{type(err).__name__}: {err}"


class CecControlConfigFlow(ConfigFlow, domain=DOMAIN):
    """Configure one CEC adapter, local or remote."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask which kind of adapter to add."""
        return self.async_show_menu(
            step_id="user", menu_options=[BACKEND_LOCAL, BACKEND_DAEMON]
        )

    async def async_step_local(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure an adapter attached to this machine."""
        errors: dict[str, str] = {}
        adapters, discovery_problem = await async_discover_adapters(self.hass)

        if user_input is not None:
            adapter = str(user_input.get(CONF_ADAPTER) or "").strip()
            if adapter == _ADAPTER_MANUAL:
                adapter = str(user_input.get("manual_adapter") or "").strip()
            if not adapter:
                errors[CONF_ADAPTER] = "adapter_required"
            else:
                address = int(user_input.get(CONF_DEVICE_ADDRESS, ADDRESS_TV))
                await self.async_set_unique_id(f"local:{adapter}:{address}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"CEC ({adapter})",
                    data={
                        CONF_BACKEND: BACKEND_LOCAL,
                        CONF_ADAPTER: adapter,
                        CONF_DEVICE_ADDRESS: address,
                    },
                )

        options = [{"value": path, "label": path} for path in adapters]
        options.append({"value": _ADAPTER_MANUAL, "label": "Enter a path manually"})
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_ADAPTER,
                    default=adapters[0] if adapters else _ADAPTER_MANUAL,
                ): SelectSelector(
                    SelectSelectorConfig(options=options, mode=SelectSelectorMode.LIST)
                ),
                vol.Optional("manual_adapter", default=""): TextSelector(),
                vol.Required(CONF_DEVICE_ADDRESS, default=ADDRESS_TV): NumberSelector(
                    NumberSelectorConfig(min=0, max=15, mode=NumberSelectorMode.BOX)
                ),
            }
        )
        return self.async_show_form(
            step_id=BACKEND_LOCAL,
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "found": ", ".join(adapters)
                if adapters
                else (discovery_problem or "none")
            },
        )

    async def async_step_daemon(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure an adapter reached over HTTP."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = str(user_input[CONF_HOST]).strip()
            port = int(user_input.get(CONF_PORT, DEFAULT_DAEMON_PORT))
            address = int(user_input.get(CONF_DEVICE_ADDRESS, ADDRESS_TV))
            backend = DaemonBackend(async_get_clientsession(self.hass), host, port)
            try:
                health = await backend.async_health()
            except BackendUnavailable:
                errors["base"] = "cannot_connect"
            except BackendError:
                errors["base"] = "unknown"
            else:
                if health.get("service") != "cec-control-daemon":
                    errors["base"] = "not_a_daemon"
                elif not health.get("device_ok"):
                    errors["base"] = "adapter_unavailable"
                else:
                    await self.async_set_unique_id(f"daemon:{host}:{port}:{address}")
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=f"CEC ({host}:{port})",
                        data={
                            CONF_BACKEND: BACKEND_DAEMON,
                            CONF_HOST: host,
                            CONF_PORT: port,
                            CONF_DEVICE_ADDRESS: address,
                        },
                    )

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=""): TextSelector(),
                vol.Required(CONF_PORT, default=DEFAULT_DAEMON_PORT): NumberSelector(
                    NumberSelectorConfig(min=1, max=65535, mode=NumberSelectorMode.BOX)
                ),
                vol.Required(CONF_DEVICE_ADDRESS, default=ADDRESS_TV): NumberSelector(
                    NumberSelectorConfig(min=0, max=15, mode=NumberSelectorMode.BOX)
                ),
            }
        )
        return self.async_show_form(
            step_id=BACKEND_DAEMON, data_schema=schema, errors=errors
        )
