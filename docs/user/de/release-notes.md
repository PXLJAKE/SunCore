# Versionshinweise und Update

## Versionshinweise

Die vollständige Liste der Änderungen je Version ist das
[Changelog](https://github.com/pxljake/suncore/blob/main/CHANGELOG.md) (englisch). Es wird bei
einem Release aus den Commit-Nachrichten erzeugt. Die
[Release-Seite](https://github.com/pxljake/suncore/releases) hat die Hinweise jedes Releases.

SunCore ist **Pre-Alpha**. Die neueste getaggte Version ist 0.2.0. Solange es kein stabiles
Release gibt, lässt dich der Schnellstart die Beta-App (**SunCore (beta)**) installieren. Die
Nummerierung des Releases 1.0 und seiner Betas steht noch nicht fest: Diese Seite wird
aktualisiert, sobald sie feststeht.

## Vor einem Update

1. **Exportiere deine Konfiguration.** Öffne die **Einstellungen** und nutze **Konfiguration
   exportieren**. Die Datei ist dein Weg zurück.
2. **Behalte die App-Sicherung.** Die Konfiguration gehört zur App-Sicherung in Home Assistant. Die
   Messdatenbank `timeseries.db` ist davon ausgenommen; SunCore baut seinen Viertelstunden-Verlauf
   aus der Statistik von Home Assistant neu auf, wenn die Datei fehlt.
3. **Beachte, dass ein Datenbank-Update nur in eine Richtung geht.** Eine neuere Version migriert
   die Datenbank, und eine ältere Version kann eine Datenbank mit neuerem Schema nicht öffnen. Die
   App-Beschreibung nennt das Datenbankschema deiner Version (Schema 6 in der aktuellen Beta).
   Zurück geht es nur über die Wiederherstellung der Sicherung oder den Import des Exports.
4. **Lies die Hinweise** des Releases auf Änderungen bei Sicherheit oder Modus-Behandlung.

## Was ein Update mit deinen Modi macht

- Ein Gerät, dessen Entitäten oder Vorlage du änderst, fällt auf den Schattenmodus zurück.
  UNVERIFIED: Dass jedes Gerät seinen Modus über ein Versions-Update behält, wurde an keinem
  echten Update getestet.
- Der Import einer Konfiguration setzt den Gesamtmodus immer auf Schatten zurück.
- Ein aktiver Not-Aus übersteht das Update.

## Unterstützte Update-Wege

UNVERIFIED. Ein automatischer Test, der das 0.2.0-Image mit vorbereiteten Daten startet, stoppt und
das Kandidaten-Release auf denselben Daten startet, ist für Phase 7 geplant (Arbeitspaket 13), ist
in dieser Version aber noch nicht vorhanden. Bis dahin gilt jedes Update als ungetestet:
Exportiere zuerst und prüfe danach die **Einstellungen** und das **Entscheidungsprotokoll**. Dass
ein Export von 0.2.0 in eine neuere Version importiert werden kann, soll dasselbe Arbeitspaket
abdecken.

## Nach einem Update

- Öffne das Panel und prüfe, ob der Gesamtmodus so ist, wie du ihn erwartest.
- Führe den Systemcheck am Ende des Einrichtungs-Assistenten aus (**Einrichtung erneut
  durchlaufen** löscht deine Einstellungen nicht; **Frühere Versionen** stellt einen früheren
  Stand wieder her).
- Melde Probleme mit der Versionsnummer und dem Log, siehe [Fehlersuche](troubleshooting.md).
