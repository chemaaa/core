"""Config flow for Universal Devices ISY994 integration."""
import logging
from urllib.parse import urlparse

from pyisy.configuration import Configuration
from pyisy.connection import Connection
import voluptuous as vol

from homeassistant import config_entries, core, exceptions
from homeassistant.components import ssdp
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback

from .const import (
    CONF_IGNORE_STRING,
    CONF_RESTORE_LIGHT_STATE,
    CONF_SENSOR_STRING,
    CONF_TLS_VER,
    CONF_VAR_SENSOR_STRING,
    DEFAULT_IGNORE_STRING,
    DEFAULT_RESTORE_LIGHT_STATE,
    DEFAULT_SENSOR_STRING,
    DEFAULT_TLS_VERSION,
    DEFAULT_VAR_SENSOR_STRING,
    DOMAIN,
    ISY_URL_POSTFIX,
    UDN_UUID_PREFIX,
)

_LOGGER = logging.getLogger(__name__)


def _data_schema(schema_input):
    """Generate schema with defaults."""
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=schema_input.get(CONF_HOST, "")): str,
            vol.Required(CONF_USERNAME): str,
            vol.Required(CONF_PASSWORD): str,
            vol.Optional(CONF_TLS_VER, default=DEFAULT_TLS_VERSION): vol.In([1.1, 1.2]),
        },
        extra=vol.ALLOW_EXTRA,
    )


async def validate_input(hass: core.HomeAssistant, data):
    """Validate the user input allows us to connect.

    Data has the keys from DATA_SCHEMA with values provided by the user.
    """
    user = data[CONF_USERNAME]
    password = data[CONF_PASSWORD]
    host = urlparse(data[CONF_HOST])
    tls_version = data.get(CONF_TLS_VER)

    if host.scheme == "http":
        https = False
        port = host.port or 80
    elif host.scheme == "https":
        https = True
        port = host.port or 443
    else:
        _LOGGER.error("The isy994 host value in configuration is invalid")
        raise InvalidHost

    # Connect to ISY controller.
    isy_conf = await hass.async_add_executor_job(
        _fetch_isy_configuration,
        host.hostname,
        port,
        user,
        password,
        https,
        tls_version,
        host.path,
    )

    if not isy_conf or "name" not in isy_conf or not isy_conf["name"]:
        raise CannotConnect

    # Return info that you want to store in the config entry.
    return {"title": f"{isy_conf['name']} ({host.hostname})", "uuid": isy_conf["uuid"]}


def _fetch_isy_configuration(
    address, port, username, password, use_https, tls_ver, webroot
):
    """Validate and fetch the configuration from the ISY."""
    try:
        isy_conn = Connection(
            address,
            port,
            username,
            password,
            use_https,
            tls_ver,
            webroot=webroot,
        )
    except ValueError as err:
        raise InvalidAuth(err.args[0]) from err

    return Configuration(xml=isy_conn.get_config())


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Universal Devices ISY994."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_PUSH

    def __init__(self):
        """Initialize the isy994 config flow."""
        self.discovered_conf = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return OptionsFlowHandler(config_entry)

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}
        info = None
        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidHost:
                errors["base"] = "invalid_host"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"

            if not errors:
                await self.async_set_unique_id(info["uuid"], raise_on_progress=False)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=info["title"], data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=_data_schema(self.discovered_conf),
            errors=errors,
        )

    async def async_step_import(self, user_input):
        """Handle import."""
        return await self.async_step_user(user_input)

    async def async_step_ssdp(self, discovery_info):
        """Handle a discovered isy994."""
        friendly_name = discovery_info[ssdp.ATTR_UPNP_FRIENDLY_NAME]
        url = discovery_info[ssdp.ATTR_SSDP_LOCATION]
        mac = discovery_info[ssdp.ATTR_UPNP_UDN]
        if mac.startswith(UDN_UUID_PREFIX):
            mac = mac[len(UDN_UUID_PREFIX) :]
        if url.endswith(ISY_URL_POSTFIX):
            url = url[: -len(ISY_URL_POSTFIX)]

        await self.async_set_unique_id(mac)
        self._abort_if_unique_id_configured()

        self.discovered_conf = {
            CONF_NAME: friendly_name,
            CONF_HOST: url,
        }

        self.context["title_placeholders"] = self.discovered_conf
        return await self.async_step_user()


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle a option flow for isy994."""

    def __init__(self, config_entry: config_entries.ConfigEntry):
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        """Handle options flow."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self.config_entry.options
        restore_light_state = options.get(
            CONF_RESTORE_LIGHT_STATE, DEFAULT_RESTORE_LIGHT_STATE
        )
        ignore_string = options.get(CONF_IGNORE_STRING, DEFAULT_IGNORE_STRING)
        sensor_string = options.get(CONF_SENSOR_STRING, DEFAULT_SENSOR_STRING)
        var_sensor_string = options.get(
            CONF_VAR_SENSOR_STRING, DEFAULT_VAR_SENSOR_STRING
        )

        options_schema = vol.Schema(
            {
                vol.Optional(CONF_IGNORE_STRING, default=ignore_string): str,
                vol.Optional(CONF_SENSOR_STRING, default=sensor_string): str,
                vol.Optional(CONF_VAR_SENSOR_STRING, default=var_sensor_string): str,
                vol.Required(
                    CONF_RESTORE_LIGHT_STATE, default=restore_light_state
                ): bool,
            }
        )

        return self.async_show_form(step_id="init", data_schema=options_schema)


class InvalidHost(exceptions.HomeAssistantError):
    """Error to indicate the host value is invalid."""


class CannotConnect(exceptions.HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(exceptions.HomeAssistantError):
    """Error to indicate there is invalid auth."""
