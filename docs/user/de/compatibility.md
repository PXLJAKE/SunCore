<!-- Generiert von scripts/gen_compat.py aus templates/. Nicht von Hand ändern. -->

# Kompatibilitätsliste

Diese Liste wird aus den eingebauten Vorlagen in `templates/` erzeugt. Eine Vorlage beschreibt,
wie SunCore die Entitäten eines Geräts, Tarifs oder einer Prognosequelle findet und welche
Standardwerte es nutzt. Sie heißt **nicht**, dass das Gerät gesteuert wird: Jedes Gerät startet im
Schattenmodus, und eine Vorlage, die nur im Simulator getestet wurde, lief nie an echter Hardware.

- **Getestet mit** nennt die Integrationsversionen, gegen die jemand die Vorlage auf einem
  laufenden Home Assistant geprüft hat. *nicht auf Hardware getestet* heißt: Die Vorlage wurde aus
  dem Quelltext der Integration geschrieben und lief nur im Simulator.
- **Ungeprüft** ist die Zahl der Tatsachen, die die Vorlage als ungeprüft aufführt. Sie stehen in
  der Vorlagendatei.
- **Verschiebbare Programme hinter einer Steckdose:** Lege eine Home-Assistant-Automatisierung an,
  die die Steckdose einschaltet, wenn sie viele Stunden aus war, falls SunCore nicht läuft, während
  das Programm wartet (siehe [Überschussregelung](surplus-control.md), "Verschiebbare Programme").
- Eigene Vorlagen (in `/data/templates`) stehen nicht in dieser Liste. Wie du ein Gerät beiträgst,
  steht unter [Gerätevorlage beitragen](device-templates.md).

## Geräte

| Id | Art | Name | Integration / Plattformen | Fähigkeiten | Getestet mit | Ungeprüft |
|---|---|---|---|---|---|---|
| `evcc.loadpoint` | device | evcc-Ladepunkt (MQTT) | beliebig | `ev_charging` | nicht auf Hardware getestet | 9 |
| `generic.climate` | device | Allgemeines Klimagerät | beliebig | `energy_counter`, `hvac_modes`, `power_measured`, `temperature_setpoint` | nicht auf Hardware getestet | 3 |
| `generic.heat_pump_dhw_setpoint` | device | Warmwasser-Sollwert der Wärmepumpe (Water-Heater-Entität) | beliebig | `power_measured`, `temperature_setpoint` | nicht auf Hardware getestet | 4 |
| `generic.heat_pump_sg_ready` | device | Wärmepumpe mit SG-Ready-Kontakten (zwei Schalter) | beliebig | `power_measured`, `sg_ready` | nicht auf Hardware getestet | 6 |
| `generic.number_load` | device | Verbraucher mit Number-Entität | beliebig | `power_measured`, `power_setpoint` | nicht auf Hardware getestet | 3 |
| `generic.program_notify` | device | Verschiebbares Programm (Benachrichtigung) | beliebig | `power_measured` | nicht auf Hardware getestet | 2 |
| `generic.program_plug` | device | Verschiebbares Programm (Steckdose) | beliebig | `on_off`, `power_measured` | nicht auf Hardware getestet | 4 |
| `generic.program_remote` | device | Verschiebbares Programm (Fernstart-Schalter) | beliebig | `power_measured`, `remote_start` | nicht auf Hardware getestet | 3 |
| `generic.switch` | device | Schalter oder Zwischenstecker | beliebig | `on_off`, `power_measured` | nicht auf Hardware getestet | 2 |
| `generic.wallbox` | device | Wallbox (allgemeine Entitäten) | beliebig | `ev_charging`, `power_measured` | nicht auf Hardware getestet | 5 |
| `generic.water_heater_relays` | device | Warmwasserspeicher mit 1-3 Relais | beliebig | `power_measured`, `power_setpoint` | nicht auf Hardware getestet | 7 |
| `generic.water_heater_steps` | device | Warmwasserspeicher mit Leistungsstufen (Select-Entität) | beliebig | `power_measured`, `power_setpoint` | nicht auf Hardware getestet | 7 |
| `lg.thinq_climate` | device | LG-ThinQ-Klimagerät | `lg_thinq` | `energy_counter`, `fan_modes`, `hvac_modes`, `power_measured`, `preset_modes`, `temperature_setpoint` | nicht auf Hardware getestet | 7 |

## Batterien

