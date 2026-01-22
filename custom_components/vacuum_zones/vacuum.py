import asyncio
import logging

from homeassistant.components.vacuum import (
    StateVacuumEntity,
    VacuumEntityFeature,
)

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
)

from homeassistant.const import (
    STATE_IDLE,
    ATTR_ENTITY_ID,
)
from homeassistant.helpers import entity_registry
from . import DOMAIN

_LOGGER = logging.getLogger(__name__)

try:
    # trying to import new constants from VacuumActivity HA Core 2026.1
    from homeassistant.components.vacuum import VacuumActivity

    STATE_CLEANING = VacuumActivity.CLEANING
    STATE_IDLE = VacuumActivity.IDLE
except ImportError:
    # if the new constants are unavailable, use the old ones
    from homeassistant.components.vacuum import (
        STATE_CLEANING,
    )
    from homeassistant.const import (
        STATE_IDLE,
    )


class ZoneCoordinator:
    """Координатор для группировки запусков виртуальных пылесосов."""

    def __init__(self, hass, vacuum_entity_id):
        self.hass = hass
        self.vacuum_entity_id = vacuum_entity_id
        self.pending_groups = {}  # cleaning_mode -> list of ZoneVacuum
        self.timer_handle = None
        self.grouping_timeout = 2  # секунды
        self._listeners = []  # Callback functions for state changes

    def add_listener(self, callback):
        """Добавить слушатель для уведомлений об изменении pending_groups."""
        self._listeners.append(callback)

    def remove_listener(self, callback):
        """Удалить слушатель."""
        if callback in self._listeners:
            self._listeners.remove(callback)

    def _notify_listeners(self):
        """Уведомить всех слушателей об изменении состояния."""
        for callback in self._listeners:
            callback()

    async def schedule_cleaning(self, zone_vacuum):
        """Добавить виртуальный пылесос в группу и запустить/сбросить таймер."""
        cleaning_mode = zone_vacuum.cleaning_mode

        if cleaning_mode not in self.pending_groups:
            self.pending_groups[cleaning_mode] = []
        self.pending_groups[cleaning_mode].append(zone_vacuum)
        self._notify_listeners()

        # Запустить/сбросить таймер группировки
        if self.timer_handle:
            try:
                self.timer_handle.cancel()
            except Exception:
                pass  # Игнорируем ошибки отмены

        self.timer_handle = asyncio.create_task(self._execute_group_after_timeout())

    async def _execute_group_after_timeout(self):
        """Выполнить группу после таймаута."""
        try:
            await asyncio.sleep(self.grouping_timeout)
            await self._execute_group()
        except asyncio.CancelledError:
            # Таймер был отменен - это нормально
            raise
        except Exception as e:
            _LOGGER.error("Error executing group: %s", e)
            # Очищаем pending_groups при ошибке
            self.pending_groups.clear()
            self.timer_handle = None
            self._notify_listeners()

    async def _execute_group(self):
        """Выполнить накопленные группы."""
        if not self.pending_groups:
            return

        try:
            # Проверить состояние пылесоса и остановить если cleaning
            await self.check_and_stop_vacuum()

            # Подготовить данные для сервиса
            service_data = await self._prepare_service_data()

            # Выполнить сервисный вызов
            await self._call_services(service_data)

        except Exception as e:
            _LOGGER.error("Error executing cleaning group: %s", e)
        finally:
            # Всегда очищаем pending_groups после выполнения или ошибки
            self.pending_groups.clear()
            self.timer_handle = None
            self._notify_listeners()

    async def check_and_stop_vacuum(self):
        """Остановить пылесос если он в состоянии cleaning."""
        state = self.hass.states.get(self.vacuum_entity_id)
        if state and state.state == STATE_CLEANING:
            _LOGGER.debug("Vacuum is cleaning, stopping before new command")
            await self.hass.services.async_call(
                "vacuum",
                "stop",
                {ATTR_ENTITY_ID: self.vacuum_entity_id},
                blocking=True,
            )

    async def _prepare_service_data(self):
        """Подготовить данные для сервисного вызова."""
        # Собрать все room из всех групп
        all_rooms = []
        cleaning_modes = set()

        for cleaning_mode, zones in self.pending_groups.items():
            cleaning_modes.add(cleaning_mode)
            for zone in zones:
                room = zone.room
                if isinstance(room, list):
                    all_rooms.extend(room)
                else:
                    all_rooms.append(room)

        # Удалить дубликаты и отсортировать
        unique_rooms = sorted(set(all_rooms))

        # Определить сценарий
        if len(cleaning_modes) == 1:
            # Все cleaning_mode одинаковые
            cleaning_mode = next(iter(cleaning_modes))
            use_customized_cleaning = False
        else:
            # Разные cleaning_mode
            use_customized_cleaning = True
            cleaning_mode = None

        return {
            "rooms": unique_rooms,
            "cleaning_mode": cleaning_mode,
            "use_customized_cleaning": use_customized_cleaning,
        }

    async def _call_services(self, service_data):
        """Вызвать необходимые сервисы для запуска уборки."""
        rooms = service_data["rooms"]
        cleaning_mode = service_data["cleaning_mode"]
        use_customized_cleaning = service_data["use_customized_cleaning"]

        if use_customized_cleaning:
            # Активировать customized cleaning switch
            await self._set_customized_cleaning(True)
        else:
            await self._set_customized_cleaning(False)
            # Установить cleaning_mode
            await self._set_cleaning_mode(cleaning_mode)

        # Определить домен и сервис на основе основного пылесоса
        domain = await self._get_vacuum_domain()

        # Вызвать vacuum_clean_segment
        _LOGGER.debug("vacuum_clean_segment for rooms %s", rooms)
        await self.hass.services.async_call(
            domain,
            "vacuum_clean_segment",
            {
                ATTR_ENTITY_ID: self.vacuum_entity_id,
                "segments": rooms,
            },
            blocking=True,
        )

    async def _get_vacuum_domain(self):
        """Получить домен основного пылесоса."""
        entry = entity_registry.async_get(self.hass).async_get(self.vacuum_entity_id)
        return entry.platform if entry else "dreame_vacuum"

    async def _set_customized_cleaning(self, turn_on=True):
        """Активировать или деактивировать переключатель customized cleaning.
        """
        switch_id = "switch.x40_ultra_complete_customized_cleaning"
        if self.hass.states.get(switch_id):
            service = "turn_on" if turn_on else "turn_off"
            await self.hass.services.async_call(
                "switch",
                service,
                {ATTR_ENTITY_ID: switch_id},
                blocking=True,
            )
            _LOGGER.debug("%s customized_cleaning", service)

    async def _set_cleaning_mode(self, cleaning_mode):
        """Установить режим уборки через селекторы."""
        if cleaning_mode in ("routine_cleaning", "deep_cleaning"):
            # Установить режим Clean Genius
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
            _LOGGER.debug("Activated cleangenius %s", cleaning_mode)
        else:
            # Установить обычный режим уборки
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
            _LOGGER.debug("Activated cleaning mode %s", cleaning_mode)

