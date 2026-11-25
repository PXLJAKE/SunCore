# Dashboard recipe

!!! note "This page is a placeholder"

    The ready-to-paste dashboard is not written yet. It is work package 11 of Phase 7 and needs a
    test on a real Home Assistant. Nothing below is a finished recipe.

## What is planned

SunCore does not ship a custom Lovelace card for 1.0. Instead this page will show a Home
Assistant dashboard (sections view) built only from cards that ship with Home Assistant, on top
of the entities SunCore publishes through MQTT (see
[Home Assistant entities](home-assistant-entities.md)):

- status and mode,
- the energy numbers (savings, autarky, planned cost, forecast error, next action),
- emergency stop and "back to normal",
- the mode of each device,
- the grid view,
- a compact variant for a small wall tablet.

The page will also show how to find your own entity ids instead of guessing them.

The decision and its reasons are in
[ADR-0021](https://github.com/pxljake/suncore/blob/main/docs/adr/0021-ha-dashboard-surface-for-1-0.md).
The plan for the work is in
[the Phase 7 plan](https://github.com/pxljake/suncore/blob/main/docs/plans/phase-7.md).

## Until then

- The SunCore panel in the Home Assistant sidebar is the full interface.
- The entities of [Home Assistant entities](home-assistant-entities.md) can be added to any
  dashboard with the cards you already know. Find the entity ids under **Settings, Devices and
  services, Entities** by searching for `suncore`. UNVERIFIED: the exact prefix of the entity
  ids depends on your setup; look them up there.
