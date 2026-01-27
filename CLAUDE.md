# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a **Home Assistant custom component** called **VacuumZones**. It enables zone/room cleaning control for Dreame vacuum cleaners with voice assistant support (Apple Siri, Google Assistant, Yandex Alice). The component creates virtual vacuum entities for each zone/room, allowing voice commands like "clean bedroom" or "clean hall and kitchen".

**Key Features:**
- Group-based cleaning (commands within 10-second window are grouped and executed together)
- Dreame vacuum platform support only (version 2.0.0+)
- Multiple cleaning modes: `sweeping`, `sweeping_and_mopping`, `routine_cleaning`, `deep_cleaning`
- Voice assistant integration through individual virtual vacuum entities

**Note:** Version 2.0.0+ only supports Dreame vacuums. Support for Xiaomi and Roborock vacuums has been removed.

## Architecture

### Core Components

1. **`custom_components/vacuum_zones/__init__.py`**
   - Component initialization and configuration schema
   - Uses `voluptuous` for configuration validation
   - Defines `DOMAIN = "vacuum_zones"`
   - Configuration schema requires:
     - `entity_id`: The physical Dreame vacuum entity ID
     - `zones`: Dictionary of zone configurations with `room` (int or list) and optional `cleaning_mode`

2. **`custom_components/vacuum_zones/vacuum.py`**
   - **`ZoneCoordinator` class**: Manages grouping of cleaning commands
     - Groups virtual vacuum start commands within 10-second window
     - Handles different cleaning modes (same vs. different modes)
     - Stops vacuum if currently cleaning before new commands
     - Activates customized cleaning when different modes are grouped

   - **`ZoneVacuum` class**: Extends `StateVacuumEntity`
     - Creates virtual vacuum entities for each zone
     - Delegates cleaning to `ZoneCoordinator` via `schedule_cleaning()`

3. **`custom_components/vacuum_zones/manifest.json`**
   - Component metadata and compatibility information
   - Version: 2.0.0
   - Home Assistant compatibility: 2023.2.0+

### Grouping Logic

The component uses a 10-second grouping window for cleaning commands:

1. **Same cleaning mode**: All rooms cleaned with specified mode
   - Example: "clean bedroom" and "clean kitchen" (both `sweeping`) → rooms 1 and 2 cleaned together in `sweeping` mode

2. **Different cleaning modes**: Customized cleaning activated, rooms cleaned together
   - Example: "clean bedroom" (`sweeping`) and "clean kitchen" (`sweeping_and_mopping`) → customized cleaning switch activated, rooms 1 and 2 cleaned together

### Configuration Schema

```yaml
vacuum_zones:
  entity_id: vacuum.x40_ultra_complete    # Your Dreame vacuum entity
  test_mode: true
  start_delay: 10
  zones:
    Bedroom Dry:                          # Virtual vacuum name
      room: 1                             # Room number(s) - int or list
      cleaning_mode: sweeping             # Optional, default: "sweeping"
    Kitchen Combined:
      room: [2, 3]                        # List of room numbers
      cleaning_mode: routine_cleaning     # Clean Genius mode
```

**Available cleaning modes:**
- `sweeping` (default)
- `sweeping_and_mopping`
- `routine_cleaning` (Clean Genius mode)
- `deep_cleaning` (Clean Genius mode)

### State Management (v2.0.0+)

- **Physical vacuum control**: Coordinator stops vacuum if cleaning before new commands
- **Service calls**: Uses `dreame_vacuum.vacuum_clean_segment` service

## Development Commands

### Validation and Testing

1. **HACS Validation** (runs on push/pull request via CI/CD):
   ```bash
   # Automated validation via .github/workflows/hacs.yml
   # Validates component for HACS repository and hassfest validation
   ```

2. **Manual Validation**:
   - Ensure `manifest.json` follows Home Assistant requirements
   - Validate configuration schema in `__init__.py`
   - Check for deprecated Home Assistant constants (recent fix for HA Core 2026.1)
   - Test with Dreame vacuum integration

### Installation Methods

1. **HACS Installation**:
   - Add custom repository: `vadim-miheev/VacuumZones`
   - Category: Integration

2. **Manual Installation**:
   - Copy `vacuum_zones` folder to `/config/custom_components`

## Code Patterns and Conventions

### Async/Await Pattern
All functions use Home Assistant's async/await patterns. The component is event-driven and integrates with Home Assistant's event bus.

### Deprecation Handling
The code includes compatibility for HA Core 2026.1 deprecations:
```python
try:
    # New constants from VacuumActivity HA Core 2026.1
    from homeassistant.components.vacuum import VacuumActivity
    STATE_CLEANING = VacuumActivity.CLEANING
    STATE_IDLE = VacuumActivity.IDLE
except ImportError:
    # Fallback to old constants
    from homeassistant.components.vacuum import STATE_CLEANING
    from homeassistant.const import STATE_IDLE
```

### Russian Comments
The codebase contains Russian comments (maintainer's language). Key terms:
- "Координатор" = Coordinator
- "виртуальный пылесос" = virtual vacuum
- "группировка" = grouping

### Service Integration
- Detects vacuum platform via entity registry (`dreame_vacuum` default)
- Calls `vacuum_clean_segment` service with room segments
- Manages cleaning mode selection via select entities
- Activates customized cleaning via switch entity when needed

## Important Files

- **`README.md`**: Comprehensive documentation with installation, configuration, and usage examples
- **`hacs.json`**: HACS integration metadata with `render_readme: true`
- **`.github/workflows/hacs.yml`**: CI/CD pipeline for HACS and hassfest validation
- **`configuration.yaml`**: Example configuration with Yandex Smart Home integration
- **`.gitignore`**: Standard Python/Home Assistant ignore patterns

## Development Notes

1. **Architecture Evolution**: Version 2.0.0 changed from queue-based to group-based architecture:

2. **Platform Specialization**: Version 2.0.0 dropped Xiaomi/Roborock support, focusing only on Dreame vacuums.

3. **Error Handling**: The component gracefully handles platform detection and service mapping failures.

4. **Extensibility**: The `extra=vol.ALLOW_EXTRA` in the config schema allows for custom service calls.

## Related Integrations

- **Dreame Vacuum Integration**: Required for vacuum control
- **Yandex Smart Home**: For voice assistant integration (example in `configuration.yaml`)
- **Vacuum Card**: Universal Lovelace vacuum card

## Version Compatibility

- Current version: 2.0.0
- Minimum Home Assistant version: 2023.2.0
- Tested up to: HA Core 2026.1 (with deprecation fixes)
- Supported vacuums: Dreame only (via `dreame_vacuum` integration)

When making changes, ensure compatibility with Dreame vacuum integration and check for deprecated Home Assistant APIs.