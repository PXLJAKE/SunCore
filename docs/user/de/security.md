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
- SunCore ist lokal: kein Konto, keine Telemetrie, keine eigenen Cloud-Anfragen.
- Abhängigkeiten sind festgepinnt und werden von Dependabot aktualisiert, CodeQL läuft bei jedem
  Push, und Release-Images werden mit Cosign signiert.

## Wo deine Geheimnisse liegen

- Das MQTT-Passwort wird als Geheimnis gespeichert und in der API, in Exporten und im
  Konfigurationsverlauf maskiert. UNVERIFIED: Es liegt im Klartext in der Datenbank
  `suncore.db` der App, die Teil der App-Sicherung von Home Assistant ist. Schütze deine
  Sicherungen (Home Assistant kann sie verschlüsseln). Eine Sicherheitsprüfung dieses Punkts ist
  für Phase 7 geplant.
- Poste Exporte, Logs oder Bildschirmfotos deiner eigenen Anlage nicht in einem öffentlichen
  Issue, ohne sie auf Tokens, Passwörter und Adressen zu prüfen.
