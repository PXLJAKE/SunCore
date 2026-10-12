# Projekt-Briefing: Energiemanagement-App für Home Assistant

> **Für Claude Code.** Dieses Dokument ist die verbindliche Produkt- und Technikvorgabe für dieses Repository. Es liegt unter `docs/BRIEF.md` und wird nicht in jede Session geladen – lies es zu Beginn jeder Phase gezielt (Inhaltsverzeichnis unten).
> Der Projektname ist der Name dieses GitHub-Repositories; im Text steht dafür **{PROJEKT}**.

**Inhalt**

0. Deine Rolle und Arbeitsregeln
1. Vision und Positionierung
2. Zielplattform und Rahmenbedingungen
3. Architektur
4. Funktionsumfang (4.1–4.14)
5. Safety und Robustheit
6. Nicht-Ziele
7. Open Source, Dokumentation, Übersetzungen
8. Qualitätskriterien (Definition of Done)
9. Referenzsystem des Maintainers
10. Phasenplan
11. Agent-Team, Skills und Hooks
12. Phase 0 – deine erste Aufgabe

---

## 0. Deine Rolle und Arbeitsregeln

Du bist Lead Engineer und Architekt eines Open-Source-Projekts, das ein Energiemanagementsystem (EMS) als Home-Assistant-App baut. Du koordinierst ein Team aus Subagents (Abschnitt 11) und lieferst in Phasen (Abschnitt 10).

1. **Verstehen → planen → bauen.** Jede Phase beginnt mit einem Plan in `docs/plans/phase-<n>.md` (Arbeitspakete, betroffene Dateien, Tests, Risiken, offene Fragen). Ich gebe den Plan frei, dann setzt du um.
2. **Verifizieren statt raten.** Home Assistant ändert sich schnell. Beispiel: Seit HA 2026.2 heißen Add-ons „Apps“, Repo-Layout und Konfigurationsschlüssel nutzen aber weiterhin die `addon`-Bezeichnungen. Prüfe jede HA-Schnittstelle (Apps/Supervisor, Ingress, WebSocket-API, MQTT Discovery, Energy-Prefs, Statistiken) gegen die aktuelle Entwicklerdoku auf developers.home-assistant.io und gegen aktuelle Beispiel-Repos, bevor du sie nutzt. Erfinde niemals Entity-IDs, Services, Attributnamen, Modbus-Register oder API-Felder. Wenn du etwas nicht verifizieren kannst, markiere es im Code und im PR als `UNVERIFIED`.
3. **Kleine, überprüfbare Schritte.** Ein Thema pro Pull Request, Conventional Commits, Tests und Lint grün, CHANGELOG und Doku aktualisiert. Committe und pushe Zwischenstände bei langen Aufgaben.
4. **Gedächtnis über Sessions hinweg.** Cloud-Sessions teilen keinen Kontext. Pflege deshalb:
   - `docs/STATUS.md` – aktueller Stand, was fertig ist, was offen ist, nächste Schritte, bekannte Probleme.
   - `docs/adr/NNNN-titel.md` – jede Architekturentscheidung mit Kontext, Optionen, Entscheidung, Konsequenzen.
   - `docs/roadmap.md` – Phasen und Meilensteine mit Status.
   Lies `CLAUDE.md`, `docs/STATUS.md` und die relevanten ADRs zu Beginn jeder Session.
5. **Fehlt etwas, frag – und arbeite am Rest weiter.** Fehlende Zugänge, Hardware-Details oder Produktentscheidungen landen im PR unter „Offene Fragen an den Maintainer“. Nichts stillschweigend mocken, als wäre es echt.
6. **Sicherheit vor Komfort.** Alles, was reale Geräte schaltet oder Sollwerte schreibt, läuft durch die Safety-Schicht (Abschnitt 5) und wird vom Agent `control-safety` geprüft.
7. **Sprachen.** Code, Kommentare, Commit-Messages, Issues und die Primärdokumentation auf Englisch. Deutsch ist die erste Übersetzung (UI und Doku). PR-Beschreibungen und Fragen an mich auf Deutsch.
8. **Kosten bewusst einsetzen.** Ich nutze Claude Pro plus Nutzungsguthaben für Fable. Setze Fable-Subagents gezielt ein (Architektur, Optimierer, Regler/Safety, Abschluss-Reviews), Routinearbeit mit Sonnet, mechanische Arbeit mit Haiku. Keine großen parallelen Fan-outs. Lies große Dateien gezielt statt komplett.
9. **Lizenzen respektieren.** Konzepte von evcc, EMHASS, Predbat und anderen zu studieren ist ausdrücklich erwünscht. Code übernimmst du nur bei kompatibler Lizenz und mit Attribution in `NOTICE`.

---

## 1. Vision und Positionierung

{PROJEKT} ist eine Home-Assistant-App, die PV-Erzeugung, Batteriespeicher, feste und dynamische Stromtarife und steuerbare Verbraucher (Klimaanlagen, Wärmepumpe, Heizstab, Wallbox, schaltbare und verschiebbare Lasten) gemeinsam optimiert. Sie verbindet die Echtzeit-Überschussregelung von evcc mit der vorausschauenden Optimierung von EMHASS – aber deutlich einfacher einzurichten, besser visualisiert, erklärbar und durch die Community erweiterbar.

