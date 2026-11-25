# Benachrichtigungen

SunCore meldet dir Fehler, Sicherheitsereignisse, assistierte Vorschläge, fertige oder verpasste
Programme und, wenn du willst, günstigen Strom und eine Tageszusammenfassung. Die Nachrichten
sind in deiner Sprache, begrenzt und kommen nie als Flut. **Die Benachrichtigungsschicht schaltet
nie etwas.** Das Einzige, was eine Benachrichtigung auslösen kann, ist die Bestätigung eines
assistierten Vorschlags, genau wie die Schaltfläche **Bestätigen** im Panel (siehe
[Überschusssteuerung](surplus-control.md#2-betriebsmodi)).

## 1. Einrichten

Öffne **Einstellungen → Benachrichtigungen**.

1. **Dauerhafte Benachrichtigungen in Home Assistant** sind standardmäßig an. Fehler und
   Sicherheitsereignisse erscheinen in der Benachrichtigungsliste von Home Assistant ohne weitere
   Einrichtung und verschwinden, wenn die Ursache weg ist.
2. Für Push-Nachrichten fügst du ein **Push-Ziel** hinzu. Zwei Arten:
   - eine **notify-Entität** von Home Assistant, aus einer Liste gewählt, oder
   - ein **notify-Dienst** wie `mobile_app_handy` (tippe den Teil nach `notify.`). Nur die
     Dienste der Companion-App können die Schaltflächen **Bestätigen** und **Ablehnen** tragen.
   Bis zu acht Ziele. Ohne Ziel wird keine Push-Nachricht gesendet (dauerhafte weiterhin).
3. Wähle die **Kategorien** (Tabelle unten), die **Ruhezeiten** und das **Tageslimit** für Push.
4. **Testnachricht senden** prüft den ganzen Weg. Ein zweiter Test innerhalb weniger Sekunden
   wird abgelehnt.

## 2. Was gemeldet wird

| Kategorie | Standard | Beispiele | Ruhezeiten |
|---|---|---|---|
| Fehler | an, dauerhaft | ein vom Watchdog ausgelöstes Gerät, drei Schreibfehler in Folge, der Planer liefert seit 1 h keinen Plan, MQTT 15 min weg | nicht zurückgehalten |
| Sicherheit | an, dauerhaft | Not-Aus und sein Ende, ein abgelehntes Erhöhen aus Home Assistant | nicht zurückgehalten |
| Vorschläge | an | ein assistierter Vorschlag, wo möglich mit Schaltflächen | zurückgehalten |
| Programme | an | „Waschmaschine jetzt starten“, fertig, verpasst | nur „jetzt starten“ nicht zurückgehalten |
| Ladeziel | an | reserviert für „E-Auto-Ziel in Gefahr“ (siehe Grenzen) | zurückgehalten |
| Günstiges Fenster | aus | der günstigste Block von mindestens einer Stunde unter dem 25. Perzentil der nächsten 24 h | zurückgehalten |
| Tageszusammenfassung | aus | Modus und Zahl der Schaltvorgänge, um 20:00 Uhr | nicht zurückgehalten |

Standards für den Rest: Ruhezeit 22:00 bis 07:00 Uhr, höchstens **20 Push-Nachrichten pro Tag**
(0 schaltet Push aus). Dieselbe Nachricht kommt einmal pro Ursache (sie enthält den Vorschlag,
den Auftrag, das Fenster oder das Datum), Fehler höchstens alle 30 Minuten. Sicherheitsereignisse
warten nie auf die Ruhezeit und zählen nicht gegen das Tageslimit. Ein „jetzt starten“ eines
Programms wird nicht durch die Ruhezeit zurückgehalten, und „jetzt starten“ und „verpasst“
ignorieren das Tageslimit: Bei einem reinen Benachrichtigungs-Programm ist diese Nachricht der
einzige Weg, das Gerät zu starten.

Das MQTT-Entity **Ereignisse** ist davon getrennt: Es feuert für ein geschaltetes Gerät, einen
Vorschlag und einen Fehler, nicht für jede Benachrichtigung (siehe
[Home-Assistant-Entitäten](home-assistant-entities.md)).

## 3. Einen Vorschlag vom Handy bestätigen

Im assistierten Modus sagt ein Vorschlag, was SunCore tun würde und wie lange er gilt (2
Minuten). Bei einem Companion-App-Ziel hat die Benachrichtigung **Bestätigen** und **Ablehnen**.

- Jede Schaltfläche trägt eine **einmalige Zufallsmarke**, die nur im Speicher von SunCore
  existiert. Ein Neustart macht alle offenen Marken ungültig. Es sind höchstens 32 Vorschläge
  gleichzeitig offen.
- **Bestätigen** schickt den Vorschlag noch einmal durch die Sicherheitsschicht; er kann
  trotzdem abgelehnt werden (zum Beispiel, wenn der Modus des Geräts nicht mehr assistiert ist
  oder der Vorschlag abgelaufen ist).
- **Ablehnen** schließt den Vorschlag nur; es wird nichts aufgerufen.
- Ohne Companion-App-Dienst (ein Entitäts-Ziel) oder wenn Home Assistant SunCore keine
  Schaltflächen-Antworten empfangen lässt, hat die Nachricht keine Schaltflächen und verweist auf
  das Panel. **Einstellungen → Benachrichtigungen** zeigt **Schaltflächen funktionieren**,
  **Schaltflächen werden geprüft** oder **Schaltflächen abgelehnt**.

## 4. Bekannte Grenzen und UNVERIFIED

- **Insel- und §14a-Pushes sind noch nicht angeschlossen.** Texte und Regeln gibt es, aber die
  Anwendung füttert den Insel- und §14a-Zustand in dieser Version nicht in die
  Benachrichtigungs-Engine. Die Badges im Panel und der Verlauf zeigen sie. Dasselbe gilt für
  **E-Auto-Ziel in Gefahr**: Es gibt noch keinen Erzeuger. Alle drei folgen in Phase 7.
- Vorschläge, die in der Ruhezeit entstehen, werden verworfen und nicht am Morgen nachgereicht
  (sie verfallen ohnehin nach Minuten).
- Die Liste der `notify`-Dienste liest SunCore nicht aus Home Assistant; du tippst den Namen.
- Restrisiko: Wer Events auf dem Home-Assistant-Bus auslösen kann und zusätzlich die Marke einer
  Benachrichtigung liest (das Handy oder das Ereignisprotokoll von Home Assistant), kann diesen
  einen Vorschlag innerhalb seiner Lebensdauer bestätigen. Die Wirkung ist auf das beschränkt,
  was **Bestätigen** im Panel tut. Die Marke läuft durch den notify-Dienst von Home Assistant
  und den Push-Anbieter, wie bei jeder Benachrichtigung mit Aktionen.
- Eine Ablehnung des Abonnements für die Schaltflächen nach einem Neuverbinden wird nur
  bemerkt, wenn die Erneuerung fehlschlägt; die Schaltflächen wechseln dann zu einfachen
  Nachrichten.
- `UNVERIFIED` an einem echten Home Assistant: die Nutzlast des Events
  `mobile_app_notification_action` über `data.action` hinaus, ob das Supervisor-Token es
  abonnieren darf (sonst bleiben die Nachrichten einfach), `data.actions` bei jedem alten
  notify-Dienst.
- Im Schattenmodus bittet ein Programm mit Startart „Benachrichtigung“ dich trotzdem um den
  Start, weil SunCore nichts schaltet.

Siehe auch: [Programme](programs.md), [Home-Assistant-Entitäten](home-assistant-entities.md).
