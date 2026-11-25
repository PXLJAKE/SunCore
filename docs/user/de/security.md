# Sicherheit

SunCore steuert echte elektrische Geräte, deshalb werden Sicherheitsmeldungen ernst genommen. Der
maßgebliche Text ist die
[Sicherheitsrichtlinie](https://github.com/pxljake/suncore/blob/main/SECURITY.md) im Repository
(englisch).

## Eine Schwachstelle melden

Eröffne bitte **kein** öffentliches Issue. Nutze die private Schwachstellenmeldung von GitHub im
Repository (Reiter „Security“, „Report a vulnerability“) oder schreibe an die E-Mail-Adresse in der
Sicherheitsrichtlinie. Du bekommst innerhalb von 7 Tagen eine Bestätigung und bei bestätigten
Problemen innerhalb von 30 Tagen eine Korrektur oder einen Plan zur Abmilderung.

Gib die Version, die Installationsart, die Schritte zum Nachstellen und, falls es hilft, den
anonymisierten Diagnose-Export an (zu seinem Stand siehe [Fehlersuche](troubleshooting.md)).

## Unterstützte Versionen

Bis 1.0 bekommen nur die neueste Beta und das neueste stabile Release Korrekturen.

## Was der Entwurf leistet

- Das Panel ist nur über den Ingress von Home Assistant erreichbar, für Administratoren. Es ist
  kein Port offen, es sei denn, du schaltest den API-Port mit einem Token ein. Direkter Zugriff
  wird verweigert („Zugriff verweigert. Öffne SunCore aus Home Assistant.“).
- Das Supervisor-Token wird nie protokolliert oder exportiert. In der Log-Ausgabe sind Tokens
  geschwärzt.
- Die Sicherheitsschicht ist der einzige Schreiber auf Geräte, und der Schattenmodus ist die
  Voreinstellung für jedes neue Gerät. Das Absenken eines Modus gilt sofort; einen Modus aus Home
  Assistant zu erhöhen, wird abgelehnt, solange du es nicht in den Einstellungen erlaubst.
- Die App braucht für MQTT keine `hassio_api`-Berechtigung: Sie fragt nur den MQTT-Dienst an.
  Den Konfigurationsordner von Home Assistant bindet sie nicht ein.
- Jede Anfrage, die etwas ändert, muss den Header `X-SunCore-Request: 1` tragen; das Panel
  sendet ihn, eine fremde Webseite kann es nicht. Nur das Auslösen des Not-Aus geht ohne ihn.
- SunCore ist lokal: kein Konto, keine Telemetrie, keine eigenen Cloud-Anfragen.
- Abhängigkeiten sind festgepinnt und werden von Dependabot aktualisiert, CodeQL läuft bei jedem
  Push, und Release-Images werden mit Cosign signiert.

## Wo deine Geheimnisse liegen

- Das Passwort eines eigenen MQTT-Brokers wird in der API, in Exporten, im Diagnose-Export,
  im Konfigurationsverlauf und im Log maskiert. Es liegt im Klartext in der Datenbank
  `suncore.db` der App, die Teil der App-Sicherung von Home Assistant ist (der
  Sicherheits-Review für 1.0 hat das so gelassen: ein Schlüssel in SunCore läge in derselben
  Sicherung). Deshalb:
  - nimm wenn möglich den automatischen Broker (die Mosquitto-App): SunCore holt die Zugangsdaten
    bei jeder Verbindung vom Supervisor und speichert keine;
  - gib einem eigenen Broker einen eigenen Benutzer für SunCore;
  - schalte die Verschlüsselung deiner Home-Assistant-Sicherungen ein. Eine Sicherung ist nur
    verschlüsselt, wenn ein Sicherungs-Passwort oder -Schlüssel gesetzt ist; ob die Einrichtung
    von Home Assistant das von selbst tut, ist `UNVERIFIED`.
- Wer sich am MQTT-Broker anmelden kann, kann SunCores Entitäten bedienen: SunCore stoppen, Geräte
  pausieren, Modi senken, ein Gerät boosten, neu planen. Mit der Mosquitto-App sind das alle
  Benutzer von Home Assistant und alle Apps mit dem MQTT-Dienst, also dieselben, die die Entitäten
  in Home Assistant drücken können. Das Hochstufen eines Modus bleibt aus, solange du es nicht
  erlaubst. Ist dir das zu weit, beschränke das Thema `suncore/#` mit der Zugriffsliste (ACL) deines
  Brokers (siehe die Dokumentation der Mosquitto-App).
- Poste Exporte, Logs oder Bildschirmfotos deiner eigenen Anlage nicht in einem öffentlichen
  Issue, ohne sie auf Tokens, Passwörter und Adressen zu prüfen.