**Leitprinzipien**
- **In 15 Minuten produktiv:** Der Setup-Assistent erkennt Entities automatisch, prüft Vorzeichen und Einheiten live und startet im Schattenmodus.
- **Erklärbar:** Jede Entscheidung hat ein „Warum?“ in Klartext.
- **Sicher:** Schattenmodus als Standard, Fail-safe, Watchdogs, harte Grenzen, die nie verletzt werden.
- **Einfach für Einsteiger, tief für Experten:** Standardansicht mit guten Defaults, Expertenmodus legt jeden Parameter offen.
- **Lokal und privat:** keine Cloud-Pflicht, keine Telemetrie.
- **Community-erweiterbar:** Gerätevorlagen, Tarif- und Prognose-Adapter, Regionsprofile und Übersetzungen lassen sich ohne Kernänderung beisteuern.

Die README erklärt ehrlich, was evcc, EMHASS und Predbat gut machen, was {PROJEKT} anders macht und wann man welches Tool nimmt. Koexistenz ist ausdrücklich vorgesehen (z. B. evcc als Aktor für den Ladepunkt, siehe 4.5).

---

## 2. Zielplattform und Rahmenbedingungen

- **Laufzeit:** Home-Assistant-App (Supervisor). Apps gibt es nur auf Home Assistant OS – die Doku sagt das klar. Die Architektur trennt die HA-Verbindung sauber ab, damit später ein **Standalone-Container** für HA-Container-Nutzer (Verbindung per Long-Lived Access Token) möglich ist.
- **Architekturen:** amd64 und aarch64 (32-bit wird von HA nicht mehr unterstützt – im ADR verifizieren).
- **Mindest-Hardware:** Raspberry Pi 4 / Home Assistant Green (4 GB RAM). Ziele: < 300 MB RAM im Leerlauf, Planer-Lauf < 10 s, UI flüssig auf Tablets.
- **UI:** Ingress-Panel in der HA-Seitenleiste (`panel_admin` für Einstellungen). Standardmäßig kein offener Port; ein optionaler API-Port mit Authentifizierung ist abschaltbar. Frontend nutzt relative Pfade (Ingress-Basispfad).
- **Daten:** persistent unter `/data` (damit in HA-Backups enthalten); große Zeitreihen ggf. vom Backup ausnehmen und aus HA-Statistiken rekonstruierbar halten. Konfigurations-Export/-Import als Datei.
- **HA-Kommunikation:** WebSocket-API (State-Abo, Services/Aktionen, Registries, Energy-Prefs, Langzeitstatistiken), REST nur wo nötig. Supervisor-Token nie loggen.
- **Auslieferung:** vorgebaute Multi-Arch-Images auf GHCR über GitHub Actions (kein lokaler Build beim Nutzer). Supervisor-Watchdog über Health-Endpoint.

---

## 3. Architektur

Vorschlag – in Phase 0 per ADR bestätigen oder begründet ändern.

**Module**
- `core/` – Domänenmodell: Assets (Netzanschluss, PV-Generator, Wechselrichter, Batterie, Verbraucher), Zeitreihen-Signale, Capabilities, Einheiten.
- `ha/` – HA-Connector (Supervisor-Token oder Long-Lived Token), Entity-Cache, Service-Aufrufe, Schreib-Rate-Limiter.
- `forecast/` – PV-, Last-, Preis- und Temperaturprognosen: Adapter, Blending, Kalibrierung.
- `tariff/` – Bezug und Einspeisung inkl. Preisformel und Regionsprofilen.
- `planner/` – vorausschauende Optimierung (LP/MILP, Model Predictive Control) mit Heuristik-Fallback.
- `controller/` – Echtzeit-Regler: Überschussverteilung, Peak Shaving, Export-Limit.
- `safety/` – Invarianten, Guards, Watchdog, Fail-safe, Plausibilitätsprüfung.
- `devices/` – Gerätetreiber und deklarative Gerätevorlagen (YAML + JSON-Schema).
- `api/` – FastAPI: REST plus WebSocket für Live-Daten.
- `storage/` – SQLite: Konfiguration (versioniert, mit Migrationen), Zeitreihen mit Downsampling, Entscheidungslog.
- `sim/` – Simulator (virtuelles Haus) für Entwicklung, CI, Demo-Modus und Backtesting.
- `frontend/` – Single-Page-App.
- `integration/` und `cards/` – optionale Companion-Integration (HACS) und Lovelace-Karten als eigene Pakete.

**Tech-Vorschlag:** Python 3.12+ mit asyncio, FastAPI, Pydantic v2, SQLite. Optimierer mit HiGHS (z. B. `highspy`) – aarch64-Wheels und Lösungszeiten auf einem Pi prüfen. Frontend TypeScript + Vite + Svelte 5 oder React (ADR), Apache ECharts für Diagramme, ICU-MessageFormat für i18n. Tests mit pytest, hypothesis, vitest, Playwright.

**Zwei Regelebenen**
1. **Planer:** läuft alle 15 Minuten und bei Ereignissen (neue Preise, Prognose-Update, große Abweichung, Konfigurationsänderung, Nutzer-Override). Berechnet einen Fahrplan über 24–48 h (mindestens 24 h, bis zum Ende bekannter Preise) im 15-Minuten-Raster.
2. **Echtzeit-Regler:** läuft alle 5–10 s (konfigurierbar), setzt den Fahrplan in Stellgrößen um, verteilt den tatsächlichen Überschuss und reagiert auf Wolken und Lastspitzen. Respektiert Hysterese, Mindestlauf- und Pausenzeiten, Rampen und Schreibraten-Limits.
Der Regler darf vom Plan abweichen, wenn die Realität abweicht, aber niemals Safety-Grenzen verletzen.

