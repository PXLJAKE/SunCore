# Gerätevorlage beitragen

Eine Vorlage bringt SunCore ein neues Gerät, einen Tarif, eine
Prognosequelle oder eine Region bei, ohne Code zu ändern. Sie ist eine
einfache YAML-Datei, die **Rollen** beschreibt (was das Gerät bietet, etwa
„der Schalter“ oder „der Energiezähler“), woran man die passenden Entitäten
erkennt, und sinnvolle Standardwerte. Der Einrichtungs-Assistent sortiert
dann deine Entitäten nach den Rollen und füllt das Formular aus.

Dieser Leitfaden behandelt Gerätevorlagen im Detail; Tarif-, Prognose- und
Regionsvorlagen haben denselben Kopf. Maßgeblich ist
[`templates/README.md`](../../../templates/README.md) (englisch) mit der
Kompatibilitätsregel für das Schema; der Entwurf steht in
[ADR-0006](../../adr/0006-template-and-plugin-system.md) und
[ADR-0012](../../adr/0012-template-schema-v1.md). Vorhandene Vorlagen findest
du in der generierten [Kompatibilitätsliste](../compatibility.md)
(englisch).

Sei ehrlich dazu, was du geprüft hast. Eine Vorlage mit dem Vermerk „nicht
getestet“ ist willkommen; eine, die so tut, nicht.

## Was du brauchst

- Ein laufendes Home Assistant mit dem Gerät, oder den Quellcode der
  Integration, oder einen Auszug aus Entwicklerwerkzeuge → Zustände mit den
  Entitäten (Attribute eingeschlossen).
- Ein Checkout des Repositorys und `uv sync --all-groups` (siehe
  [CONTRIBUTING.md](../../../CONTRIBUTING.md)).
- Erfinde nie Entitäts-IDs, Attribute, Dienste oder Register. Alles in der
  Vorlage muss aus dem Quellcode der Integration, einem Herstellerdokument
  oder deinem eigenen Gerät stammen.

## Datei und Ort

`templates/<kind>/<vendor>.<model>.yaml`, mit `<kind>` aus `device`,
`tariff`, `forecast`, `region`. Der Dateiname ist die `id` plus `.yaml`; die
ID ist kleingeschrieben `<vendor>.<model>` (Regionen nutzen den Ländercode,
zum Beispiel `de`). Beginne die Datei mit dem Schema-Kopf, damit dein Editor
beim Tippen prüft:

```yaml
# yaml-language-server: $schema=../schema/device.schema.json
```

Die JSON-Schemas in `templates/schema/` werden generiert; ändere sie nie
von Hand.

## Aufbau einer Gerätevorlage

```yaml
id: vendor.model
kind: device
schema_version: 1
name: template.vendor.model.name        # i18n-Schlüssel, immer template.<id>.name
source:                                  # Pflicht, mindestens ein Eintrag
  - title: "home-assistant/core vendor climate.py (was du gelesen hast)"
    url: "https://github.com/home-assistant/core/blob/<commit>/..."
    date: 2026-10-13
tested_with: []                          # oder [{ integration: vendor, version: "2026.10" }]
capabilities: [on_off, power_measured]
unverified:
  - "Was du nicht prüfen konntest."
device_kind: switch                      # climate, switch, number_load, ...
roles: { ... }
defaults: { ... }
```

Nur einfaches YAML: **keine Anker oder Aliase** (`&a`, `*a`, `<<`). Der
Lader lehnt sie ab, weil wenige Byte Aliase zu Millionen Knoten anwachsen
können. Wiederhole stattdessen den Text.

### Rollen und Matcher

Eine Rolle enthält nie eine Entitäts-ID, sondern einen Matcher; **alle**
angegebenen Kriterien müssen zutreffen, und `domain` ist Pflicht:

| Kriterium | Bedeutung |
|---|---|
| `domain` | Domäne der Entität, zum Beispiel `switch`, `sensor`, `climate` |
| `platforms` | Integrationen (Liste); die Plattform der Entität laut Registry |
| `device_class` | Geräteklasse der Entität, zum Beispiel `power`, `energy` |
| `units` oder `unit_pattern` | Genaue Einheiten (Liste) oder ein regulärer Ausdruck, nicht beides |
| `translation_key` oder `translation_key_pattern` | Von der Integration vergebene Entitätsart, exakt oder als regulärer Ausdruck, nicht beides |
| `state_class` | Zum Beispiel `measurement` oder `total_increasing` |

