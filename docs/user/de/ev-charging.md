# E-Auto laden (Wallbox, evcc)

SunCore kann ein Elektroauto auf zwei Wegen laden:

- **Direkt** über die Home-Assistant-Entitäten deiner Wallbox (Vorlage `generic.wallbox`):
  SunCore setzt den Ladestrom, pausiert und setzt fort und kann zwischen ein und drei Phasen
  umschalten, wo die Wallbox das erlaubt.
- **Über evcc** (Vorlage `evcc.loadpoint`): SunCore sagt evcc, was es darf, und evcc regelt Strom
  und Phasen. Siehe [Abschnitt 5](#5-evcc-als-akteur).

Beide folgen derselben Regel: **Das Auto wird aus Überschuss geladen, es sei denn, du oder der
Plan erlauben Netzstrom.** Wie jedes Gerät startet ein Lader im **Schattenmodus** (nichts wird
geschrieben) und läuft höchstens **assistiert**, bis du sein Verhalten bestätigt hast.

## 1. Einrichten

Wähle im Einrichtungsassistenten, Schritt *Verbraucher*, bei der Wallbox-Vorlage **Wählen** und
ordne zu:

| Rolle | Entität | Nötig |
|---|---|---|
| Ladestrom | eine `number` in A | ja |
| Pause/Fortsetzen | ein `switch` desselben Geräts | ja |
| Phasen | ein `select` (1 oder 3 Phasen) | nur für die Phasenumschaltung |
| Ladeleistung | ein Leistungssensor | optional (bessere Leistungsschätzung) |
| Fahrzeug-Ladestand | ein Batteriesensor in % | optional (Ziele nach Ladestand) |
| Angesteckt | ein Binärsensor `plug` | optional (ein bekannt abgestecktes Auto wird nicht geladen) |

Trage dann den **echten** Maximalstrom von Wallbox und Stromkreis ein. Die 16 A der Vorlage sind
ein Platzhalter. Das Minimum ist 6 A. Setze **Ich habe das Verhalten dieses Geräts geprüft und
bestätige es**, wenn du es im Schattenmodus beobachtet hast. Ohne das geht der Lader nie über
assistiert hinaus, was auch immer du einstellst. Für das Fahrzeug trägst du die Batteriekapazität
ein (für einen Ladestand nötig) und den Ladewirkungsgrad (Standard 90 %, die Verluste zwischen
Steckdose und Batterie).

Noch keine Integrations-Vorlage ist am Quellcode geprüft; es gibt nur die generische (siehe
Grenzen). Sieh in die [Kompatibilitätsliste](compatibility.md).

## 2. Lademodi

Wähle den Modus auf der Karte des Laders in der Ansicht **Jetzt** (Standard **PV**).

| Modus | Was SunCore tut |
|---|---|
| **Aus** | Kein Laden. |
| **Sofort** | Sofort der Maximalstrom. Darf Netzstrom beziehen, innerhalb deiner Anschlussgrenze. |
| **Min + PV** | Mindestens der kleinste Strom (6 A, darf aus dem Netz kommen), der Rest aus Überschuss. |
| **PV** | Nur Überschuss. Der Standard. |
| **Ziel** | Ein Ladestand (oder eine Energiemenge) bis zu einer Uhrzeit. Der Planer wählt die günstigsten Fenster und gibt Netzleistung nur dort frei, wo der Plan es erlaubt, der Rest kommt aus Überschuss. |

Netzstrom für das Auto ist eine begrenzte Ausnahme: **Sofort**, **Min + PV** und **Ziel** dürfen
nur bis zur genannten Menge und nie über Bezugsgrenze, §14a-Budget oder Inselregeln hinaus
beziehen (siehe [Netzgrenzen](grid-limits.md)). Der Defizit-Rückzug geht nie unter die 6-A-Schwelle,
der Abwurf über der Anschlussgrenze aber schon.

Weitere Regeln:

- **6-A-Schwelle und Hysterese.** Unter 6 A pausiert SunCore (0 A). Die Stromstufen folgen dem
  Überschuss mit der üblichen Hysterese sowie Mindestlauf- und Mindestpausenzeit (standardmäßig
  300 s).
- **Umschaltung 1/3 Phasen** ist optional und braucht eine Phasen-Entität und einen dreiphasigen
  Lader. SunCore pausiert zuerst, schaltet erst nach 120 s Pause um, setzt nach weiteren 120 s
  fort, höchstens viermal pro Stunde und nie in **Sofort**. Auf eine Phase geht es, wenn der
  nutzbare Überschuss 5 Minuten unter dem Dreiphasen-Minimum bleibt, zurück auf drei Phasen mit
  500 W mehr für 5 Minuten.
- **Ziele** nutzen den Ladestand des Fahrzeugs (wenn zugeordnet) oder Energie. Der Wirkungsgrad
  berücksichtigt die Verluste. Mit einem Ziel zeigt die Karte „Ziel: 80 % bis 07:30“.
- **Abgesteckt oder unbekannte Eingänge:** Ein abgestecktes Auto wird nicht geladen, und bei
  unbekannten Netz- oder Überschusswerten wird der Strom nicht erhöht und fällt auf das Minimum.
- **Der Hausakku entlädt nie ins Auto.** Laden aus dem Netz (**Sofort**, Schwelle,
  **Ziel**-Fenster) beginnt nur, solange es keinen Hausakku gibt oder SunCore eine bestätigte
  Entladesperre auf ihn hält. Darf SunCore den Akku nicht steuern (Schattenmodus), kann es diese
  Sperre nicht setzen, und das Auto lädt nur aus Überschuss, auch in **Sofort** oder
  **Min + PV**. Lädt das Auto aus dem Netz, versorgt das Netz auch das Haus, nicht der Akku.

## 3. Sicherheitsverhalten

- **Schatten:** Nichts wird geschrieben; das Entscheidungsprotokoll zeigt, was SunCore gesetzt
  hätte.
- **Assistiert:** SunCore schlägt einen Strom oder eine Pause vor, du bestätigst. Jeder
  Schreibvorgang wird erneut geprüft.
- **Zurückgeben:** Ein Not-Aus, der Schattenmodus, ein Watchdog-Auslöser, eine gestoppte App oder
  eine verlorene Verbindung stellen wieder her, was die Wallbox vorher hatte: Strom, Phasen und
  Freigabe-Schalter. **Zurück zu normal** nach einem Not-Aus startet nichts von selbst.
- Eine Freigabe, die warten muss (Phasenpause, laufende §14a-Begrenzung), lässt den Lader
  pausiert, was der sichere Zustand ist, und stellt ihn danach wieder her. Beendet sich SunCore,
  während eine gestufte Freigabe wartet, bleibt der Lader pausiert, bis SunCore wieder läuft.
- Eine Änderung, die du an der Wallbox oder in ihrer App machst, wird erkannt und respektiert
  (Halten bis 04:00 Uhr, siehe
  [Überschusssteuerung](surplus-control.md#4-wenn-du-selbst-etwas-änderst)).

## 4. Bekannte Grenzen (Wallbox)

- Nur im Simulator: Es stand keine Wallbox zur Verfügung. `UNVERIFIED`: dass ein Lader eine
  Phasenänderung im pausierten Zustand annimmt und wie lange er braucht, Standardwerte für
  Cloud-abgefragte Lader (die Integration `wallbox` fragt alle 90 s ab; gib so einem Lader eine
  längere Beruhigungs- und Laufzeit).
- Nur eine generische Vorlage; für eine Integrations-Vorlage muss der Quellcode dieser Integration
  gelesen werden.
- Ein Fahrzeug pro Lader, kein Vehicle-to-Home. Noch keine E-Auto-Entitäten in Home Assistant
  (siehe [Home-Assistant-Entitäten](home-assistant-entities.md)); Modus und Ziel stellst du im
  Panel ein.
- Ein Ziel bei später Ankunft kann knapp verfehlt werden (der Simulator hört bei etwa 77 % eines
  80-%-Ziels auf, Netzleistung freizugeben). Eine harte Frist zu jedem Preis ist eine offene
  Frage.
- Ohne Akkusperre kann ein **Ziel**-Fenster nicht aus dem Netz laden, solange ein Hausakku
  existiert und im Schattenmodus ist.

## 5. evcc als Akteur

Mit der Vorlage `evcc.loadpoint` steuert SunCore die Wallbox nicht selbst. Es sagt evcc über
evccs MQTT-Schnittstelle, was es darf, und evcc regelt Strom und Phasen.

- **Was geschrieben wird** (und sonst nichts): der Ladepunkt-Modus (`off`, `smart`, `now`), der
  Maximalstrom und das Ladelimit. SunCore berührt nie evccs Batterieeinstellungen, schreibt nie
  einen Fahrzeugplan, nie `minCurrent` oder die Phasenkonfiguration.
- **Zuordnung:** **Aus** wird `off`; **PV** wird `smart`; **Min + PV** wird ebenfalls `smart`,
  weil das heutige evcc keinen Modus mit Mindestleistung mehr hat, also **wird keine
  6-A-Schwelle erzwungen**; **Sofort** oder ein geplantes **Ziel**-Fenster wird `now` mit einem
  Maximalstrom, der zu deiner Freigabe, der Bezugsgrenze und dem §14a-Budget passt, und nur,
  solange kein Hausakku ins Auto entladen kann.
- **Einrichtung:** Es braucht MQTT (den Broker des Abschnitts `mqtt`, evcc und SunCore müssen
  also denselben Broker nutzen). Die Ladepunkt-ID ist standardmäßig 1, das Topic-Präfix `evcc`.
  Der Assistent hat dafür noch kein Feld; setze `loadpoint_id` und `topic_prefix` in der
  Gerätekonfiguration, wenn deine abweichen. Lass evccs eigene Batterieregeln (Puffer-SoC und
  Ähnliches) neutral.
- **Freigabe:** Ein Not-Aus, der Schattenmodus, ein Auslöser oder eine gestoppte App stellen
  evccs Modus, Maximalstrom und Limit wie bei der Übernahme wieder her, und SunCore wartet auf
  evccs Echo, bevor es als erledigt gilt. Eine fehlgeschlagene Freigabe wird mit wachsendem
  Abstand wiederholt (30 s bis 10 min).
- **Deine eigene Änderung in evcc** (seine App, seine Oberfläche) wird wie jede manuelle Änderung
  respektiert. evcc setzt den Modus beim Abstecken zurück; SunCore kann das als Übernahme lesen
  und bis 04:00 Uhr des nächsten Tages warten (`UNVERIFIED`, mit einem echten evcc zu messen).

### Restrisiken und Vorbehalte beim evcc-Weg

- **Der Modus `smart` reagiert auf die Bezugsgrenze nur nachträglich.** evcc kann nach eigenen
  Regeln beziehen (ein Fahrzeugplan, ein Smart-Cost-Limit, „immer laden“). SunCore sieht nur den
  gemessenen Bezug und senkt dann evccs Maximalstrom oder schaltet ihn ab: Eine Überschreitung
  von bis zu etwa **einer Minute** (60 bis 90 s bei verrauschtem Bezug) ist möglich. Die
  Sicherheitsschicht kann das nicht verhindern, weil sie nur sieht, was evcc tun *darf*. Deine
  Sicherung und die Grenze deines Netzbetreibers bleiben dein eigener Schutz. Gegen Flattern
  schaltet SunCore erst nach einer Minute über der Grenze ab und erst nach fünf Minuten mit etwas
  Luft wieder ein; über der Sicherungsgrenze schaltet es sofort ab.
- **Keine Akkusperre bei `smart`.** evccs eigene Batterieregeln können den Hausakku ins Auto
  entladen. Nur `now` und geplantes Laden aus dem Netz warten auf SunCores Sperre.
- **Die Lebenszeichen-Erkennung hängt an evccs Veröffentlichungsintervall.** SunCore hält evcc
  für weg, wenn `<prefix>/updated` älter als 120 s ist, und schreibt dann nichts. Ein sehr ruhiges
  evcc, das seltener veröffentlicht, kann das fälschlich auslösen. Eine vorgehende Uhr in evcc
  lässt einen alten gespeicherten Zeitstempel frisch aussehen. Nach einem Neuverbinden zum Broker
  lässt ein veralteter gespeicherter Wert ein totes evcc nie lebendig aussehen.
- Eine Freigabe, während evcc schweigt, wird abgelehnt und wiederholt, bis evcc die Ausgangswerte
  zeigt.
- `UNVERIFIED`: alles lief nur gegen ein simuliertes evcc. Ladepunkt-IDs ab 1, die gespeicherten
  Lese-Topics, der Heartbeat und die Setter-Nutzlasten wurden aus evccs Quellcode gelesen; das
  Löschen eines Plans über MQTT, wie schnell evcc einen neuen Maximalstrom umsetzt und das
  Zurücksetzen des Modus beim Abstecken wurden nicht geprüft.

Siehe auch: [Überschusssteuerung](surplus-control.md), [Netzgrenzen](grid-limits.md),
[Planung und Akkusteuerung](planning-and-battery.md).