**Entities in HA bereitstellen:** ADR mit Abwägung zwischen MQTT Discovery (die App kann MQTT-Zugangsdaten über die Supervisor-Services-API beziehen, `mqtt:want`) und einer Companion-Integration via HACS (native Services, Diagnostics, Repairs, Supervisor-Discovery). Empfehlung: v1 mit MQTT Discovery plus voll nutzbarer UI ohne MQTT; Companion-Integration später.

---

## 4. Funktionsumfang

### 4.1 Datenquellen und Zuordnung
- **Netz:** Leistung (ein Sensor mit Vorzeichen oder getrennt Bezug/Einspeisung), Energiezähler, optional Phasenleistungen (Schieflast, 1/3-Phasen-Umschaltung).
- **PV:** mehrere Generatoren, Wechselrichter und Strings, Ausrichtungen (z. B. Ost-West), DC- und AC-Werte.
- **Batterie:** SoC, Leistung, Kapazität, Lade-/Entladegrenzen, Wirkungsgrade, Min-/Max-SoC.
- **Hausverbrauch:** gemessen oder aus der Bilanz berechnet; gesteuerte Verbraucher werden für die Grundlastprognose herausgerechnet.
- **Wetter/Temperatur:** HA-Wetter-Entity (`weather.get_forecasts`) oder Open-Meteo.
- **Vorbelegung** aus dem HA-Energie-Dashboard (Energy-Prefs), falls konfiguriert.
- **Einheiten und Vorzeichen:** automatische Erkennung (W/kW, Wh/kWh), Live-Plausibilitätscheck („Die Batterie lädt gerade mit 2,1 kW – stimmt das?“), Vorzeichen-Umkehr per Klick.
- **Robustheit:** Erkennung veralteter Werte, `unavailable`/`unknown`, Ausreißerfilter, Ersatzwerte. Ein hängender Wechselrichter darf das System nie in einen gefährlichen Zustand bringen.

### 4.2 Prognosen
- **PV-Adapter:** Solcast (HACS-Integration, P10/P50/P90 aus den Prognose-Attributen nutzen, nie selbst die Solcast-API abfragen, wenn die Integration läuft – API-Limits), Forecast.Solar (Core), Open-Meteo Solar Forecast (HACS), generischer Adapter (beliebiger Sensor mit Prognose-Attribut, Mapping per Vorlage), eingebauter Fallback (Open-Meteo-Strahlung + Anlagenmodell).
- **Mehrere Quellen gleichzeitig:** Vergleich und gewichtetes Blending nach gemessener Prognosegüte.
- **Selbstkalibrierung:** lernt systematische Abweichungen je Tageszeit, Saison und Wetterlage; erkennt Clipping (WR-Limit, Export-Limit).
- **Lastprognose:** Profile aus der HA-Historie (Wochentag, Feiertag über die Workday-Integration, Temperatur), exponentielle Glättung; optional ein leichtgewichtiges ML-Modell, wenn es auf aarch64 sinnvoll läuft. Gesteuerte Verbraucher werden separat modelliert.
- **Unsicherheit:** optional konservativ planen (z. B. P10 für „Akku abends voll“).
- **Prognosegüte im UI:** Fehler und Bias je Quelle.

### 4.3 Tarife
- **Bezug fix:** Einheitspreis, HT/NT, Zeitfenster, saisonal.
- **Bezug dynamisch:** Tibber (Core-Integration inkl. Preisabruf-Aktion), Nordpool (Core), ENTSO-E und EPEX Spot (HACS), Octopus, aWATTar sowie ein eingebauter Fallback für Day-Ahead-Preise (z. B. Energy-Charts-API; Nutzungsbedingungen prüfen).
- **Preisformel:** Börsenpreis → Endpreis mit konfigurierbaren Bestandteilen (Netzentgelt inkl. zeitvariabler Netzentgelte nach §14a Modul 3, Abgaben, Marge, MwSt.). Endpreise von Anbietern direkt nutzbar.
- **Raster:** 15- und 60-Minuten-Preise (Day-Ahead kommt inzwischen in 15-Minuten-Auflösung).
- **Einspeisung:** fix (EEG, auch gestaffelt nach kWp-Anteilen als Mischsatz), dynamisch (Marktwert/Direktvermarktung), negative Preise per Regionsprofil (DE: Neuanlagen erhalten bei negativen Preisen keine Vergütung → Einspeisung vermeiden, Verbrauch und Akku vorziehen, notfalls abregeln).
- **Ersparnis:** Vergleich gegen eine Baseline „ohne EMS“ (gleiche Anlage, nur Eigenverbrauchslogik des Wechselrichters) und „ohne PV“.

### 4.4 Batteriestrategien
- Eigenverbrauch maximieren (Standard).
- **Ziel-SoC zu einer Uhrzeit** (z. B. „abends 100 %, damit die Nacht gedeckt ist“) unter Berücksichtigung der Prognose-Unsicherheit.
- **Netzladung bei dynamischem Tarif** nur, wenn die Preisspreizung Wirkungsgradverluste, Degradationskosten und eine Sicherheitsmarge übersteigt; Ladefenster kommen aus dem Optimierer. Option „Netzladung nur nachts“.
- **Netzladung bei Fixtarif** nur bei HT/NT-Spreizung oder im Notfall (SoC unter Reserve, Prognose schlecht) – standardmäßig aus.
- **Entladesperre:** Akku in günstigen Stunden halten, um teure Stunden zu decken; Akku nicht ins E-Auto entladen (konfigurierbar).
- **Reserve/Notstrom:** Mindest-SoC getrennt von BMS-Drift (BMS kann unter die eingestellte Grenze sacken); optional dynamisch erhöht.
- **Schonung:** Max-SoC-Begrenzung, periodische Vollladung zur BMS-Kalibrierung, Zyklenzähler, Degradationskosten in €/kWh.
- **Hardware-Ansteuerung über Batterie-Adapter:**
  - generisch über HA-Entities (number/select/switch/Aktionen), deklarativ als Vorlage;
  - Referenzadapter Kostal Plenticore G3 (Modbus TCP/SunSpec, externe Batteriesteuerung mit Fallback-Timeout im Wechselrichter) – Register ausschließlich aus der offiziellen Kostal-Modbus-Dokumentation, Schreibrate begrenzt, Rücklesen nach dem Schreiben;
  - weitere Hersteller als Community-Vorlagen (Huawei, SMA, Fronius, Victron, GoodWe, Sungrow, SolarEdge, Fox, Solax …).
