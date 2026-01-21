import asyncio
import logging

from homeassistant.components.vacuum import (
    StateVacuumEntity,
    VacuumEntityFeature,
)
from homeassistant.const import (
    STATE_IDLE,
    ATTR_ENTITY_ID,
)
from homeassistant.helpers import entity_registry

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

    async def schedule_cleaning(self, zone_vacuum):
        """Добавить виртуальный пылесос в группу и запустить/сбросить таймер."""
        cleaning_mode = zone_vacuum.cleaning_mode

        if cleaning_mode not in self.pending_groups:
            self.pending_groups[cleaning_mode] = []
        self.pending_groups[cleaning_mode].append(zone_vacuum)

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

    async def _execute_group(self):
        """Выполнить накопленные группы."""
        if not self.pending_groups:
            return

        try:
            # Проверить состояние пылесоса и остановить если cleaning
            await self._check_and_stop_vacuum()

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

    async def _check_and_stop_vacuum(self):
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

        # Определить домен и сервис на основе основного пылесоса
        domain = await self._get_vacuum_domain()

        if use_customized_cleaning:
            # Активировать customized cleaning switch
            await self._activate_customized_cleaning()
            # Использовать стандартный cleaning_mode для вызова сервиса
            cleaning_mode_for_service = "sweeping"
        else:
            cleaning_mode_for_service = cleaning_mode

        # Установить cleaning_mode
        await self._set_cleaning_mode(cleaning_mode_for_service)

        # Вызвать vacuum_clean_segment
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

    async def _activate_customized_cleaning(self):
        """Активировать переключатель customized cleaning."""
        switch_id = "switch.x40_ultra_complete_customized_cleaning"
        if self.hass.states.get(switch_id):
            await self.hass.services.async_call(
                "switch",
                "turn_on",
                {ATTR_ENTITY_ID: switch_id},
                blocking=True,
            )

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


async def async_setup_platform(hass, _, async_add_entities, discovery_info=None):
    entity_id: str = discovery_info["entity_id"]

    # Создаем координатор для группировки запусков
    coordinator = ZoneCoordinator(hass, entity_id)

    # Создаем виртуальные пылесосы
    entities = [
        ZoneVacuum(name, config, entity_id, coordinator)
        for name, config in discovery_info["zones"].items()
    ]
    async_add_entities(entities)


class ZoneVacuum(StateVacuumEntity):
    _attr_state = STATE_IDLE
    _attr_supported_features = VacuumEntityFeature.START

    def __init__(
            self,
            name: str,
            config: dict,
            entity_id: str,
            coordinator: ZoneCoordinator,
    ):
        self._attr_name = config.pop("name", name)
        self.entity_id = entity_id
        self.coordinator = coordinator

        # Извлечение room и cleaning_mode из конфигурации
        self.room = config.get("room")
        self.cleaning_mode = config.get("cleaning_mode", "sweeping")

        # Проверка обязательного параметра room
        if self.room is None:
            raise ValueError(f"Zone '{name}' must have 'room' parameter")

    @property
    def vacuum_entity_id(self) -> str:
        return self.entity_id

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
        _LOGGER.debug("Vacuum stop request ignored (not supported): %s", self._attr_name)
        # В новой архитектуре остановка отдельных виртуальных пылесосов не поддерживается
        # Остановка основного пылесоса выполняется через координатор при необходимости
