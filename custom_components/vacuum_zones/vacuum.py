import asyncio
import logging
from enum import Enum

from homeassistant.components.vacuum import (
    StateVacuumEntity,
    VacuumEntityFeature,
)

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorDeviceClass,
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

class DreameCleaningMode(Enum):
    DRY = "sweeping"
    COMBINED = "sweeping_and_mopping"
    SEQUENTIAL = "mopping_after_sweeping"
    CLEAN_GENIUS = "routine_cleaning"
    CLEAN_GENIUS_DEEP = "deep_cleaning"
    SEQUENTIAL_CLEAN_GENIUS = "mopping_after_sweeping_genius"
    SEQUENTIAL_CLEAN_GENIUS_DEEP = "mopping_after_sweeping_genius_deep"

class ZoneCoordinator:
    """Координатор для группировки запусков виртуальных пылесосов."""

    def __init__(self, hass, vacuum_entity_id, test_mode=False, start_delay=10):
        self.hass = hass
        self.vacuum_entity_id = vacuum_entity_id
        self.prefix = vacuum_entity_id.split('.', 1)[1] # for example x40_ultra_complete
        self.pending_zones_ordered = []  # ordered list of ZoneVacuum
        self.timer_handle = None
        self.start_delay = start_delay
        self.test_mode = test_mode
        self._listeners = []  # Callback functions for state changes

    def add_listener(self, callback):
        """Добавить слушатель для уведомлений об изменении pending zones."""
        self._listeners.append(callback)

    def remove_listener(self, callback):
        """Удалить слушатель."""
        if callback in self._listeners:
            self._listeners.remove(callback)

    def _notify_listeners(self):
        """Уведомить всех слушателей об изменении состояния."""
        for callback in self._listeners:
            callback()

    def _cancel_timer(self):
        """Отменить таймер группировки и очистить handle."""
        if self.timer_handle:
            try:
                self.timer_handle.cancel()
            except Exception:
                pass  # Игнорируем ошибки отмены
            self.timer_handle = None

    async def _restart_timer(self):
        """Перезапустить таймер группировки: отменить старый и создать новую задачу."""
        _LOGGER.debug("Restarting grouping timer for %s seconds", self.start_delay)
        self._cancel_timer()
        self.timer_handle = asyncio.create_task(self._execute_tasks_after_timeout())

    async def schedule_cleaning(self, zone_vacuum):
        """Добавить виртуальный пылесос в очередь и запустить/сбросить таймер."""
        self.pending_zones_ordered.append(zone_vacuum)

        """Пропускаем планирование из запускаем задачу мгновенно если delay равен 0."""
        if self.start_delay == 0:
            await self._execute_tasks()
            return

        self._notify_listeners()

        # Запустить/сбросить таймер группировки
        await self._restart_timer()

    async def remove_zone(self, zone_vacuum, restart_timer=True):
        """Удалить виртуальный пылесос из pending_zones_ordered."""
        if zone_vacuum in self.pending_zones_ordered:
            self.pending_zones_ordered = [z for z in self.pending_zones_ordered if z != zone_vacuum]
            # Если список ожидания стал пустым, отменить таймер
            if not self.pending_zones_ordered and self.timer_handle:
                _LOGGER.debug("Pending zones list empty, cancelling timer")
                self._cancel_timer()
            # Если после удаления зоны список не пуст, restart_timer=True и start_delay != 0 - перезапустить таймер
            elif self.pending_zones_ordered and restart_timer and self.start_delay != 0:
                await self._restart_timer()
            self._notify_listeners()

    async def _execute_tasks_after_timeout(self):
        """Выполнить задачи после таймаута."""
        try:
            await asyncio.sleep(self.start_delay)
            await self._execute_tasks()
        except asyncio.CancelledError:
            # Таймер был отменен - это нормально
            raise
        except Exception as e:
            _LOGGER.error("Error executing group: %s", e)
            # Очищаем pending zones при ошибке
            await self._rollback_to_initial_state()

    async def _execute_tasks(self):
        """Выполнить накопленные команды."""
        if not self.pending_zones_ordered:
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
            # Всегда очищаем pending zones после выполнения или ошибки
            await self._rollback_to_initial_state()

    async def check_and_stop_vacuum(self):
        """Остановить пылесос если он в состоянии cleaning."""
        state = self.hass.states.get(self.vacuum_entity_id)
        if state and state.state == STATE_CLEANING:
            _LOGGER.debug("Vacuum is cleaning, stopping before new command, test_mode=%s", self.test_mode)
            if not self.test_mode:
                await self.hass.services.async_call(
                    "vacuum",
                    "stop",
                    {ATTR_ENTITY_ID: self.vacuum_entity_id},
                    blocking=True,
                )

    async def _rollback_to_initial_state(self):
        # Создаем копию списка, чтобы избежать изменения во время итерации
        zones_to_stop = list(self.pending_zones_ordered)
        for zone in zones_to_stop:
            await zone.async_stop(restart_timer=False)
        self.pending_zones_ordered.clear()
        self._cancel_timer()
        self._notify_listeners()

    async def _prepare_service_data(self):
        """Подготовить данные для сервисного вызова."""
        # Собрать все room из всех групп
        all_rooms = []
        cleaning_modes = set()

        for zone in self.pending_zones_ordered:
            cleaning_modes.add(zone.cleaning_mode)
            room = zone.room
            if isinstance(room, list):
                all_rooms.extend(room)
            else:
                all_rooms.append(room)

        # Удалить дубликаты и отсортировать
        unique_rooms = sorted(set(all_rooms))

        vacuum_state = self.hass.states.get(self.vacuum_entity_id)
        all_rooms_mode = False
        # Получить последовательность комнат из настроек пылесоса
        if vacuum_state and "cleaning_sequence" in vacuum_state.attributes:
            cleaning_sequence = vacuum_state.attributes.get("cleaning_sequence")
            unique_rooms = [
                room_id for room_id in cleaning_sequence
                if room_id in unique_rooms
            ]
            all_rooms_mode = unique_rooms == cleaning_sequence

        use_customized_cleaning = False
        # Определить сценарий
        if len(cleaning_modes) == 1:
            # Все cleaning_mode одинаковые
            cleaning_mode = next(iter(cleaning_modes))
        elif DreameCleaningMode.SEQUENTIAL in cleaning_modes:
            # Поэтапная уборка
            cleaning_mode = DreameCleaningMode.SEQUENTIAL
        elif DreameCleaningMode.SEQUENTIAL_CLEAN_GENIUS in cleaning_modes:
            # Поэтапная уборка в режиме CleanGenius
            if DreameCleaningMode.CLEAN_GENIUS_DEEP in cleaning_modes:
                cleaning_mode = DreameCleaningMode.SEQUENTIAL_CLEAN_GENIUS_DEEP
            else:
                cleaning_mode = DreameCleaningMode.SEQUENTIAL_CLEAN_GENIUS
        else:
            # Разные cleaning_mode
            use_customized_cleaning = True
            cleaning_mode = None

        return {
            "rooms": unique_rooms,
            "cleaning_mode": cleaning_mode,
            "use_customized_cleaning": use_customized_cleaning,
            "all_rooms_mode": all_rooms_mode,
        }

    async def _call_services(self, service_data):
        """Вызвать необходимые сервисы для запуска уборки."""
        rooms = service_data["rooms"]
        cleaning_mode = service_data["cleaning_mode"]
        use_customized_cleaning = service_data["use_customized_cleaning"]
        all_rooms_mode = service_data["all_rooms_mode"]

        if use_customized_cleaning:
            # Выключить Clean Genius перед кастомной уборкой
            await self._turn_off_cleangenius()
            # Активировать customized cleaning switch
            await self._set_customized_cleaning(True)
            # Установить настройки для каждой комнаты
            await self._set_customized_room_settings(rooms)
        else:
            await self._set_customized_cleaning(False)
            # Установить cleaning_mode
            await self._set_cleaning_mode(cleaning_mode)

        # Определить домен и сервис на основе основного пылесоса
        domain = await self._get_vacuum_domain()

        # Вызвать vacuum_clean_segment
        _LOGGER.debug("vacuum_clean_segment for rooms %s, test_mode=%s", rooms, self.test_mode)
        if not self.test_mode:
            if all_rooms_mode:
                # Уборка всех комнат
                await self.hass.services.async_call(
                    "vacuum",
                    "start",
                    {
                        ATTR_ENTITY_ID: self.vacuum_entity_id,
                    },
                    blocking=True,
                )
            else:
                # Уборка конкретных комнат
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
        switch_id = f"switch.{self.prefix}_customized_cleaning"
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
        clean_genius_modes = [
            DreameCleaningMode.CLEAN_GENIUS,
            DreameCleaningMode.CLEAN_GENIUS_DEEP,
            DreameCleaningMode.SEQUENTIAL_CLEAN_GENIUS,
            DreameCleaningMode.SEQUENTIAL_CLEAN_GENIUS_DEEP,
        ]

        if cleaning_mode in clean_genius_modes:
            # Установить режим Clean Genius
            clean_genius = cleaning_mode.value
            clean_genius_mode = "vacuum_and_mop"

            if cleaning_mode == DreameCleaningMode.SEQUENTIAL_CLEAN_GENIUS:
                clean_genius = DreameCleaningMode.CLEAN_GENIUS.value
                clean_genius_mode = "mop_after_vacuum"
            elif cleaning_mode == DreameCleaningMode.SEQUENTIAL_CLEAN_GENIUS_DEEP:
                clean_genius = DreameCleaningMode.CLEAN_GENIUS_DEEP.value
                clean_genius_mode = "mop_after_vacuum"

            await self._set_select_option(f"select.{self.prefix}_cleangenius", clean_genius)
            await self._set_select_option(f"select.{self.prefix}_cleangenius_mode", clean_genius_mode)
            _LOGGER.debug("Activated CleanGenius %s with mod %s", clean_genius, clean_genius_mode)
        else:
            # Установить обычный режим уборки
            await self._turn_off_cleangenius()
            await self._set_select_option(f"select.{self.prefix}_cleaning_mode", cleaning_mode)
            _LOGGER.debug("Activated cleaning mode %s", cleaning_mode)

    async def _turn_off_cleangenius(self):
        """Выключить Clean Genius."""
        await self._set_select_option(f"select.{self.prefix}_cleangenius", "off")
        _LOGGER.debug("Turned off cleangenius")

    def _fill_room_to_mode_mapping(self, room, cleaning_mode, room_to_mode):
        """Добавить комнату (или список комнат) в mapping room->cleaning_mode.
        """
        def add_single_room(r):
            if r in room_to_mode and room_to_mode[r] != cleaning_mode:
                _LOGGER.warning(
                    "Room %s has conflicting cleaning modes: %s vs %s. Using %s",
                    r, room_to_mode[r], cleaning_mode, cleaning_mode
                )
            room_to_mode[r] = cleaning_mode

        if isinstance(room, list):
            for r in room:
                add_single_room(r)
        else:
            add_single_room(room)

    async def _set_select_option(self, entity_id, option):
        """Установить опцию select"""
        # Если передан enum member, берем его значение
        if isinstance(option, Enum):
            option = option.value

        if self.hass.states.get(entity_id):
            await self.hass.services.async_call(
                "select",
                "select_option",
                {
                    ATTR_ENTITY_ID: entity_id,
                    "option": option,
                },
                blocking=True,
            )
            _LOGGER.debug("Set option: %s to %s", entity_id, option)
        else:
            _LOGGER.warning("Entity %s not found", entity_id)

    async def _set_customized_room_settings(self, rooms):
        """Установить настройки для каждой комнаты в режиме кастомной уборки.

        Для каждой комнаты устанавливает:
        1. Режим уборки (sweeping или sweeping_and_mopping)
        2. Количество повторов (1x или 2x)
        """

        # Построить mapping room -> cleaning_mode из pending_zones_ordered (учитывает порядок добавления)
        room_to_mode = {}
        for zone in self.pending_zones_ordered:
            room = zone.room
            cleaning_mode = zone.cleaning_mode
            self._fill_room_to_mode_mapping(room, cleaning_mode, room_to_mode)

        # Установить настройки для каждой комнаты
        for room in rooms:
            cleaning_mode = room_to_mode.get(room)
            if cleaning_mode is None:
                _LOGGER.warning("Room %s not found in pending groups, skipping", room)
                continue

            # Определить режим уборки для комнаты
            if cleaning_mode == DreameCleaningMode.DRY:
                room_cleaning_mode = DreameCleaningMode.DRY
            else:  # sweeping_and_mopping, routine_cleaning, deep_cleaning
                room_cleaning_mode = DreameCleaningMode.COMBINED

            # Определить количество повторов для комнаты
            if cleaning_mode == DreameCleaningMode.CLEAN_GENIUS_DEEP:
                cleaning_times = "2x"
            else:
                cleaning_times = "1x"

            # Установить режим уборки для комнаты
            mode_entity_id = f"select.{self.prefix}_room_{room}_cleaning_mode"
            await self._set_select_option(mode_entity_id, room_cleaning_mode)

            # Установить количество повторов для комнаты
            times_entity_id = f"select.{self.prefix}_room_{room}_cleaning_times"
            await self._set_select_option(times_entity_id, cleaning_times)