- **Bidirektionales Laden (V2H/V2G)** ist im Datenmodell vorgesehen, Umsetzung später.

### 4.5 Verbraucher und Überschussverteilung
**Gemeinsame Konzepte aller Geräte:** Priorität (Drag & Drop), Leistungsmodell (gemessen | aus Energiezähler abgeleitet | gelernt | Nennleistung), Steuerart (Ein/Aus | Stufen | stufenlos | Sollwert | Modus), Mindestlauf- und Mindestpausenzeit, maximale Schaltvorgänge pro Stunde, Hysterese mit Verzögerung, Zeitfenster, Komfortgrenzen, manuelle Overrides (Boost für N Minuten, Pause bis …). Manuelle Änderungen durch den Nutzer werden erkannt und respektiert.

**Leistung ohne Leistungssensor** (wichtiger Fall, z. B. Klimaanlagen, die nur einen Tagesenergiezähler in Wh liefern): mittlere Leistung aus Zähler-Deltas schätzen (Latenz und Quantisierung berücksichtigen), ein Modell lernen (Modus, Soll-Ist-Differenz, Außentemperatur → Leistung), die Netzbilanz zur Validierung nutzen und die Unsicherheit im Regler einpreisen (konservativere Hysterese). Geräte mit echtem Leistungssensor nutzen diesen direkt. Beide Wege müssen gleichwertig funktionieren.

**Gerätetypen**
- **Klimaanlagen (Split, Multi-Split):** `climate`-Entities; Heizen/Kühlen/Auto; Komfortband (z. B. 22–23 °C) mit Vorheizen/Vorkühlen bei Überschuss (thermische Masse nutzen), Sollwertverschiebung statt Ein/Aus wo möglich, Kompressorschutz (Mindestlauf/-pause), Fenster-offen und Abwesenheit, Ruhezeiten, optional externe Raumtemperatursensoren, gemeinsame Außeneinheit bei Multi-Split.
- **Wärmepumpe:** SG-Ready (2 Kontakte, 4 Zustände), Sollwertanhebung (Warmwasser, Vorlauf, Puffer), Betriebsmodi, lernbares thermisches Modell (RC-Modell), COP abhängig von der Außentemperatur, §14a-Dimmung respektieren.
- **Heizstab/Warmwasser:** stufig (1–3 Relais) oder stufenlos (z. B. my-PV, Askoma über HA/Modbus), Maximaltemperatur, Legionellenprogramm, Mindestladung.
- **Wallbox/E-Auto:** Modi Aus | Sofort | Min+PV | Nur PV | Ziel (SoC bis Uhrzeit, günstigste Fenster), Mindeststrom 6 A, 1/3-Phasen-Umschaltung, Hysterese, Fahrzeug-SoC aus HA, Ladeverluste. Treiber über HA-Integrationen (OCPP, go-e, Easee, KEBA, openWB, Wallbox, Tesla …) und optional **evcc als Aktor** (evcc regelt den Ladepunkt, {PROJEKT} gibt Modus, Ziel und Limit vor).
- **Schaltbare Lasten:** Steckdosen/Relais (Pool, Bewässerung, Infrarotheizung …).
- **Verschiebbare Programme:** Wasch-/Spülmaschine/Trockner mit Laufzeit, Lastprofil und „fertig bis“; Start per Smart-Plug/Remote-Start oder Benachrichtigung „Jetzt starten“.
- **Generischer stufenloser Verbraucher:** number-Entity in W, A oder %.

**Echtzeit-Überschussverteilung**
- Verfügbarer Überschuss = Einspeisung + aktuelle Leistung gesteuerter Verbraucher + strategieabhängiger Anteil der Batterieladeleistung − Reserve.
- Verteilung nach Priorität und Plan mit Hysterese und Verzögerung. Batterieregeln wie „Akku zuerst bis X % bzw. bis Uhrzeit Y“ oder „Verbraucher vor Akku, wenn die Prognose den Akku-Zielwert trotzdem erreicht“.
- Glättung gegen Wolkenflackern, Mindestabstand zwischen Schaltvorgängen, nachweislich keine Oszillation (Tests).

### 4.6 Netzanschluss: Peak Shaving, Export-Limit, §14a
- **Bezugsgrenze** (Hausanschluss, Sicherung, Leistungstarif wie in Belgien) mit Lastabwurf-Reihenfolge und Akku-Unterstützung; Monats-Peak-Tracking.
- **Einspeisebegrenzung** (statisch in W oder % kWp, dynamisch nach Preis): erst Verbraucher und Akku hochfahren, dann WR-Leistung über den Adapter begrenzen; Null-Einspeisung als Option.
- **§14a EnWG:** Steuersignal des Netzbetreibers (Entity, Relais, Steuerbox; EEBus später) → gesteuerte Verbraucher auf die zulässige Leistung begrenzen, protokollieren.
- **Phasen:** optionale Schieflastgrenzen.

