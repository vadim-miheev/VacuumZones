import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.const import CONF_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.helpers.discovery import async_load_platform

DOMAIN = "vacuum_zones"
PLATFORMS = ["vacuum", "binary_sensor"]

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required(CONF_ENTITY_ID): cv.entity_id,
                vol.Required("zones"): {
                    cv.string: vol.Schema(
                        {
                            vol.Optional("name"): str,
                            vol.Required("room"): vol.Any(list, int),
                            vol.Optional("cleaning_mode"): str,
                        },
                        extra=vol.ALLOW_EXTRA,
                    )
                },
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass: HomeAssistant, config: dict):
    domain_config = config[DOMAIN]

    # Initialize hass.data for our domain
    hass.data.setdefault(DOMAIN, {})

    # Load vacuum platform
    hass.async_create_task(
        async_load_platform(hass, "vacuum", DOMAIN, domain_config, config)
    )

    # Load binary_sensor platform
    hass.async_create_task(
        async_load_platform(hass, "binary_sensor", DOMAIN, domain_config, config)
    )

    return True
