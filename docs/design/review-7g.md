# Design-Durchgang, Schritt 7-G: Einstellungen und Einrichtung

Letzter Schritt des Design-Durchgangs und **Prüfstopp 2**. Nach „Jetzt“ ([7-E](review-7e.md))
und Plan/Analyse ([7-F](review-7f.md)) bekommen die *Einstellungen* und der
*Einrichtungsassistent* dieselbe ruhige Sprache: eine Übersicht statt einer langen Seite, keine
Karte in der Karte, selten Gebrauchtes eingeklappt. Grundlage sind Tokens und Bausteine aus
[7-D](review-7d.md) und [ADR-0019](../adr/0019-ui-design-system.md); Plan
[`phase-7.md`](../plans/phase-7.md) WP5 und Abschnitt 8 (7-G).

Alle Bilder stammen aus dem Demo-Modus (Szenario `summer_day`, Start 09:30, englisch,
`SUNCORE_DEMO_FLEX=1`). Größen: Handy 390×844 und Desktop 1280×800, hell und dunkel, doppelte
Pixeldichte, ganze Seiten. Vorher = Stand 7-F (`phase-7/f-plan-analysis`), aufgenommen mit
derselben Testdatei (`e2e/review-shots.spec.ts`). Die Unterseiten der Einstellungen gab es
vorher nicht; ihr Vorher-Bild ist die lange Einstellungsseite.

## Was sich geändert hat

### Einstellungen

| Bereich | Vorher (7-F) | Nachher (7-G) |
| --- | --- | --- |
| Aufbau | eine lange Seite: Assistent, zehn Einzelkarten mit „Bearbeiten“, HA-Entitäten, Benachrichtigungen, frühere Versionen, Sicherung, Expertenhinweis (Desktop etwa 7500 px hoch) | Übersicht mit fünf Gruppen: **Deine Anlage**, **Geräte**, **Ziele und Betriebsart**, **Home Assistant**, **Erweitert**; je Gruppe eine Zeile mit Symbol und Kurzfassung („6 Geräte“, „Schattenmodus“) |
| Desktop | – | zwei Spalten: links die Gruppen, rechts die gewählte Seite; `#/settings` öffnet „Deine Anlage“ |
| Handy und Tablet | – | erst die Gruppenliste, dann die Seite allein; oben „‹ Einstellungen“ zurück |
| Schritte der Einrichtung | zehn getrennte Karten | je Seite eine Karte mit Zeilen und Trennlinien; „Bearbeiten“ öffnet den Schritt wie bisher |
| Einrichtungsassistent | große Karte mit Text und Knopf | eine Statuszeile mit Haken: „Die Einrichtung ist abgeschlossen“ und „Einrichtung erneut starten“ |
| Geräte | nur „6 Geräte“ | zusätzlich die Liste der eingerichteten Geräte (Name, Vorlage, Art), nur lesend |
| Benachrichtigungen | Karten in der Karte (Kategorien, Ziele, Ruhezeiten) | flache Abschnitte mit Trennlinie im selben Kasten |
| Expertenmodus | Schalter in der Kopfleiste, Hinweis am Seitenende | Schalter in **Erweitert** mit Erklärung; die Kopfleiste ist ruhiger (Marke, Ziele, Status, Not-Aus). Was-wäre-wenn verlinkt dorthin |
| Über SunCore | fehlte | Version (aus `/info`), Datenquelle (Demo oder Home Assistant), Lizenz, Link zum Projekt |
| Diagnose | – | Platz in **Erweitert** reserviert (zwischen Expertenmodus und früheren Versionen); das Panel kommt mit 7-I |

### Einrichtungsassistent

| Bereich | Vorher | Nachher |
| --- | --- | --- |
| Fortschritt | „Schritt 3 von 10“, Balken, zehn Schritte in einer Liste | vier **Abschnitte** als Leiste über dem Schritt (Grundlagen · Deine Anlage · Geräte und Ziele · Abschluss; erledigt grün, aktuell blau); die Schrittliste ist nach Abschnitten gruppiert |
| Kopf des Schritts | „3. Netzanschluss“ | kleine Zeile „Deine Anlage · Schritt 3 von 10“, darunter der Titel ohne Nummer |
| Inhalt | jede Gruppe als eigene Karte in der Schritt-Karte | flache Abschnitte mit Trennlinie; ein Kasten pro Schritt |
| Netzanschluss | neun Gruppen untereinander | Sensoren und Grenzen offen; „Lastspitzen, Einspeisegrenzen, §14a und Notstrom“ und „Energiezähler und Hausverbrauch (optional)“ eingeklappt. Sie öffnen sich von selbst, wenn darin etwas eingestellt ist oder ein Fehler gemeldet wird |
| Fußleiste | „Einrichtung verlassen“ als Knopf, Zurück, Überspringen, Weiter | über die ganze Kartenbreite, bleibt beim Scrollen unten: „Einrichtung verlassen“ und „Überspringen“ als ruhige Text-Knöpfe, „Zurück“ umrandet, „Speichern und weiter“ gefüllt |