### 4.7 Notstrom-Bewusstsein
Netzausfall erkennen (Entity von Backup-Switch oder Wechselrichter). Im Inselbetrieb: Lastabwurf nach Priorität, Akku-Reserve schützen, keine Netzladung, Benachrichtigung.

### 4.8 Optimierer (Planer)
- **Zielfunktion:** Kosten minimieren (Bezug − Einspeiseerlös + Batteriedegradation + Komfortstrafen + Schaltkosten), optional CO₂-Gewichtung.
- **Nebenbedingungen:** Energiebilanz je Intervall; Batterie (SoC- und Leistungsgrenzen, getrennte Lade-/Entladewirkungsgrade); Netzgrenzen und Export-Limit; Geräte (thermische Modelle, Laufzeiten, Deadlines, Mindestlaufzeiten per MILP wo nötig); Zielzustände (Akku abends X %, Auto-SoC bis Uhrzeit).
- **Laufzeit:** 24–48 h, 15-Minuten-Raster, Warmstart, Zeitbudget; Heuristik-Fallback bei Timeout oder Unlösbarkeit (mit Hinweis im UI).
- **Erklärbarkeit:** Entscheidungsgründe je Intervall (Preis, Prognose, Ziel, Limit) als Klartext, z. B. „Akku lädt 02:00–04:00 aus dem Netz, weil Strom dort deutlich günstiger ist als morgen 18:00 und die vorsichtige PV-Prognose den Abend nicht deckt“.
- **What-if und Backtesting:** Plan mit anderen Parametern oder Tarifen gegen echte Historie rechnen („hätte gespart“).

### 4.9 Visualisierung und UI
- **„Jetzt“:** animierter Energiefluss (PV, Netz, Akku, Haus, jedes Gerät), aktueller Modus, nächste geplante Aktionen, Warnungen.
- **Plan:** kombiniertes Diagramm mit Preis (Farbbänder/Linie), PV-Prognose als P10–P90-Band plus Ist, Lastprognose plus Ist, Akku-SoC geplant und Ist, gestapelte Flächen für Bezug/Einspeisung, Gantt-Zeilen für Geräte; 24 h/48 h, Zoom und Pan, Tooltips mit Begründung.
- **Historie und Analyse:** Tag/Woche/Monat/Jahr mit Autarkie, Eigenverbrauchsquote, Kosten und Ersparnis gegen die Baseline, Sankey je Zeitraum, Heatmap (Verbrauch nach Stunde × Wochentag), Batteriezyklen, Prognosegüte je Quelle, Gerätelaufzeiten.
- **Geräteseiten:** Status, Regelhistorie, Parameter, Override-Buttons.
- **Entscheidungslog:** filterbar – „Warum hat X um Y geschaltet?“
- **Export:** CSV und PNG.
- **Design:** HA-Look (Theme-Variablen, Hell/Dunkel), mobile-first für iPad und Smartphone, barrierearm (Kontrast, Tastatur, ARIA), Live-Updates per WebSocket ohne Ruckeln auf Tablets.
- **Optional:** Lovelace-Karten (Energiefluss, Plan-Mini, Gerätesteuerung) für Wand-Tablets, inklusive Kompaktmodus für sehr kleine Displays.

### 4.10 Setup-Assistent
Jeder Schritt mit kurzer Erklärung, guten Defaults, „Überspringen“ wo sinnvoll und Live-Vorschau der zugeordneten Werte:
1. Sprache, Region (Regionsprofil), Währung, Zeitzone.
2. Übernahme aus dem HA-Energie-Dashboard (falls konfiguriert).
3. Netzanschluss: Sensoren, Vorzeichen-Check, Anschlussgrenzen, Export-Limit, §14a ja/nein.
4. PV: Erzeuger, Wechselrichter, kWp und Ausrichtung, Prognosequelle(n) mit Auto-Erkennung.
5. Batterie: Sensoren, Kapazität, Grenzen, Steuerungs-Adapter oder „nur beobachten“.
6. Tarif: Bezug (fix/dynamisch, Quelle, Preisformel) und Einspeisung (inkl. gestaffelt).
7. Verbraucher hinzufügen: Vorlagen-Galerie mit Suche (Klima, Wallbox, Wärmepumpe, Heizstab, Steckdose, Programm), Entity-Zuordnung, Leistungsmodell, Komfortgrenzen.
8. Prioritäten und Ziele (Drag & Drop; „Akku abends voll“, „Klima vor Einspeisung“ …).
9. Betriebsmodus: Schattenmodus (Standard) | Assistiert | Automatisch, je Gerät überschreibbar.
10. Zusammenfassung mit Systemcheck (Sensoren aktuell? Einheiten plausibel? Bilanz stimmt?) und erster Plan als Vorschau.

Nachträglich: Geräte jederzeit über dieselben Schritte hinzufügen, ändern oder entfernen. Konfigurationsschema versioniert, mit Migrationen.

### 4.11 Betriebsmodi
- **Schattenmodus:** rechnet, zeigt, protokolliert „hätte geschaltet“, schaltet nichts. Standard nach der Installation und für jedes neue Gerät. Auswertung „Was wäre gespart worden“.
- **Assistiert:** schlägt vor, der Nutzer bestätigt (UI oder Benachrichtigung mit Aktion).
- **Automatisch:** schaltet selbst innerhalb aller Grenzen.
- **Globaler Not-Aus** („alles zurück in Normalbetrieb“), überall gut erreichbar.

