# Home-Assistant-Entitäten und Befehle

SunCore zeigt seinen Zustand in Home Assistant als Geräte und Entitäten und nimmt einige wenige
Befehle von Home Assistant an. Das läuft über MQTT-Discovery und braucht deshalb einen
MQTT-Broker, normalerweise die App **Mosquitto broker**. Ohne Broker funktioniert alles andere in
SunCore wie bisher; es fehlen nur diese Entitäten.

Diese Seite behandelt die Einrichtung, was du bekommst, was Home Assistant darf und was nicht,
und die bekannten Grenzen. Sicherheit zuerst: **Die Entitäten-Schnittstelle schreibt nie in ein
Gerät.** Ein Befehl aus Home Assistant läuft durch dieselben Dienste und dieselbe
Sicherheitsschicht wie ein Klick im Panel.

## 1. Einrichten

1. Installiere die App **Mosquitto broker** (**Einstellungen → Apps → App-Store**), starte sie
   und lass Home Assistant die MQTT-Integration hinzufügen.
2. Öffne SunCore, **Einstellungen → Home-Assistant-Entitäten**. Die Statuszeile zeigt
   **Verbunden** und die Anzahl der Entitäten. SunCore fragt den Supervisor selbst nach dem
   Broker (die App braucht dafür keine zusätzliche Berechtigung) und sucht jede Minute neu, bis
   einer da ist.
3. In Home Assistant erscheint ein Gerät **SunCore** und je ein Gerät pro konfiguriertem Gerät.
   Auf der Home-Assistant-Seite ist nichts einzustellen.

Weitere Zustände: **Aus** (in den Einstellungen abgeschaltet), **Kein Broker** (der Supervisor
kennt keinen; die Seite sagt warum und was zu tun ist), **Verbindet**, **Fehler** (falscher
Benutzername oder falsches Passwort, TLS-Fehler, keine Antwort; die Seite nennt den Grund). Der
Systemcheck am Ende des Einrichtungsassistenten hat dafür ebenfalls eine Zeile.

Einen anderen Broker als die Mosquitto-App (oder einen außerhalb von Home Assistant OS) richtest
du mit `broker: manual` (Host, Port, TLS, Benutzername, Passwort) im Abschnitt `mqtt` der
Konfiguration ein. Das Panel hat dafür noch keine Felder (siehe Grenzen unten). Das Passwort wird
als Geheimnis gespeichert und in der API, in Exporten und im Konfigurationsverlauf maskiert. Die
Schaltfläche **Erneut veröffentlichen** meldet alle Entitäten noch einmal an.

Die Entitäten werden `unavailable`, wenn SunCore stoppt (der Broker veröffentlicht SunCores
Last Will), und werden beim Neustart von SunCore oder Home Assistant neu angemeldet.

## 2. Was du bekommst

Namen und Aufzählungszustände folgen deiner Sprache (Regions-Einstellung oder, bei „automatisch“,
die Sprache von Home Assistant). Automationen, die einen Aufzählungszustand wie „Automatisch“
prüfen, hängen deshalb an der Sprache. Nimm stattdessen die Binärsensoren und den Schalter.