### Was erreichbar bleibt

- Alle zehn Schritt-IDs und Links (`#/setup/<schritt>`), alle Test-IDs, „Einrichtung erneut
  starten“, ungespeicherte Änderungen (Hinweis jetzt über jeder Einstellungsseite), frühere
  Versionen, Export und Import, HA-Entitäten, Benachrichtigungen, Expertenbereich.
- `#/settings` funktioniert weiter (Links aus Benachrichtigungen); neue Adressen
  `#/settings/system`, `/devices`, `/goals`, `/ha`, `/advanced`.
- Eine geöffnete Unterseite setzt den Fokus auf ihre Überschrift; die Gruppenliste ist eine
  Navigation mit `aria-current`. Eingeklappte Felder bleiben per Tastatur erreichbar
  (natives `details`).

### Barrierefreiheit und Technik

- axe ohne Befund auf der Übersicht und jeder Unterseite (hell und dunkel) und auf allen
  Assistenten-Schritten; keine waagerechte Scrollleiste bei 375 px (Übersicht, zwei Unterseiten,
  Netzanschluss).
- Neue Symbole: Gerät, Ziel, Glocke. Neue Schlüssel en/de für Abschnitte, Gruppen und „Über“.
- **Bundle:** der Standardpfad zählt alle Dateien zusammen. Jede Unterseite und jeden Schritt
  als eigenen Chunk zu laden, macht ihn *größer* (gemessen: 258,8 kB mit fünf
  Einstellungs-Chunks), weil jeder Chunk eigene Import-Listen, CSS und gzip-Kopf mitbringt.
  Stattdessen: Einstellungen bleiben ein (fauler) Chunk, der Assistent lädt **je Abschnitt**
  einen Chunk statt je Schritt (−8 kB), und der Build lässt Texte weg, die nur das Backend
  braucht (MQTT-Entitätsnamen, Benachrichtigungstexte; −2 kB je Sprache). Erste Anzeige
  122,6 → 121,4 kB (Budget 125), Standardpfad 249,6 → 244,1 kB (Budget 250),
  Experten-Extras 25,5 → 25,6 kB (Budget 30), ECharts unverändert.

## Vorher / Nachher

### Einstellungen (Übersicht)

| | Vorher (7-F) | Nachher (7-G) |
| --- | --- | --- |
| Handy hell | ![Einstellungen, Handy, hell, vorher](../assets/screenshots/before-7g/settings-phone-light.png) | ![Einstellungen, Handy, hell, nachher](../assets/screenshots/after-7g/settings-phone-light.png) |
| Handy dunkel | ![Einstellungen, Handy, dunkel, vorher](../assets/screenshots/before-7g/settings-phone-dark.png) | ![Einstellungen, Handy, dunkel, nachher](../assets/screenshots/after-7g/settings-phone-dark.png) |
| Desktop hell | ![Einstellungen, Desktop, hell, vorher](../assets/screenshots/before-7g/settings-desktop-light.png) | ![Einstellungen, Desktop, hell, nachher](../assets/screenshots/after-7g/settings-desktop-light.png) |
| Desktop dunkel | ![Einstellungen, Desktop, dunkel, vorher](../assets/screenshots/before-7g/settings-desktop-dark.png) | ![Einstellungen, Desktop, dunkel, nachher](../assets/screenshots/after-7g/settings-desktop-dark.png) |

### Einstellungen, Unterseiten (nachher)

| | Handy hell | Desktop dunkel |
| --- | --- | --- |
| Deine Anlage | ![Deine Anlage, Handy](../assets/screenshots/after-7g/settings-system-phone-light.png) | ![Deine Anlage, Desktop](../assets/screenshots/after-7g/settings-system-desktop-dark.png) |
| Geräte | ![Geräte, Handy](../assets/screenshots/after-7g/settings-devices-phone-light.png) | ![Geräte, Desktop](../assets/screenshots/after-7g/settings-devices-desktop-dark.png) |
| Home Assistant | ![Home Assistant, Handy](../assets/screenshots/after-7g/settings-ha-phone-light.png) | ![Home Assistant, Desktop](../assets/screenshots/after-7g/settings-ha-desktop-dark.png) |
| Erweitert | ![Erweitert, Handy](../assets/screenshots/after-7g/settings-advanced-phone-light.png) | ![Erweitert, Desktop](../assets/screenshots/after-7g/settings-advanced-desktop-dark.png) |