### 4.12 Expertenmodus
Ein Schalter blendet alle erweiterten Parameter ein – jeweils mit Erklärung, Einheit, Default, gültigem Bereich und „Zurücksetzen“: Regler-Intervalle, Hysteresen, Glättung, Horizont und Raster, Solver-Optionen und Zeitbudget, Gewichte der Zielfunktion, Degradationskosten, Wirkungsgrade, Prognose-Blending und Sicherheitsabschläge, Reserve-Logik, Schreibraten-Limits, Geräte-Feinparameter, Debug-Ansichten (Rohdaten, Plan-JSON, Solver-Statistik), Simulation, What-if und Backtesting. Die Parameter-Referenz der Doku wird aus dem Code generiert.

### 4.13 Home-Assistant-Integration
- **Entities:** Status/Modus, nächste Aktion, Plan-Kennzahlen, Ersparnis, Prognosegüte; Schalter (global Auto, je Gerät Auto), Selects (Batteriestrategie, Wallbox-Modus), Numbers (Min-SoC, Ziel-SoC, Komfortgrenzen).
- **Aktionen:** neu planen, Boost Gerät X für N Minuten, Pause bis, Akku zwangsladen/sperren bis, Modus setzen.
- **Ereignisse** für Automationen (z. B. günstiges Fenster beginnt, Gerät geschaltet).
- **Benachrichtigungen:** Fehler, günstige Fenster, „Jetzt Waschmaschine starten“.
- **Energie-Dashboard:** keine Doppelzählung.

### 4.14 Simulator und Demo-Modus
- Virtuelles Haus (PV aus Wetterdaten/Profil, Lastprofile, Batterie, Geräte mit einfachen Physikmodellen, Tarife) für Entwicklung, CI, Demo-Modus (Screenshots für die Doku) und Backtesting mit echter HA-Historie.
- Szenarien als Dateien: Sommertag, Wintertag mit Nebel, Wolkenflackern, negative Preise, Preisspitze, Sensor fällt aus, Wechselrichter hängt, Netzausfall, Klima-Mindestlaufzeit, Auto kommt spät, Klima nur mit Energiezähler.

---

## 5. Safety und Robustheit (nicht verhandelbar)

- **Harte Invarianten**, abgesichert durch Property-based-Tests: Bezugsgrenze nicht überschreiten (soweit steuerbar), Akku nie durch uns unter die Reserve entladen, Mindestlauf-/Pausenzeiten und maximale Schaltfrequenz einhalten, Komfortgrenzen einhalten.
- **Fail-safe:** Bei Absturz, Stopp, Update oder Verbindungsverlust definierte Rückfallzustände (Wechselrichter in Eigenregelung, Geräte im Normalbetrieb). Heartbeats/Watchdogs dort, wo Hardware Timeouts unterstützt.
- **Plausibilität:** veraltete Werte, Sprünge, Einheitenfehler, Bilanzprüfung → bei Verdacht den betroffenen Bereich pausieren und warnen.
- **Schreibschutz:** Rate-Limits, nur bei echter Änderung schreiben (Flash/EEPROM schonen), nach dem Schreiben zurücklesen.
- **Overrides:** manuelle Eingriffe des Nutzers werden erkannt und für eine konfigurierbare Zeit respektiert.
- **Logs:** strukturiert, ohne Secrets. Anonymisierter Diagnose-Export für Bug-Reports.
- **Security:** standardmäßig nur Ingress, Admin-Panel für Einstellungen, Abhängigkeiten gepinnt, Dependabot/Renovate, CodeQL.

---

## 6. Nicht-Ziele (v1)

Keine Cloud-Pflicht, keine Telemetrie, keine eigene Modbus-Implementierung für alles (zuerst vorhandene HA-Integrationen nutzen), kein Ersatz für Schutzfunktionen der Hersteller, keine Rechts- oder Steuerberatung (Regionsprofile sind Hilfen mit Quellenangabe).

---

## 7. Open Source, Dokumentation, Übersetzungen

- **Lizenz:** Apache-2.0 (`LICENSE`, `NOTICE`), SPDX-Header in Quelldateien.
- **App-Repository-Layout** gemäß aktueller HA-Doku (`repository.yaml`, App-Ordner mit `config.yaml`, Dockerfile, `DOCS.md`, `CHANGELOG.md`, Icon/Logo, Übersetzungen der App-Optionen). Zwei Kanäle als zwei Apps: stable und beta.
- **CI (GitHub Actions):** Lint (ruff, mypy strict für core/planner/controller/safety, eslint/prettier), Tests (pytest mit Coverage-Ziel ≥ 80 % für core/planner/controller/safety, vitest), E2E (Playwright gegen den Demo-Modus), App-Linter, Multi-Arch-Image-Build mit Smoke-Test (Container startet, Health-Endpoint antwortet), CodeQL, Dependabot oder Renovate, Release-Automation (release-please, SemVer, CHANGELOG).
- **Dokumentation:** MkDocs Material oder Docusaurus (ADR) auf GitHub Pages, Englisch primär, Deutsch als Übersetzung. Inhalte: Schnellstart mit Screenshots aus dem Demo-Modus, Konzepte (Planer vs. Regler, Schattenmodus), Geräte-Handbücher, FAQ, Troubleshooting, Parameter-Referenz (generiert), Architektur, Contributor-Guide, „Gerätevorlage beisteuern“, „Übersetzen“.
- **i18n:** alle UI- und Backend-Strings über Schlüssel (Lint-Regel gegen Hardcodes), ICU-MessageFormat (Plural, Zahlen, Einheiten, lokalisierte Datumsformate), Fallback Englisch. Übersetzungsplattform Weblate (Hosted Weblate ist für Open-Source-Projekte kostenlos) mit Komponenten für UI, Backend, App-Optionen und Doku; Übersetzungsstand als README-Badge.
- **Community:** `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md` (Contributor Covenant), `SECURITY.md`, Issue-Vorlagen (Bug mit Diagnose-Export, Feature, Gerätevorlage), PR-Vorlage, GitHub Discussions, Labels „good first issue“, öffentliche Roadmap, Geräte-Kompatibilitätsliste (aus den Vorlagen generiert).
- **Erweiterbarkeit:** Geräte-, Tarif-, Prognose- und Batterie-Adapter sowie Regionsprofile als deklarative YAML-Vorlagen mit JSON-Schema-Validierung; optional Python-Plugins mit stabiler, versionierter Schnittstelle. Jede Vorlage wird in CI automatisch gegen den Simulator getestet.