| Entität | Art | Bedeutung |
|---|---|---|
| Status | Sensor | Startet, läuft, Not-Aus, Home Assistant nicht erreichbar |
| Betriebsmodus | Sensor | Schatten, assistiert oder automatisch (Gesamtmodus) |
| Verfügbarer Überschuss | Sensor | Der sichere Überschuss, den die Steuerung sieht, in W |
| Gesund, Schattenmodus, Not-Aus | Binärsensoren | Für Automationen und Dashboards |
| Nächste Aktion | Sensor | Die nächste Aktion des Plans in einer Zeile, etwa „14:30 Uhr: Speichere 2.400 W Solarüberschuss“, mit dem Tag für morgen und später; unbekannt, wenn der Plan nichts vorsieht |
| Geplante Kosten heute | Sensor | Was der Plan für den Rest des Tages an Netzkosten erwartet (Bezug minus Einspeiseerlös), in deiner Währung; unbekannt ohne Preise |
| Ersparnis heute | Sensor | Was die Anlage heute gegenüber einem Haus ohne PV gespart hat (die erste Spar-Kachel der Verlaufsansicht), in deiner Währung |
| Autarkie heute | Sensor | Anteil des heutigen Verbrauchs, der nicht aus dem Netz kam, in % |
| PV-Prognosefehler | Sensor | Mittlerer absoluter Fehler der PV-Prognose am neuesten Tag mit Messung, in W (keine Prozentzahl; gestern, nachts berechnet) |
| Monatsspitze | Sensor | Der höchste 15-Minuten-Bezug des Monats in W (wenn der Netzabschnitt ihn misst) |
| Netzzustand | Sensor | Normal, vom Netzbetreiber begrenzt (§14a), Inselbetrieb |
| Begrenzung des Netzbetreibers (§14a) aktiv, Inselbetrieb | Binärsensoren | Nur mit Signal oder Backup-Entität; unbekannt, solange diese nicht verfügbar ist |
| Ereignisse | Event | Gerät geschaltet, Vorschlag, Fehler, Beginn und Ende von Insel und §14a, Programm „jetzt starten“ und „fertig“, Beginn und Ende eines günstigen Fensters, Ladeziel in Gefahr |
| Automatisch | Schalter | Gesamtmodus: an = automatisch, aus = Schatten |
| Neu planen | Taste | Startet eine neue Planung |
| Not-Aus | Taste | Wie der Not-Aus im Panel |
| Not-Aus aufheben | Taste | Wird nur angemeldet, wenn du das Erhöhen erlaubt hast (unten) |

Pro konfiguriertem Gerät: **Modus**, **Leistung** und **Letzte Entscheidung** (Sensoren), ein
Schalter **Automatisch**, die Tasten **Boost**, **Pause** und **Fortsetzen** und eine Zahl
**Boost-Dauer** (Minuten, Standard 60). Das sind keine Entitäten für das Energie-Dashboard und
sie enthalten keine Energiezähler.

Reine Anzeigen, nur wo sie zutreffen:

- **Wallbox:** Lademodus, Ziel-Ladestand, Zielenergie und Zielzeit (wenn gesetzt), angesteckt,
  Ladephasen und der Ladestand des Fahrzeugs (wenn die Wallbox sie meldet).
- **Wärmepumpe (SG-Ready):** SG-Zustand (1 bis 4) und die Sollwert-Anhebung in K.
- **Programm:** Auftragszustand (bereit, scharf, läuft) und der geplante Start.
- **Batterie** (eigenes HA-Gerät, wenn es einen Batterie-Adapter gibt): Aktion (Ruhe, Laden,
  Halten) und wer die Steuerung hält. Sie zeigen, was SunCore hält, bleiben im Schattenmodus also
  bei „Ruhe“ und „Eigene Einstellungen“.

Das sind absichtlich Sensoren. EV-Modus und Ziel setzen, ein Programm vormerken und das
Komfortband aus Home Assistant gibt es noch nicht (siehe Grenzen).

Die fünf Standort-Sensoren lesen den Plan und den Verlauf; sie schreiben nie. Ein Wert ohne
Quelle (kein Plan, keine Preise, kein gemessener Tag) erscheint in Home Assistant als
unbekannt, nicht als null. Ersparnis, Autarkie und Prognosefehler werden höchstens alle fünf
Minuten aus dem Verlauf gelesen und hinken dem Panel entsprechend nach.

**Ereignisse.** Jedes Ereignis trägt seinen Typ und einige Attribute (etwa den Gerätenamen oder
die Zeiten des Fensters). Sie stammen aus denselben Beobachtungen wie die Benachrichtigungen,
sind aber von den Push-Regeln unabhängig: Ruhezeit, Tageslimit und eine abgeschaltete Kategorie
unterdrücken den Push, nicht das Ereignis. Eine Automation auf „Inselbetrieb beginnt“ läuft
also auch nachts. Ein Programm, das nicht rechtzeitig gestartet wurde oder dessen Ausgang
unbekannt ist, ist nur eine Benachrichtigung ohne Ereignis. Ereignisse werden nicht
nachgeholt: Eines, das länger als fünf Minuten auf den Broker wartete, entfällt.

## 3. Befehle und was abgelehnt wird

| Befehl | Ergebnis |
|---|---|
| Not-Aus | Immer ausgeführt, nie begrenzt, nie abgelehnt. |
| „Automatisch“ ausschalten (gesamt oder je Gerät) | Immer ausgeführt. Absenken geht in den Schatten. |
| Pause, Boost, Fortsetzen, Neu planen | Wie im Panel ausgeführt. Ein Gerät im Schatten wird trotzdem nicht beschrieben. |
| „Automatisch“ einschalten, Not-Aus aufheben | **Abgelehnt**, solange du es nicht erlaubst. |