| Id | Art | Name | Integration / Plattformen | Fähigkeiten | Getestet mit | Ungeprüft |
|---|---|---|---|---|---|---|
| `generic.battery` | battery | Akku mit Zahlen-Entitäten (generisch) | beliebig | `charge_limit`, `discharge_limit`, `grid_charge`, `min_soc_limit` | nicht auf Hardware getestet | 3 |
| `kostal.plenticore` | battery | KOSTAL PLENTICORE (Home-Assistant-Integration, Mindest-Ladestand) | `kostal_plenticore` | `min_soc_limit` | nicht auf Hardware getestet | 4 |
| `kostal.plenticore_g3_modbus` | battery | KOSTAL PLENTICORE G3 Akku-Grenzen über Modbus (§3.5) | `modbus` | `charge_limit`, `discharge_limit`, `hardware_fallback` | nicht auf Hardware getestet | 5 |

## Wechselrichter-Grenzen

| Id | Art | Name | Integration / Plattformen | Fähigkeiten | Getestet mit | Ungeprüft |
|---|---|---|---|---|---|---|
| `generic.inverter_limit` | inverter | Wechselrichter-Limit über eine Number-Entität (generisch) | beliebig | `ac_limit`, `export_limit` | nicht auf Hardware getestet | 3 |
| `kostal.plenticore_g3_modbus_limit` | inverter | KOSTAL PLENTICORE G3 Wirkleistungsvorgabe über Modbus (Register 533) | `modbus` | `ac_limit` | nicht auf Hardware getestet | 4 |
| `kostal.plenticore_power_limit` | inverter | KOSTAL PLENTICORE Wirkleistungsbegrenzung (Home-Assistant-Integration) | `kostal_plenticore` | `export_limit` | nicht auf Hardware getestet | 4 |

## Tarife

| Id | Art | Name | Integration / Plattformen | Fähigkeiten | Getestet mit | Ungeprüft |
|---|---|---|---|---|---|---|
| `entsoe.price` | tariff | ENTSO-E Day-Ahead-Preis (kWh) | `entsoe` | `price_curve`, `price_now` | nicht auf Hardware getestet | 4 |
| `entsoe.price_mwh` | tariff | ENTSO-E Day-Ahead-Preis (MWh-Skala) | `entsoe` | `price_curve`, `price_now` | nicht auf Hardware getestet | 4 |
| `epex_spot.market_price` | tariff | EPEX-Spot-Marktpreis | `epex_spot` | `price_curve`, `price_now` | nicht auf Hardware getestet | 4 |
| `epex_spot.total_price` | tariff | EPEX-Spot-Gesamtpreis | `epex_spot` | `price_curve`, `price_now` | nicht auf Hardware getestet | 4 |
| `generic.price_list` | tariff | Allgemeine Preisliste (Sensor-Attribut) | beliebig | `price_curve`, `price_now` | nicht auf Hardware getestet | 3 |
| `nordpool.price` | tariff | Nord-Pool-Day-Ahead-Preis | `nordpool` | `price_curve`, `price_now` | nicht auf Hardware getestet | 6 |
| `octopus_energy.rates` | tariff | Octopus-Energy-Tarife | `octopus_energy` | `price_curve`, `price_now` | nicht auf Hardware getestet | 4 |
| `tibber.price` | tariff | Tibber dynamischer Preis | `tibber` | `price_curve`, `price_now` | nicht auf Hardware getestet | 6 |

## PV-Prognosen

| Id | Art | Name | Integration / Plattformen | Fähigkeiten | Getestet mit | Ungeprüft |
|---|---|---|---|---|---|---|
| `forecast_solar.pv` | forecast | Forecast.Solar-PV-Prognose (Energie-Dashboard) | beliebig | `pv_curve` | nicht auf Hardware getestet | 8 |
| `generic.pv_forecast` | forecast | Allgemeine PV-Prognose (Attribut eines Template-Sensors) | `template` | `pv_curve` | nicht auf Hardware getestet | 1 |
| `open_meteo_solar_forecast.pv` | forecast | Open-Meteo Solar Forecast | `open_meteo_solar_forecast` | `pv_curve` | nicht auf Hardware getestet | 5 |
| `solcast.pv` | forecast | Solcast-PV-Prognose | `solcast_solar` | `pv_curve`, `pv_quantiles` | nicht auf Hardware getestet | 4 |

## Regionen

| Id | Art | Name | Integration / Plattformen | Fähigkeiten | Getestet mit | Ungeprüft |
|---|---|---|---|---|---|---|
| `de` | region | Deutschland | – | `negative_price_rule` | – | 2 |
| `generic` | region | Anderes Land (neutrales Profil) | – | – | – | 1 |