Reguläre Ausdrücke sind verankert (`re.fullmatch`). Bevorzuge
`translation_key` und `translation_key_pattern` gegenüber Namen: Entitäts-IDs
und Namen ändern sich mit Firmware, Sprache und Spitzname, Übersetzungs-
schlüssel nicht. Nimm `translation_key_pattern`, wenn eine Integration
Varianten hat, zum Beispiel `'^energy_usage_today(_for_location)?$'`.

Eine Rolle kann `required: false`, eine `description` (ein i18n-Schlüssel,
siehe unten) und `same_device_as: <andere Rolle>` setzen. Damit passt ein
Kandidat nur, wenn er zum selben Registry-Gerät gehört wie die Entität, die
für die andere Rolle gewählt wurde (nur eine Ebene; die andere Rolle darf
selbst kein `same_device_as` nutzen). So findet man „den Leistungssensor
**dieses** Klimageräts“.

Lass `same_device_as` weg, wenn der Sensor auf einem **anderen** Gerät liegen
darf: Die Klimagerät-Vorlagen tun das bei ihrer optionalen Rolle `power`,
weil eine zweite Integration (zum Beispiel `smartthinq_sensors`) oder ein
Zwischenstecker ein eigenes Registry-Gerät anlegt. Eine solche Rolle hat
keinen Gerätefilter; der Assistent sortiert nur Kandidaten höher, deren
Geräte- oder Entitätsname den Namen des Geräts der Hauptrolle enthält. Dafür
braucht die Vorlage keine eigene Syntax. Vermerke in einem Kommentar, wenn
ein solcher Sensor nur langsam aktualisiert, denn ein darauf aufgebautes
Leistungsmodell ist ebenfalls langsam.

```yaml
roles:
  switch:
    matcher:
      domain: switch
    description: template.vendor.model.role.switch
  power:
    required: false
    same_device_as: switch
    matcher:
      domain: sensor
      device_class: power
      units: [W, kW]
    description: template.vendor.model.role.power
```

### Fähigkeiten (capabilities)

`capabilities` ist eine geschlossene Liste: `power_measured`,
`energy_counter`, `on_off`, `power_setpoint`, `temperature_setpoint`,
`hvac_modes`, `preset_modes`, `fan_modes`, `price_now`, `price_curve`,
`pv_curve`, `pv_quantiles`, `feed_in_tiers`, `negative_price_rule`. Wo ein
Validator die Angabe prüfen kann (etwa braucht `on_off` eine
Ein/Aus-Steuerung), ist eine unbegründete ein Fehler. Neue Einträge
erfordern eine Schema-Änderung; eröffne ein Issue.

### Standardwerte und Steuerung

`defaults` wird in jedes neue Gerät aus der Vorlage kopiert:

- `power_model`: Wie SunCore die Leistung ermittelt: `measured` (braucht
  eine Leistungsrolle), `counter_derived` (braucht eine Energierolle),
  `learned` oder `nominal` (der Assistent fragt die Nennleistung in W ab;
  rate keine).
- `control`: Wie das Gerät angesteuert wird, über eine Rolle: `on_off`,
  `steps` (Stufen), `continuous` (eine Zahl in `W`, `A` oder `%` zwischen
  `min` und `max`), `setpoint` (Temperatur um höchstens `shift_max_c`
  verschieben; braucht `comfort`) oder `mode`.
- Schutz: `min_run_s`, `min_pause_s`, `max_switches_per_hour`,
  `hysteresis_w`, `hysteresis_delay_s`, `settle_s` (Wartezeit, bevor ein
  geschriebener Wert zurückgelesen wird). Kompressoren und Motoren brauchen
  Mindestlauf- und Pausenzeiten; stammen die Zahlen aus deiner eigenen
  Überlegung und nicht vom Hersteller, schreibe das unter `unverified`.
- `comfort`: nur für thermische Gerätearten.

Der Betriebsmodus (Schatten, assistiert, automatisch) gehört **nicht** in
eine Vorlage. Neue Geräte starten immer im Schattenmodus, und SunCore
schaltet in dieser Version nichts.

### Tarif- und Prognosekurven