Standardmäßig darf Home Assistant die Autonomie **nicht erhöhen**: Ein Gerät oder das ganze
System auf Automatik zu stellen oder den Not-Aus aufzuheben, wird abgelehnt, und der wahre
Zustand wird neu veröffentlicht. SunCore meldet das höchstens einmal pro Stunde und Entität in
einer Benachrichtigung. Zum Erlauben schaltest du unter **Einstellungen →
Home-Assistant-Entitäten** **Home Assistant darf einen Modus erhöhen oder den Notaus aufheben**
ein. Tu das nur, wenn du jedem vertraust, der auf deinen Broker veröffentlichen kann.

Schutzmaßnahmen, alle getestet:

- Nur genaue Nutzlasten werden angenommen: `ON`, `OFF`, `PRESS` und einfache Zahlen (höchstens
  64 Byte). Alles andere wird ignoriert.
- Ein „retained“ Befehl wird ignoriert und vom Broker entfernt.
- Höchstens ein Befehl pro Sekunde und Entität (Burst drei) und 20 Befehle je 10 Sekunden
  insgesamt. Eine Flut kann die Steuerung nicht aufhalten. Der Not-Aus ist ausgenommen.
- Entitäten, die SunCore nicht angemeldet hat, werden ignoriert.
- Jeder Befehl hinterlässt eine Zeile im App-Log mit seinem Ergebnis.

## 4. Bekannte Grenzen und UNVERIFIED

- **Noch nicht angemeldet:** die Ersparnis des Monats, Batteriestrategie, -reserve und
  Abendziel sowie jede **Steuerung** für E-Auto, Programme und Komfort (EV-Modus, Ziel,
  Vormerken, Abbrechen, Fertig bis, Komfortband). Die Steuerungen brauchen einen Befehlsweg,
  der etwas vormerkt oder setzt; das ist offene Arbeit (Plan Abschnitt 8, 6-O) und kommt mit
  der Begleit-Integration in Phase 7. Bis dahin nimmst du dafür das Panel. Der Ereignistyp
  „Vorschlag abgelaufen“ hat noch keinen Erzeuger. Konfigurationsänderungen aus Home Assistant
  (und damit ein Urheber `mqtt` im Konfigurationsverlauf) gibt es nicht.
- Einen manuellen Broker kannst du im Panel noch nicht eintragen; nimm den Abschnitt `mqtt`.
- Ein Erhöhen eines Geräts aus Home Assistant trägt die aktuelle Identitätsmarke des Geräts, der
  Schutz vor einem umgeordneten Gerät greift dort also nicht. Nur die Freigabe schützt.
- Wer Zugangsdaten für den Broker hat, kann SunCore stoppen, Geräte pausieren und Modi
  absenken. Das ist Absicht: Stoppen ist immer sicher.
- Ersparnis, Autarkie und Kosten sind dieselben Zahlen wie in der Verlaufsansicht; sie sind nur
  so gut wie die Preise und die gemessenen Flüsse dahinter (der Schattenmodus spart nichts von
  selbst: Die Zahlen zeigen dann, was die Anlage tut, nicht was SunCore geändert hat).
- Am 2026-11-25 gegen den Quelltext von Home Assistant Core geprüft: die Geräteklassen `plug`,
  `battery`, `timestamp`, `power` und `monetary`, die Einheit K ohne Geräteklasse und das
  Zeitstempel-Format (ISO 8601 mit Zeitzone). An einem laufenden Home Assistant noch nicht gesehen.
- `UNVERIFIED` an einem echten Home Assistant: die Standard-Nutzlasten der MQTT-Plattformen
  Schalter, Taste und Zahl, dass Home Assistant Befehle ohne Retain sendet, der Schlüssel
  `language` für die Namen, die Antwort des Supervisors bei der Broker-Suche, TLS zur
  Mosquitto-App.
- Der RAM- und CPU-Bedarf mit laufendem MQTT ist auf einem Raspberry Pi nicht gemessen
  (`UNVERIFIED`, Ziel unter 300 MB).

Siehe auch: [Benachrichtigungen](notifications.md), [Überschusssteuerung](surplus-control.md).