### Assistent: Netzanschluss (Schritt 3)

| | Vorher (7-F) | Nachher (7-G) |
| --- | --- | --- |
| Handy hell | ![Netz, Handy, hell, vorher](../assets/screenshots/before-7g/wizard-grid-phone-light.png) | ![Netz, Handy, hell, nachher](../assets/screenshots/after-7g/wizard-grid-phone-light.png) |
| Handy dunkel | ![Netz, Handy, dunkel, vorher](../assets/screenshots/before-7g/wizard-grid-phone-dark.png) | ![Netz, Handy, dunkel, nachher](../assets/screenshots/after-7g/wizard-grid-phone-dark.png) |
| Desktop hell | ![Netz, Desktop, hell, vorher](../assets/screenshots/before-7g/wizard-grid-desktop-light.png) | ![Netz, Desktop, hell, nachher](../assets/screenshots/after-7g/wizard-grid-desktop-light.png) |
| Desktop dunkel | ![Netz, Desktop, dunkel, vorher](../assets/screenshots/before-7g/wizard-grid-desktop-dark.png) | ![Netz, Desktop, dunkel, nachher](../assets/screenshots/after-7g/wizard-grid-desktop-dark.png) |

### Assistent: weitere Schritte

| | Vorher (7-F) | Nachher (7-G) |
| --- | --- | --- |
| Region, Handy hell | ![Region, vorher](../assets/screenshots/before-7g/wizard-region-phone-light.png) | ![Region, nachher](../assets/screenshots/after-7g/wizard-region-phone-light.png) |
| Solar, Desktop hell | ![Solar, vorher](../assets/screenshots/before-7g/wizard-pv-desktop-light.png) | ![Solar, nachher](../assets/screenshots/after-7g/wizard-pv-desktop-light.png) |
| Geräte, Desktop dunkel | ![Geräte-Schritt, vorher](../assets/screenshots/before-7g/wizard-devices-desktop-dark.png) | ![Geräte-Schritt, nachher](../assets/screenshots/after-7g/wizard-devices-desktop-dark.png) |
| Zusammenfassung, Handy dunkel | ![Zusammenfassung, vorher](../assets/screenshots/before-7g/wizard-summary-phone-dark.png) | ![Zusammenfassung, nachher](../assets/screenshots/after-7g/wizard-summary-phone-dark.png) |

## Fragen an den Maintainer

1. **Prüfstopp 2:** Passen Übersicht mit Unterseiten und die vier Abschnitte im Assistenten?
   Änderungswünsche setze ich als `fix`-Commit auf 7-G um, bevor 7-I und 7-J darauf aufbauen.
2. **Phase-6-Felder** (`para14a` und `shed_rank` je Gerät, Liste der Geräte im Inselbetrieb,
   manueller MQTT-Broker) sind **nicht** in diesem PR: sie speisen die Sicherheitsregeln G1–G6
   und brauchen die `control-safety`-Durchsicht. Vorschlag: eigener PR direkt nach 7-G, damit
   der Design-Prüfstopp nicht darauf wartet. Einverstanden?
3. **Filter im Entscheidungsprotokoll** (Frage 3 aus 7-F): ohne Antwort bleibt es bei der
   einklappbaren Karte; ein unteres Blatt (`Sheet`) habe ich nicht gebaut. Reicht das?
4. **Weitere eingeklappte Felder:** nur der Netzanschluss hat jetzt „mehr“-Bereiche. Kandidaten
   für dasselbe Muster wären das Batteriemodell und die Prognosequellen im Solar-Schritt. Soll
   ich die auch einklappen, oder lieber alles sichtbar lassen, solange man den Assistenten
   selten benutzt?
5. **Budget-Messung:** Der Standardpfad zählt alle faulen Chunks mit; „mehr Chunks“ hilft
   deshalb nicht, sondern schadet. Soll das so bleiben (dann spare ich weiter über weniger,
   größere Chunks), oder soll das Budget künftig nur zählen, was eine Ansicht tatsächlich lädt?
6. **„Live-Vorschau an derselben Stelle“** (Plan WP5) habe ich nicht umgesetzt: nur
   Netzanschluss (Vorzeichen-Prüfung) und Zusammenfassung (Planvorschau) haben eine Vorschau,
   und beide stehen direkt bei dem, was sie prüfen. Einverstanden?