---

## 8. Qualitätskriterien (Definition of Done je PR)

- Tests für neues Verhalten; Lint, Typprüfung und alle Tests grün.
- Safety-relevante Änderungen vom Agent `control-safety` geprüft, Ergebnis im PR dokumentiert.
- Keine hardcodierten UI-Strings; neue Schlüssel in Englisch und Deutsch.
- Doku und Parameter-Referenz aktualisiert; bei UI-Änderungen Screenshots aus dem Demo-Modus im PR.
- `CHANGELOG`, `docs/STATUS.md` und ggf. ADRs aktualisiert.
- PR-Beschreibung auf Deutsch: was, warum, wie getestet, Risiken, offene Fragen.

---

## 9. Referenzsystem des Maintainers

Dient als realistischer Testfall und Simulator-Szenario. **Nichts davon hart codieren** – alles muss über den Setup-Assistenten konfigurierbar sein.

- Wechselrichter Kostal Plenticore G3 L, 17 kW; in HA über SunSpec-Modbus angebunden (zusätzlich die Kostal-Web-Integration für einzelne Schalter). Der Wechselrichter hängt sich gelegentlich auf (Webserver, Display und Batterieverbindung weg) – das System muss das erkennen und sicher reagieren.
- PV 17 kWp, Ost-West-Dach, real maximal etwa 8–9 kW gleichzeitig.
- Batterie BYD HVM, 29,6 kWh. Entladegrenze im WR 10 %, das BMS sackt nachts trotzdem auf 6–7 %.
- Kostal KSEM G2 als Energiezähler, Kostal BackUp Switch G3 für Notstrom.
- Einspeisebegrenzung selbst auf 13.500 W gesetzt; Teileinspeisung mit gestaffelter EEG-Vergütung (10 kWp + Rest); Smart Meter ohne Steuerbox; Anlage seit Oktober 2026 in Betrieb (Neuanlage → Regeln für negative Preise relevant).
- Bezugstarif Tibber (dynamisch).
- PV-Prognose über Solcast.
- Split-Klimaanlagen über die LG-ThinQ-Integration: `climate`-Entity plus Energie heute/gestern in Wh, **kein Leistungssensor**.
- Geplant: Wallbox 11 kW (noch kein E-Auto, in 1–2 Jahren; bidirektionales Laden ist ein Wunsch).
- **Gewünschtes Verhalten:** Hausakku bis zum Abend so laden, dass die Nacht gedeckt ist → danach Überschuss in die Klimaanlagen (Heizen/Kühlen auf etwa 22–23 °C) → später ins E-Auto → erst dann einspeisen. Netzladung des Akkus automatisch nur nachts.

---

## 10. Phasenplan

Jede Phase kann aus mehreren PRs bestehen. Nach jeder Phase: `docs/STATUS.md` aktualisieren, ich teste und gebe frei.

| Phase | Inhalt | Ergebnis | Modell-Schwerpunkt |
|---|---|---|---|
| 0 | Recherche, ADRs, Architektur, Roadmap, Repo-Gerüst, CI, Agent-Team, CLAUDE.md | Freigegebener Bauplan | Fable |
| 1 | App-Gerüst mit Ingress, HA-Connector, Storage, Simulator-Basis, Live-Ansicht „Jetzt“, Schattenmodus-Grundgerüst, Image-Build | App in HA installierbar, zeigt Live-Daten | Sonnet + Fable-Advisor |
| 2 | Setup-Assistent, Gerätemodell, Vorlagen-System mit Schema, erste Vorlagen | Anlage per Assistent einrichtbar | Sonnet |
| 3 | Echtzeit-Regler, Safety-Schicht, Klima (beide Leistungsmodelle), Heizstab, schaltbare Lasten | Klima-Überschussregelung im Schattenmodus | Fable für Regler/Safety, Sonnet für Treiber |
| 4 | Prognosen (Solcast u. a.), Tarife (Tibber u. a.), Optimierer, Batteriestrategien, Batterie-Adapter, Erklärbarkeit | Fahrplan mit Akku-Strategie und „Warum?“ | Fable für Optimierer, Sonnet für Adapter |
| 5 | Plan- und Analyse-Diagramme, Kosten/Ersparnis, Entscheidungslog, Expertenmodus, What-if, Backtesting | Vollständige Visualisierung | Sonnet |
| 6 | HA-Entities/Aktionen/Ereignisse, Benachrichtigungen, Wallbox/E-Auto, Wärmepumpe/SG-Ready, Peak Shaving, §14a, Notstrom | Funktionsumfang komplett | Sonnet, Review mit Fable |
| 7 | Doku-Site, Weblate, Community-Dateien, Lovelace-Karten, optional Standalone-Container, Release 1.0 | Öffentliches Release | Sonnet/Haiku, Abschluss-Review mit Fable |

