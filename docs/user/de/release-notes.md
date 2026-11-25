# Versionshinweise und Update

## Versionshinweise

Die vollständige Liste der Änderungen je Version ist das
[Changelog](https://github.com/pxljake/suncore/blob/main/CHANGELOG.md) (englisch). Es wird bei
einem Release aus den Commit-Nachrichten erzeugt. Die
[Release-Seite](https://github.com/pxljake/suncore/releases) hat die Hinweise jedes Releases und
bei stabilen Releases diese Dokumentation als ZIP (`suncore-docs-<Version>.zip`) für ältere
Versionen.

## SunCore 1.0 (Release-Kandidat)

1.0 ist noch nicht veröffentlicht. Die neueste getaggte Version ist 0.2.0. Bis 1.0 erscheint,
lässt dich der Schnellstart die Beta-App (**SunCore (beta)**) installieren. Falls der
Maintainer eine 1.0-Nummerierung für die Beta-App festlegt (Planfragen 15 und 25), heißen ihre
Release-Kandidaten künftig `1.0.0-beta.<n>`; bis dahin behält die Beta-App ihr bisheriges
Versionsschema.
Die stabile App springt von 0.2.0 direkt auf 1.0.0.

### Was seit 0.2.0 neu ist

- **Planung:** Prognosen für PV, Verbrauch und Preise, ein Plan über 24 bis 48 h für die Batterie
  und die flexiblen Verbraucher, die Ansicht **Plan** mit ihren Begründungen, der Verlauf und das
  Entscheidungsprotokoll, What-if und Backtesting, der Expertenmodus.
- **Geräte:** Wallbox und E-Auto (auch über evcc), Wärmepumpe (SG-Ready und Sollwert-Anhebung),
  verschiebbare Programme wie eine Waschmaschine, Batteriesteuerung.
- **Netz:** Bezugsgrenze und Spitzenkappung, Dimmen nach §14a EnWG, Ersatzstrom (Inselbetrieb),
  die Einspeisekette mit dem Wechselrichter-Limit und seinem
  [Verifikationstest](grid-limits.md#wechselrichter-limit-und-der-verifikationstest).
- **Home Assistant:** Entitäten und Ereignisse über MQTT, Benachrichtigungen mit Knöpfen.
- **Panel:** ein neues Design mit vier Bereichen (Jetzt, Plan, Analyse, Einstellungen), hell und
  dunkel, und ein Diagnose-Export für Fehlermeldungen (**Einstellungen → Erweitert → Diagnose**).
- **Dokumentation** als Website auf Englisch und Deutsch.

### Geändertes Verhalten: vor dem Update lesen

- **Wechselrichter-Limit: Führe nach dem Update den Verifikationstest erneut aus.** Ein Flag
  `limit_verified`, das von Hand oder durch einen Import gesetzt wurde (ohne die Kennung des
  Wechselrichter-Adapters, mit dem getestet wurde), gilt nicht mehr. Bis der Test bestanden ist,
  läuft das Limit höchstens **assistiert**. Auch der Import einer Konfiguration setzt das Flag
  zurück. Siehe
  [Wechselrichter-Limit und der Verifikationstest](grid-limits.md#wechselrichter-limit-und-der-verifikationstest).
- **Jeder schreibende API-Aufruf braucht den Header `X-SunCore-Request: 1`.** Das Panel sendet
  ihn. Ein Skript, ein `rest_command` oder ein Proxy, der über SunCores API etwas ändert, muss ihn
  ebenfalls senden, sonst kommt `403`. Lesen geht ohne Header. Die einzige Ausnahme ist das
  Auslösen des Not-Aus (`POST /api/v1/control/emergency-stop`), das immer geht; das Aufheben
  braucht den Header. Meldet das Panel „ein nötiger Header fehlt“, prüfe, ob ein Proxy vor Home
  Assistant eigene Header weiterleitet.
- **Kein Zugriff mehr auf den Konfigurationsordner von Home Assistant.** Die App bindet ihn nicht
  mehr ein (gelesen hat sie ihn nie). Für dich ändert sich nichts; SunCore sieht nur
  `secrets.yaml` nicht mehr.
- **Sehr große Anfragen werden abgelehnt.** Jede API-Route, die Daten annimmt, hat eine
  Größengrenze (`413`); das Panel kommt nie in ihre Nähe.
- **Kein Weg zurück auf 0.2.0 ohne Sicherung.** 1.0 migriert die Datenbank (`suncore.db` Schema 6,
  `timeseries.db` Schema 3). 0.2.0 startet darauf nicht und meldet „newer than the supported“.
  Mach vor dem Update eine Sicherung in Home Assistant; zurück geht es nur über diese Sicherung.
- **Die stabile App ist nicht mehr als experimentell markiert.**

### Nur im Simulator geprüft

Nichts davon lief in den Entwicklungssitzungen an echter Hardware. Alles startet im Modus
**Schatten**; nimm **Assistiert** vor **Automatisch** und beobachte das Entscheidungsprotokoll:

- Wallbox und E-Auto-Laden, evcc als Steller;
- Wärmepumpe (SG-Ready-Kontakte, Sollwert-Anhebung);
- verschiebbare Programme (Steckdose, Fernstart);
- das Wechselrichter-Limit und sein Verifikationstest;
- Lastabwurf, Spitzenkappung, Dimmen nach §14a und Ersatzstrom;
- die ersten echten Schreibzugriffe mit PV-Überschuss im Automatikmodus.

Die vollständige Liste dessen, was noch nicht bestätigt ist, steht im
[Status](https://github.com/pxljake/suncore/blob/main/docs/STATUS.md) unter `UNVERIFIED`
(englisch).

### Hinweise zur Sicherheit

- Das Passwort eines eigenen MQTT-Brokers liegt in SunCores Datenbank und damit in den
  Sicherungen von Home Assistant. Nimm wenn möglich den automatischen Broker (die
  Mosquitto-App; dann wird kein Passwort gespeichert), gib einem eigenen Broker einen eigenen
  Benutzer und schalte die Verschlüsselung deiner Home-Assistant-Sicherungen ein. Siehe
  [Sicherheit](security.md).
- Wer sich am MQTT-Broker anmelden kann, kann SunCore stoppen, Geräte pausieren und Modi senken.
  Das Hochstufen eines Modus aus Home Assistant bleibt aus, solange du es nicht erlaubst.

## Vor einem Update

1. **Mach eine Sicherung in Home Assistant**, die die SunCore-App enthält. Sie ist der einzige
   Weg zurück auf eine ältere Version.
2. **Exportiere deine Konfiguration.** Öffne die **Einstellungen** und nutze **Konfiguration
   exportieren**. Die Datei lässt sich in diese oder eine neuere Version wieder importieren.
3. **Wisse, was die Sicherung enthält.** Die Konfiguration gehört zur App-Sicherung. Die
   Messdatenbank `timeseries.db` ist davon ausgenommen; SunCore baut seinen Viertelstunden-Verlauf
   aus der Statistik von Home Assistant neu auf, wenn die Datei fehlt.
4. **Beachte, dass ein Datenbank-Update nur in eine Richtung geht.** Eine neuere Version migriert
   die Datenbank, und eine ältere Version kann eine Datenbank mit neuerem Schema nicht öffnen. Die
   App-Beschreibung nennt das Datenbankschema deiner Version.
5. **Lies die Hinweise** des Releases auf Änderungen bei Sicherheit oder Modus-Behandlung (für
   1.0 oben).

## Was ein Update mit deinen Modi macht

- Ein Gerät, dessen Entitäten oder Vorlage du änderst, fällt auf den Schattenmodus zurück.
  UNVERIFIED: Dass jedes Gerät seinen Modus über ein Versions-Update behält, wurde an keinem
  echten Update getestet.
- Der Import einer Konfiguration setzt den Gesamtmodus immer auf Schatten zurück.
- Ein aktiver Not-Aus übersteht das Update.

## Unterstützte Update-Wege

- **0.2.0 auf 1.0:** durch Tests abgedeckt. Eine eingefrorene 0.2.0-Datenbank und ein
  0.2.0-Export werden in der Testsuite migriert und importiert, und ein CI-Job startet das
  0.2.0-Image mit Daten, stoppt es, startet das neue Image auf denselben Daten und prüft das
  Ergebnis. UNVERIFIED: Dieser CI-Job lief noch nicht auf dem Release-Build.
- **Eine 1.0-Beta auf eine neuere Beta oder auf 1.0:** dieselben Migrationen; nicht eigens
  getestet.
- **Zurück auf eine ältere Version:** nicht unterstützt. Stell die Sicherung wieder her.

## Nach einem Update

- Öffne das Panel und prüfe, ob der Gesamtmodus so ist, wie du ihn erwartest.
- Führe den Systemcheck am Ende des Einrichtungs-Assistenten aus (**Einrichtung erneut
  durchlaufen** löscht deine Einstellungen nicht; **Frühere Versionen** stellt einen früheren
  Stand wieder her).
- Wenn du das Wechselrichter-Limit nutzt, führe seinen Verifikationstest erneut aus.
- Melde Probleme mit der Versionsnummer und dem Diagnose-Export, siehe
  [Fehlersuche](troubleshooting.md).
