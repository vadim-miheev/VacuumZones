import logging

from . import DOMAIN
from .vacuum import ZoneCoordinatorIsPending, ZoneCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_platform(hass, _, async_add_entities, discovery_info=None):
    """Set up the binary sensor platform for vacuum_zones."""
    entity_id: str = discovery_info["entity_id"]

    # Initialize hass.data structure for our domain
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault(entity_id, {})

    entry = hass.data[DOMAIN][entity_id]

    # Create or retrieve coordinator
    if "coordinator" not in entry:
        # First platform to load (usually vacuum) creates the coordinator
        coordinator = ZoneCoordinator(hass, entity_id)
        entry["coordinator"] = coordinator
    else:
        coordinator = entry["coordinator"]

    # Create binary sensor for coordinator pending state
    binary_sensor = ZoneCoordinatorIsPending(coordinator, entity_id)
    async_add_entities([binary_sensor])