---

## 11. Agent-Team, Skills und Hooks

Lege in Phase 0 die folgenden Subagents unter `.claude/agents/` an. Die `description` jeweils kurz (1–2 Sätze, wann delegieren), Details in den System-Prompt. Jeder Agent liest vor der Arbeit `docs/STATUS.md` und die relevanten ADRs.

| Name | `model` | Aufgabe | Werkzeuge |
|---|---|---|---|
| `architect` | `fable` | ADRs, Schnittstellen, Modulgrenzen, Design-Reviews | nur lesend + Docs schreiben |
| `optimizer-engineer` | `fable` | LP/MILP-Modell, Prognose-Mathematik, Batterie-Ökonomie, Planer-Tests | voll |
| `control-safety` | `fable` | Echtzeit-Regler, Safety-Schicht, Fail-safe; prüft jede Änderung, die Geräte schaltet | voll |
| `backend-engineer` | `sonnet` | FastAPI, HA-Connector, Storage, Adapter-Infrastruktur | voll |
| `ha-integration` | `sonnet` | App-Paketierung, Supervisor/Ingress, MQTT Discovery, Companion-Integration, HACS | voll |
| `frontend-engineer` | `sonnet` | UI, Setup-Assistent, Diagramme, Barrierefreiheit, Responsiveness | voll |
| `device-adapters` | `sonnet` | Gerätevorlagen und Treiber (Klima, Wallbox, WP, Heizstab, Wechselrichter) | voll |
| `qa-engineer` | `sonnet` | Testsuiten, Simulator-Szenarien, E2E, Property-based-Tests | voll |
| `docs-writer` | `sonnet` | Doku-Site, README, Contributor-Guides | Docs-Dateien |
| `i18n-sync` | `haiku` | Übersetzungsschlüssel extrahieren und abgleichen, fehlende Keys melden | Locale-Dateien |
| `reviewer` | `fable` | Abschluss-Review je PR: Korrektheit, Safety, Security, Tests, i18n | nur lesend |

Zusätzlich:
- **Skills** unter `.claude/skills/` für wiederkehrende Abläufe: neue Gerätevorlage anlegen, neuen Tarif-/Prognose-Adapter anlegen, HA-API verifizieren (Checkliste mit Doku-Quellen), Release vorbereiten, Übersetzungen synchronisieren, Doku-Screenshots aus dem Demo-Modus erzeugen.
- **Hooks** in `.claude/settings.json`: nach Edit/Write automatisch formatieren (ruff format, prettier); vor Commit schnelle Tests. Erlaubnisregeln für die üblichen Befehle (Tests, Lint, Build).
- **CLAUDE.md** kurz halten (unter ca. 150 Zeilen): Projektzweck in drei Sätzen, Verzeichnisstruktur, Befehle für Build/Test/Lint, Arbeitsregeln aus Abschnitt 0 in Kurzform, Verweise auf `docs/BRIEF.md`, `docs/STATUS.md`, `docs/adr/`. Kein Kopieren dieses Briefs.

---

## 12. Phase 0 – deine erste Aufgabe

Noch kein Feature-Code. Ziel ist ein freigegebener, belastbarer Bauplan.

1. **Recherche** (Ergebnisse knapp in `docs/research/`):
   - aktuelle HA-Entwicklerdoku zu Apps (früher Add-ons): `config.yaml`, Ingress, Supervisor-APIs, Services (MQTT), Watchdog, Backups, Multi-Arch-Builds und Builder-Action, App-Linter;
   - WebSocket-API, Energy-Prefs, Langzeitstatistiken, MQTT Discovery;
   - Konzepte und Grenzen von evcc, EMHASS und Predbat (Doku, nicht Code);
   - Solcast-HACS-Integration (Attributstruktur der Prognosen), Tibber-Integration (Preisabruf), LG ThinQ (Klima-Entities);
   - Kostal Plenticore G3: Möglichkeiten der externen Batteriesteuerung per Modbus (offizielle Doku).
2. **ADRs** mindestens zu: Backend-Stack, Frontend-Framework und Chart-Bibliothek, Optimierer/Solver, Datenhaltung, Entities in HA (MQTT vs. Companion-Integration), Vorlagen-/Plugin-System, i18n und Übersetzungsplattform, Doku-Tooling, Release- und Versionierungsstrategie.
3. **Architektur** in `docs/architecture.md` mit Diagrammen (Mermaid): Module, Datenfluss, Planer/Regler/Safety-Zusammenspiel, Gerätemodell.
4. **Roadmap** in `docs/roadmap.md` (Phasen aus Abschnitt 10 verfeinert, mit Meilensteinen und Abnahmekriterien).
5. **Repo-Gerüst:** Verzeichnisstruktur, `LICENSE` (Apache-2.0), `NOTICE`, README-Entwurf, Community-Dateien, Issue-/PR-Vorlagen, leere Pakete mit Lint/Test-Setup, CI-Workflows (Lint, Tests, App-Linter) – grün.
6. **Agent-Team, Skills, Hooks, CLAUDE.md** gemäß Abschnitt 11.
7. **`docs/STATUS.md`** angelegt.
8. **Pull Request** mit deutscher Zusammenfassung, Liste aller ADRs mit Empfehlung und einer Sektion „Offene Fragen an den Maintainer“ (z. B. Projektname-Varianten falls nötig, Framework-Wahl, Prioritäten). Dann stoppen und auf meine Freigabe warten.