async def async_setup_platform(hass, _, async_add_entities, discovery_info=None):
    entity_id: str = discovery_info["entity_id"]
    test_mode = discovery_info.get("test_mode", False)
    start_delay = discovery_info.get("start_delay", 10)

    # Initialize hass.data structure for our domain
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault(entity_id, {})

    entry = hass.data[DOMAIN][entity_id]

    # Create or retrieve coordinator
    if "coordinator" not in entry:
        # First platform to load (usually vacuum) creates the coordinator
        coordinator = ZoneCoordinator(hass, entity_id, test_mode=test_mode, start_delay=start_delay)
        entry["coordinator"] = coordinator
    else:
        coordinator = entry["coordinator"]

    # Create virtual vacuums for vacuum platform
    entities = [
        ZoneVacuum(name, config, coordinator, entity_id)
        for (name, config) in discovery_info["zones"].items()
    ]

    async_add_entities(entities)


class ZoneVacuum(StateVacuumEntity):
    _attr_supported_features = VacuumEntityFeature.START | VacuumEntityFeature.STOP

    def __init__(
            self,
            name: str,
            config: dict,
            coordinator: ZoneCoordinator,
            parent_id: str,
    ):
        self._attr_name = config.pop("name", name)
        self._cleaning = False
        self.parent_id = parent_id
        self.coordinator = coordinator

        # Извлечение room и cleaning_mode из конфигурации
        self.room = config.get("room")

        cleaning_mode_str = config.get("cleaning_mode", DreameCleaningMode.DRY.value)
        try:
            self.cleaning_mode = DreameCleaningMode(cleaning_mode_str)
        except ValueError:
            _LOGGER.warning("Invalid cleaning_mode '%s' for zone '%s'.", cleaning_mode_str, name)
            self.cleaning_mode = DreameCleaningMode.DRY

        # Проверка обязательного параметра room
        if self.room is None:
            raise ValueError(f"Zone '{name}' must have 'room' parameter")

    @property
    def activity(self) -> VacuumActivity:
        if self._cleaning:
            return VacuumActivity.CLEANING
        return VacuumActivity.IDLE

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
        self._cleaning = True
        self.async_write_ha_state()
        await self.coordinator.schedule_cleaning(self)

    async def async_stop(self, **kwargs):
        """Остановка виртуального пылесоса."""
        _LOGGER.debug("Vacuum stop request: %s", self._attr_name)
        self._cleaning = False
        self.async_write_ha_state()
        # Удалить этот виртуальный пылесос из pending_zones_ordered координатора
        restart_timer = kwargs.get('restart_timer', True)
        await self.coordinator.remove_zone(self, restart_timer)

class ZoneCoordinatorIsPending(BinarySensorEntity):
    """Binary sensor indicating if coordinator has pending cleaning groups."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_name = "Vacuum zones pending"

    def __init__(self, coordinator, parent_entity_id):
        """Initialize the binary sensor."""
        self._attr_device_class = BinarySensorDeviceClass.RUNNING
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
        return len(self.coordinator.pending_zones_ordered) > 0

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
