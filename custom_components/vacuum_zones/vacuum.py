import asyncio
import logging

from homeassistant.components.vacuum import (
    StateVacuumEntity,
    VacuumEntityFeature,
    DOMAIN as VACUUM_DOMAIN,
)
from homeassistant.const import (
    CONF_SEQUENCE,
    STATE_IDLE,
    STATE_PAUSED,
    EVENT_STATE_CHANGED,
    ATTR_ENTITY_ID,
)
from homeassistant.core import Context, Event, State
from homeassistant.helpers import entity_registry
from homeassistant.helpers.script import Script

_LOGGER = logging.getLogger(__name__)

try:
    # trying to import new constants from VacuumActivity HA Core 2026.1
    from homeassistant.components.vacuum import VacuumActivity

    STATE_CLEANING = VacuumActivity.CLEANING
    STATE_RETURNING = VacuumActivity.RETURNING
    STATE_DOCKED = VacuumActivity.DOCKED
    STATE_IDLE = VacuumActivity.IDLE
    STATE_PAUSED = VacuumActivity.PAUSED
except ImportError:
    # if the new constants are unavailable, use the old ones
    from homeassistant.components.vacuum import (
        STATE_CLEANING,
        STATE_RETURNING,
        STATE_DOCKED,
    )
    from homeassistant.const import (
        STATE_IDLE,
        STATE_PAUSED,
    )


async def async_setup_platform(hass, _, async_add_entities, discovery_info=None):
    entity_id: str = discovery_info["entity_id"]
    queue: list[ZoneVacuum] = []
    queue_lock = asyncio.Lock()
    entities = [
        ZoneVacuum(name, config, entity_id, queue, queue_lock)
        for name, config in discovery_info["zones"].items()
    ]
    async_add_entities(entities)

    async def state_changed_event_listener(event: Event):
        if entity_id != event.data.get(ATTR_ENTITY_ID):
            return

        new_state: State = event.data.get("new_state")
        if not new_state:
            return
        vacuum_state = new_state.attributes.get("vacuum_state")
        _LOGGER.debug("New state received: %s", new_state.state)
        _LOGGER.debug("New vacuum state received: %s", vacuum_state)

        async with queue_lock:
            if not queue:
                return

            if new_state.state == STATE_DOCKED:
                for vacuum in queue:
                    await vacuum.internal_stop()
                queue.clear()
                return

            if new_state.state != STATE_RETURNING:
                return

            if vacuum_state and vacuum_state != "returning":
                return

            prev: ZoneVacuum = queue.pop(0)
            await prev.internal_stop()

            if not queue:
                return

            next_: ZoneVacuum = queue[0]
            await next_.internal_start(event.context)

    hass.bus.async_listen(EVENT_STATE_CHANGED, state_changed_event_listener)


class ZoneVacuum(StateVacuumEntity):
    _attr_state = STATE_IDLE
    _attr_supported_features = VacuumEntityFeature.START | VacuumEntityFeature.STOP

    domain: str = None
    service: str = None
    script: Script = None

    def __init__(
            self,
            name: str,
            config: dict,
            entity_id: str,
            queue: list,
            queue_lock: asyncio.Lock,
    ):
        self._attr_name = config.pop("name", name)
        self.service_data: dict = config | {ATTR_ENTITY_ID: entity_id}
        self.queue = queue
        self.queue_lock = queue_lock

    @property
    def vacuum_entity_id(self) -> str:
        return self.service_data[ATTR_ENTITY_ID]

    async def async_added_to_hass(self):
        # init start script
        if sequence := self.service_data.pop(CONF_SEQUENCE, None):
            self.script = Script(self.hass, sequence, self.name, VACUUM_DOMAIN)

        # get entity domain
        # https://github.com/home-assistant/core/blob/dev/homeassistant/components/xiaomi_miio/services.yaml
        # https://github.com/Tasshack/dreame-vacuum/blob/master/custom_components/dreame_vacuum/services.yaml
        # https://github.com/humbertogontijo/homeassistant-roborock/blob/main/custom_components/roborock/services.yaml
        entry = entity_registry.async_get(self.hass).async_get(self.vacuum_entity_id)
        self.domain = entry.platform

        # migrate service field names
        if room := self.service_data.pop("room", None):
            self.service_data["segments"] = room
        if goto := self.service_data.pop("goto", None):
            self.service_data["x_coord"] = goto[0]
            self.service_data["y_coord"] = goto[1]

        if "segments" in self.service_data:
            # "xiaomi_miio", "dreame_vacuum", "roborock"
            self.service = "vacuum_clean_segment"
        elif "zone" in self.service_data:
            # "xiaomi_miio", "dreame_vacuum", "roborock"
            if self.domain == "xiaomi_miio":
                self.service_data.setdefault("repeats", 1)
            self.service = "vacuum_clean_zone"
        elif "x_coord" in self.service_data and "y_coord" in self.service_data:
            # "xiaomi_miio", "roborock"
            self.service = "vacuum_goto"

    async def internal_start(self, context: Context) -> None:
        self._attr_state = STATE_CLEANING
        self.async_write_ha_state()
        _LOGGER.debug("Vacuum stared: %s", self._attr_name)

        if self.script:
            await self.script.async_run(context=context)

        if self.service:
            cleaning_mode = self.service_data.pop("cleaning_mode", "sweeping")

            if cleaning_mode in ("routine_cleaning", "deep_cleaning"):
                await self.hass.services.async_call(
                    "select",
                    "select_option",
                    {
                        ATTR_ENTITY_ID: "select.x40_ultra_complete_cleangenius",
                        "option": cleaning_mode,
                    },
                    blocking=True,
                )
                await self.hass.services.async_call(
                    "select",
                    "select_option",
                    {
                        ATTR_ENTITY_ID: "select.x40_ultra_complete_cleangenius_mode",
                        "option": "vacuum_and_mop",
                    },
                    blocking=True,
                )
            else:
                await self.hass.services.async_call(
                    "select",
                    "select_option",
                    {
                        ATTR_ENTITY_ID: "select.x40_ultra_complete_cleangenius",
                        "option": "off",
                    },
                    blocking=True,
                )
                await self.hass.services.async_call(
                    "select",
                    "select_option",
                    {
                        ATTR_ENTITY_ID: "select.x40_ultra_complete_cleaning_mode",
                        "option": cleaning_mode,
                    },
                    blocking=True,
                )

            await self.hass.services.async_call(
                self.domain,
                self.service,
                self.service_data,
                blocking=True,
            )

    async def internal_stop(self):
        self._attr_state = STATE_IDLE
        self.async_write_ha_state()
        _LOGGER.debug("Vacuum stopped: %s", self._attr_name)

    async def async_start(self):
        async with self.queue_lock:
            _LOGGER.debug("Vacuum start request: %s", self._attr_name)
            self.queue.append(self)

            state = self.hass.states.get(self.vacuum_entity_id)
            if len(self.queue) > 1 or state == STATE_CLEANING:
                self._attr_state = STATE_PAUSED
                self.async_write_ha_state()
                _LOGGER.debug("Vacuum paused: %s", self._attr_name)
                return

            await self.internal_start(self._context)

    async def async_stop(self, **kwargs):
        async with self.queue_lock:
            _LOGGER.debug("Vacuum stop request: %s", self._attr_name)

            for vacuum in self.queue:
                await vacuum.internal_stop()
            self.queue.clear()

            await self.internal_stop()
