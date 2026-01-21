# VacuumZones

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs)

[Home Assistant](https://www.home-assistant.io/) custom component that helps control room cleaning for Dreame vacuum cleaners with the help of voice assistants - Apple Siri, Google Assistant, Yandex Alice.

Supported vacuums:

- [Dreame Vacuum](https://github.com/Tasshack/dreame-vacuum)

**Note:** Version 2.0.0+ only supports Dreame vacuums. Support for Xiaomi and Roborock vacuums has been removed.

This component creates a virtual vacuum cleaner for each of your rooms.

By adding these vacuums to your voice assistant, you can give voice commands, like "clean bedroom". If your voice assistant supports multiple device commands - you can say "clean up the hall and under the table".

All cleaning commands are **grouped within 2 seconds**. When multiple virtual vacuums are started within this time window, they are grouped by cleaning mode and executed together.

**Grouping logic:**
- **Same cleaning mode**: All rooms are cleaned using the specified cleaning mode
- **Different cleaning modes**: Customized cleaning is activated and rooms are cleaned together

Virtual vacuum entities are always in the `idle` state. The component stops the main vacuum if it's currently cleaning before executing new commands.

## Installation

**Method 1.** [HACS](https://hacs.xyz/) custom repo:

> HACS > Integrations > 3 dots (upper top corner) > Custom repositories > URL: `vadim-miheev/VacuumZones`, Category: Integration > Add > wait > VacuumZones > Install

**Method 2.** Manually copy `vacuum_zones` folder from [latest release](https://github.com/AlexxIT/VacuumZones/releases/latest) to `/config/custom_components` folder.

## Configuration

Configure each virtual vacuum with a room number and optional cleaning mode.

**Available cleaning modes:**
- `sweeping` (default)
- `sweeping_and_mopping`
- `routine_cleaning`
- `deep_cleaning`

`configuration.yaml` example:

```yaml
# available cleaning_modes:
# sweeping sweeping_and_mopping routine_cleaning deep_cleaning
vacuum_zones:
  entity_id: vacuum.x40_ultra_complete    # change to your Dreame vacuum entity
  zones:
    Bedroom Dry:                          # virtual vacuum name
      room: 1                             # single room number
      cleaning_mode: sweeping             # default cleaning mode
    Bedroom Combined:                     # virtual vacuum name
      room: 1                             # same room number
      cleaning_mode: sweeping_and_mopping # different mode
    Kitchen Dry:
      room: 2
    Kitchen Combined:
      room: 2
      cleaning_mode: routine_cleaning     # cleangenius mode
```

**Note:** The `room` parameter accepts either a single integer or a list of integers. All cleaning commands started within 2 seconds are grouped according to the grouping logic described above.

## Useful links

- [Dreame Vacuum Integration](https://github.com/Tasshack/dreame-vacuum) - Home Assistant integration for Dreame vacuums
- [Vacuum Card](https://github.com/denysdovhan/vacuum-card) - universal Lovelace vacuum card
