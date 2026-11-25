# Fehlersuche

Fang mit dem Symptom an. Die meisten Probleme sind eine fehlende oder falsche Quell-Entität, und
SunCore markiert solche Werte, statt zu raten.

## Verbindung und Panel

- **„Home Assistant nicht verbunden“.** SunCore erreicht Home Assistant nicht und verbindet sich
  von selbst mit wachsendem Abstand neu. Sieh in den Tab **Log** der App. Wenn Home Assistant
  gerade neu gestartet hat, verschwindet die Meldung, sobald es wieder da ist. Bleibt sie, starte
  die App neu.
- **„Offline, verbinde neu...“ im Panel.** Der Browser hat die Verbindung zu SunCore verloren und
  versucht es automatisch erneut.
- **„Zugriff verweigert. Öffne SunCore aus Home Assistant.“** Nutze den Eintrag in der Seitenleiste.
  Der direkte Zugriff auf den Port der App ist absichtlich gesperrt.
- **Die App wird als nicht gesund angezeigt.** Die App stellt intern `/health` bereit. Es meldet
  einen Fehler, wenn die Datenbank unbrauchbar ist, Home Assistant die Zugangsdaten der App
  ablehnt oder die Verbindung endgültig geschlossen wurde. Ein Neustart von Home Assistant allein
  zählt nicht.

## Werte und Einrichtung

- **Ein Wert ist als „Veraltet“ markiert.** Der Sensor hat sich länger nicht aktualisiert. Prüfe
  die Quell-Integration.
- **Ein Wert ist als „Nicht verfügbar“ oder „Ungültig“ markiert.** Die Entität ist `unavailable`
  oder `unknown`, nicht numerisch oder hat eine nicht unterstützte Einheit. Wähle eine andere
  Entität oder repariere die Integration.
- **Der Vorzeichen-Check meldet, dass die Bilanz nicht aufgeht.** Ein Sensor, eine Einheit oder
  ein Vorzeichen stimmt nicht. Der Systemcheck nennt den Teil; gehe zu diesem Schritt zurück und
  antworte mit **Nein, Vorzeichen umkehren**.
- **Ein Sensor wird wegen der Einheit abgelehnt.** Leistungssensoren müssen W oder kW melden,
  Energiezähler Wh oder kWh, Ladestände %. SunCore rät nie Einheiten.
- **Der Assistent findet keine Vorschläge.** Ist das Register von Home Assistant nicht verfügbar,
  fällt SunCore auf eine einfache Liste von Entitäten mit schlichteren Regeln zurück. Nutze das
  Suchfeld.
- **Ein Gerät ist nicht in der Galerie.** Siehe [Gerätevorlage beitragen](device-templates.md)
  oder nimm eine allgemeine Vorlage (Schalter, Klima, regelbare Last). Die
  [Kompatibilitätsliste](compatibility.md) zeigt, was es gibt.

## Plan und Steuerung

- **Kein Plan, ein regelbasierter Plan, ein veralteter Plan, ein Spotpreis ohne Formel.** Siehe
  den Abschnitt Fehlersuche in
  [Planung und Akkusteuerung](planning-and-battery.md#9-fehlersuche).
- **Ein Gerät tut nichts.** Prüfe zuerst die Modi: Ein Gerät handelt im niedrigeren von
  Gesamtmodus und eigenem Modus, und im Schattenmodus protokolliert es nur. Lies dann das
  **Entscheidungsprotokoll**: Jede Entscheidung, auch „nichts zu tun“, hat einen Grund. Siehe
  [Überschusssteuerung](surplus-control.md).
- **Etwas läuft schief, und alles soll anhalten.** Drücke **Not-Aus** in der Kopfzeile. Die
  Geräte werden innerhalb von 10 Sekunden zurückgegeben, und es wird nichts geschrieben, bis du
  den Stopp mit **Zurück zum Normalbetrieb** beendest.
- **In Home Assistant erscheinen keine Entitäten.** Sie brauchen die App Mosquitto broker. Siehe
  [Home-Assistant-Entitäten](home-assistant-entities.md).

## Logs und was du einer Meldung beilegst

1. Öffne den Tab **Log** der App. Setze die Option `log_level` auf `debug` für mehr Details und
   starte die App neu. Tokens werden geschwärzt.
2. Nutze in den **Einstellungen** **Konfiguration exportieren**, um eine Kopie deiner Einrichtung
   zu behalten, bevor du etwas Größeres ausprobierst.
3. Ein Diagnose-Export (Konfiguration mit maskierten Geheimnissen und durch Pseudonyme ersetzten
   Entitäts-IDs, die letzten Entscheidungen, Verbindungs- und Planerzustand) ist für Phase 7
   geplant (Arbeitspaket 7-I). Ob er in deiner Version enthalten ist, ist `UNVERIFIED`: Suche
   **Diagnose** in den **Einstellungen**. Ist er nicht da, hänge das Log an und beschreibe deine
   Einrichtung in Worten.
4. Eröffne ein Issue unter <https://github.com/pxljake/suncore/issues>. Poste keine Tokens,
   Passwörter oder echten Entitäts-IDs, die privat bleiben sollen. Für Sicherheitsprobleme nutze
   den privaten Weg in [Sicherheit](security.md).

## Immer noch nicht weiter

Die [FAQ](faq.md) beantwortet die häufigen Fragen. Die App-Beschreibung auf GitHub hat einen
[Abschnitt zur Fehlersuche](https://github.com/pxljake/suncore/blob/main/addon/suncore/DOCS.md),
der mit der App-Version mitläuft (englisch).
