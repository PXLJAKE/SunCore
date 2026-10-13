# Changelog

## [0.2.0](https://github.com/PXLJAKE/SunCore/compare/v0.1.0...v0.2.0) (2026-10-13)


### Features

* **api,app:** add FastAPI service, live WebSocket and composition root ([4607949](https://github.com/PXLJAKE/SunCore/commit/460794960715367fe4c7afcd1d893dfdadc21afe))
* **core,safety,storage:** Phase 1-A – Domänentypen, Schattenmodus-Gate, Konfigurationsspeicher ([5c35405](https://github.com/PXLJAKE/SunCore/commit/5c35405dd0c639c01f7d3f09012ff5394f2a8058))
* **core:** add units, signals, site config and energy balance ([f0c12e6](https://github.com/PXLJAKE/SunCore/commit/f0c12e6c10980fba4092d9aad2d24e99d62b8521))
* **frontend:** add Svelte app with Now view, settings page and i18n ([cca366e](https://github.com/PXLJAKE/SunCore/commit/cca366ec7de03bfe57ff8d05a34d04f814dc9c73))
* **frontend:** Phase 1-C – Svelte-App mit „Jetzt“-Ansicht, Einstellungen und i18n ([607ed9f](https://github.com/PXLJAKE/SunCore/commit/607ed9fbf86f21137f58e6a5dea2c4c71abca6a4))
* **ha,api,sim:** Phase 1-B – HA-Connector, API mit Live-Feed, Simulator und Demo-Modus ([16e1781](https://github.com/PXLJAKE/SunCore/commit/16e178135c27ba55fb630a564f93a805228f056a))
* **ha:** add WebSocket connector with Supervisor and direct transports ([9dcb929](https://github.com/PXLJAKE/SunCore/commit/9dcb9299bc15116ea96c4e426c97b3fe3afd2ea3))
* **safety:** add decision contracts and shadow-only safety gate ([d2dcef5](https://github.com/PXLJAKE/SunCore/commit/d2dcef5d1602987b70cc491b6caa904bf5880ba5))
* **sim:** add fake HA connection with fault injection for demo mode ([0dcef60](https://github.com/PXLJAKE/SunCore/commit/0dcef60460e03b9d5862dac7e4d69b042700beff))
* **sim:** add virtual house and scenario files ([04f2b3b](https://github.com/PXLJAKE/SunCore/commit/04f2b3bc35c66d4c503588114b38b500c694d508))
* **sim:** start the demo at a configurable time of day ([8366337](https://github.com/PXLJAKE/SunCore/commit/83663376f62f9dfea4ca0824242309d82646a09f))
* **storage:** add SQLite config store with migrations, export/import and decision log ([c196805](https://github.com/PXLJAKE/SunCore/commit/c196805327991a92da7cfaa533ea3504510bebf0))


### Bug fixes

* address review findings for PR 1-A ([6c37c29](https://github.com/PXLJAKE/SunCore/commit/6c37c2919baf8fce692b53f2f740b0c3feda8807))
* **api:** pin import error descriptions so OpenAPI is identical on 3.12 and 3.13 ([bbc0438](https://github.com/PXLJAKE/SunCore/commit/bbc04385915e40aac35f13ab3c9b37aaff903230))
* **app:** address review findings for PR 1-D ([de6d4ea](https://github.com/PXLJAKE/SunCore/commit/de6d4eaaa22ec9f0d3569bf89a4d2d317a784f61))
* **app:** declare BUILD_FROM before the first FROM ([c66f6fa](https://github.com/PXLJAKE/SunCore/commit/c66f6fa71d695ab62e5e931f4755c8f9d0f8d9d9))
* **app:** install the locked dependency set from PyPI in the image ([5c1cdbc](https://github.com/PXLJAKE/SunCore/commit/5c1cdbcd7e9b6292bdda9561cc4595a8213b4dfd))
* **frontend:** address review findings for PR 1-C ([a5e9310](https://github.com/PXLJAKE/SunCore/commit/a5e93109350bf5f783b205b3a7f4f373d8de172c))
* **frontend:** searchable entity picker filtered by unit ([8416197](https://github.com/PXLJAKE/SunCore/commit/8416197d4f0a83d21483d27e1c2b4c6091547132))
* **ha,api:** address review findings for PR 1-B ([d5ac799](https://github.com/PXLJAKE/SunCore/commit/d5ac799b5fd6d805295d1725d9db443850a8f5fd))
* **ha:** offer sensors without device_class by unit in the entity picker ([f5c71b3](https://github.com/PXLJAKE/SunCore/commit/f5c71b35f75cf656dbe02438817088103078d83e))
* **i18n:** use descriptive German connection status texts ([1ec3b9b](https://github.com/PXLJAKE/SunCore/commit/1ec3b9b6fa5f5489f3b0a491fd2c356c163b9e09))
* SoC-Sensor ohne device_class in der Sensorauswahl, durchsuchbare Auswahlliste ([4071930](https://github.com/PXLJAKE/SunCore/commit/4071930dbf0fe6b19e69116f0c14a1ec4864ecf3))


### Documentation

* add architecture with Mermaid diagrams, roadmap and status ([d58aa6b](https://github.com/PXLJAKE/SunCore/commit/d58aa6b74a258fdfac5c059a84e1cb0dacda9457))
* add demo-mode screenshots of the Now and settings views ([c6585ec](https://github.com/PXLJAKE/SunCore/commit/c6585eca3e5ab4774f95a5249789142d7314c202))
* add project brief ([a52f56f](https://github.com/PXLJAKE/SunCore/commit/a52f56f8ca5a6ec7b63494337db312dd4d5e7bc6))
* **adr:** add template and ADRs 0001-0011 with recommendations ([fc68787](https://github.com/PXLJAKE/SunCore/commit/fc687877a8e10ed013a96a624f82e4990e379cea))
* app docs, quick start and contributor guide for Phase 1 ([7758a46](https://github.com/PXLJAKE/SunCore/commit/7758a469b53ebd57506f687536f0878e5e8c2f7c))
* **plans:** add phase 0 plan ([449a7b3](https://github.com/PXLJAKE/SunCore/commit/449a7b3c5db789eb15473f057d6cf53adc68cf75))
* **plans:** add phase 1 plan ([0ab7018](https://github.com/PXLJAKE/SunCore/commit/0ab7018a0b6810c2d11f2914cce6789c40614630))
* **research:** add sourced notes on HA apps, WebSocket API, MQTT discovery, competitors, integrations, Kostal Modbus and solver availability ([cc36b7b](https://github.com/PXLJAKE/SunCore/commit/cc36b7b34506f8e500e6ef21fe822b4b82af31f7))
* STATUS nach dem Merge von Phase 1 ([3154cc4](https://github.com/PXLJAKE/SunCore/commit/3154cc4f445080b52e1b9b4b1873cb9ea43fe0c9))
* update STATUS after the Phase 1 merge ([a1e3ead](https://github.com/PXLJAKE/SunCore/commit/a1e3eadcaa3a7c42e57bb9740c1898e36ca9dbf9))
* update STATUS for PR 1-B and wrap a long research note line ([171bc2f](https://github.com/PXLJAKE/SunCore/commit/171bc2f9c5347c4f00627f6da97ee44875b6292c))
* update STATUS for PR 1-D ([632e5d6](https://github.com/PXLJAKE/SunCore/commit/632e5d69d73c9f60c524cf2f7a4fc2aed3b9effd))
* verify Kostal Modbus and KSEM register maps from official documents, accept all ADRs ([9d71968](https://github.com/PXLJAKE/SunCore/commit/9d719681ffbf9cab3afddd59087718b9822198d2))

## Changelog

All notable changes to this project are documented in this file. It is
generated by release-please from Conventional Commits; do not edit by hand.