async def async_setup_platform(hass, _, async_add_entities, discovery_info=None):
    entity_id: str = discovery_info["entity_id"]

    # Initialize hass.data structure for our domain
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}

    # Initialize entity_id entry if not exists
    if entity_id not in hass.data[DOMAIN]:
        hass.data[DOMAIN][entity_id] = {}

    entry = hass.data[DOMAIN][entity_id]

    # Create coordinator for grouping (only for vacuum platform)
    coordinator = ZoneCoordinator(hass, entity_id)

    # Store coordinator
    entry["coordinator"] = coordinator

    # Create virtual vacuums
    entities = [
        ZoneVacuum(name, config, coordinator, entity_id, i)
        for i, (name, config) in enumerate(discovery_info["zones"].items())
    ]

    # Create binary sensor for coordinator pending state
    binary_sensor = ZoneCoordinatorIsPending(coordinator, entity_id)
    entities.append(binary_sensor)

    async_add_entities(entities)


class ZoneVacuum(StateVacuumEntity):
    _attr_state = STATE_IDLE
    _attr_supported_features = VacuumEntityFeature.START | VacuumEntityFeature.STOP

    def __init__(
            self,
            name: str,
            config: dict,
            coordinator: ZoneCoordinator,
            parent_id: str,
            number: int,
    ):
        self._attr_name = config.pop("name", name)
        self._attr_unique_id = f"zone_vacuum_{number + 1}"
        self.parent_id = parent_id
        self.coordinator = coordinator

        # Извлечение room и cleaning_mode из конфигурации
        self.room = config.get("room")
        self.cleaning_mode = config.get("cleaning_mode", "sweeping")

        # Проверка обязательного параметра room
        if self.room is None:
            raise ValueError(f"Zone '{name}' must have 'room' parameter")

    @property
    def vacuum_entity_id(self) -> str:
        return self.parent_id

    async def async_added_to_hass(self):
        """Вызывается когда entity добавлен в HA."""
        # Никакой дополнительной инициализации не требуется
        pass

    async def async_start(self):
        """Запустить виртуальный пылесос - добавить в группу координатора."""
        _LOGGER.debug("Vacuum start request: %s", self._attr_name)
        await self.coordinator.schedule_cleaning(self)

    async def async_stop(self, **kwargs):
        """Остановка виртуального пылесоса не поддерживается в новой архитектуре."""
        _LOGGER.debug("Vacuum stop request: %s", self._attr_name)
        await self.coordinator.check_and_stop_vacuum()

class ZoneCoordinatorIsPending(BinarySensorEntity):
    """Binary sensor indicating if coordinator has pending cleaning groups."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_name = "Vacuum zones pending"

    def __init__(self, coordinator, parent_entity_id):
        """Initialize the binary sensor."""
        self.coordinator = coordinator
        self.parent_entity_id = parent_entity_id
        self._attr_unique_id = f"{parent_entity_id}_pending"
        self._attr_device_info = {
            "identifiers": {("vacuum_zones", parent_entity_id)},
            "name": f"Vacuum Zones ({parent_entity_id})",
            "manufacturer": "VacuumZones",
            "model": "Zone Coordinator",
        }

    @property
    def is_on(self) -> bool:
        """Return True if coordinator has pending groups."""
        return len(self.coordinator.pending_groups) > 0

    @property
    def icon(self) -> str:
        """Return the icon to use in the frontend."""
        return "mdi:timer-sand" if self.is_on else "mdi:timer-sand-empty"

    async def async_added_to_hass(self):
        """Register callbacks when entity is added to hass."""
        await super().async_added_to_hass()
        # Register callback with coordinator
        self.coordinator.add_listener(self._handle_coordinator_update)

    async def async_will_remove_from_hass(self):
        """Clean up when entity is removed from hass."""
        await super().async_will_remove_from_hass()
        # Remove callback from coordinator
        self.coordinator.remove_listener(self._handle_coordinator_update)

    def _handle_coordinator_update(self):
        """Handle coordinator state updates."""
        # Schedule state update in Home Assistant
        self.async_write_ha_state()