Tarif- und Prognosevorlagen holen Kurven über `series`: entweder aus einem
Entitätsattribut oder aus der Antwort einer lesenden Aktion. Nur Aktionen
der eingebauten Liste (`tibber.get_prices`,
`solcast_solar.query_forecast_data`, `weather.get_forecasts`) werden
akzeptiert; jeder andere Dienst wird beim Laden mit
`template.error.action_not_allowed` abgelehnt. Eine neue Aktion braucht eine
Änderung in `suncore/core/actions.py` mit einem anhand der Quelle geprüften
Eintrag.

### Quellen, `tested_with` und `unverified`

- `source` ist Pflicht: mindestens ein Dokument oder eine Quelldatei mit
  Titel, URL und dem Datum, an dem du sie gelesen hast. Hänge GitHub-Links
  an einen Commit.
- `tested_with` nennt Integrationsversionen, mit denen du die Vorlage auf
  einem echten Home Assistant betrieben hast.
- Ist `tested_with` leer, muss `unverified` aufführen, was du nicht prüfen
  konntest. Die Anzahl erscheint auch in der
  [Kompatibilitätsliste](../compatibility.md). Der Assistent zeigt Vorlagen
  ohne `tested_with` mit dem Hinweis „Nicht an echter Hardware geprüft“ an.
- Entferne einen `unverified`-Eintrag nur, wenn du genau diese Tatsache
  geprüft hast.

## Testen

1. Schema und Lint prüfen (prüft auch die i18n-Schlüssel):

   ```bash
   uv run python scripts/validate_templates.py
   ```

2. Ein **Simulator-Konformitätsszenario**
   `tests/scenarios/<kind>/<id>.yaml` anlegen. Es legt fest, worauf die
   Demo-Installation auflösen muss, mit erwarteter Entität, Einheit und
   getroffenen Kriterien je Rolle:

   ```yaml
   template: generic.switch
   sim_scenario: summer_day
   user_input:
     nominal_w: 750
   roles:
     switch:
       entity: switch.sim_pool_pump
       criteria: [domain]
   ```

   Hat die Demo keine passende Entität, erweitere im selben PR die Registry
   des Simulators. Ausführen mit `uv run pytest -m simulator -k <id>`.

3. Abgeleitete Dateien neu erzeugen und die komplette Prüfung laufen
   lassen:

   ```bash
   uv run python scripts/gen_compat.py
   ./scripts/check.sh
   ```

## Übersetzungen (i18n-Schlüssel)

Jeder Schlüssel, den eine Vorlage nutzt (`name`, die `description` der
Rollen), muss in beiden Katalogen stehen,
`frontend/src/lib/i18n/locales/en.json` und `de.json`:

```json
"template.vendor.model.name": "Hersteller Modell",
"template.vendor.model.role.switch": "Der Schalter zum Ein- und Ausschalten"
```

`validate_templates.py` schlägt fehl, wenn ein Schlüssel in einem der beiden
fehlt.

## Ohne Pull Request ausprobieren: eigene Vorlagen

Vorlagen im Ordner `/data/templates/<kind>/` der App ersetzen die
eingebaute mit gleicher `id` oder kommen neu hinzu. Sie werden beim Start
geladen; eine fehlerhafte eigene Vorlage wird mit einer Warnung im
App-Protokoll übersprungen und beeinflusst die eingebauten nie. Du brauchst
einen Weg, eine Datei in den Datenordner der App zu legen; welches
Home-Assistant-Werkzeug das bietet, ist hier nicht geprüft. Zum Teilen ist
ein Pull Request besser.

## Checkliste für den Pull Request

- [ ] Vorlage in `templates/<kind>/<id>.yaml` mit Schema-Kopf, ohne Anker
      oder Aliase, ohne Entitäts-IDs.
- [ ] `source`-Einträge mit Titel, URL und Datum; GitHub-Links an einen
      Commit gehängt.
- [ ] `tested_with` gefüllt, oder `unverified` nennt alles, was du nicht
      geprüft hast.
- [ ] i18n-Schlüssel in `en.json` und `de.json`.
- [ ] Konformitätsszenario in `tests/scenarios/<kind>/<id>.yaml`; der
      Simulator-Test läuft durch.
- [ ] `uv run python scripts/validate_templates.py` und
      `uv run python scripts/gen_compat.py` ausgeführt; die neu erzeugte
      `docs/user/compatibility.md` gehört zur Änderung.
- [ ] `./scripts/check.sh` ist grün.
- [ ] Schreibt die Vorlage auf ein Gerät, sage das im PR; Steuerpfade
      bekommen ein Sicherheits-Review.
- [ ] Der PR-Text nennt, was du wie geprüft hast.
