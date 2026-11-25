# Netzgrenzen: Spitzenkappung, §14a, Inselbetrieb, Einspeisegrenze

SunCore beachtet die Grenzen deines Netzanschlusses. Diese Seite behandelt die Bezugsseite
(Sicherung, Spitzenkappung, Abwurf, §14a EnWG, Ersatzstrom) und die Einspeiseseite
(Einspeisegrenze, Nulleinspeisung, das Wechselrichter-Limit). Alles stellst du im
Einrichtungsassistenten ein (Schritt *Netzanschluss*, für den Wechselrichter *Batterie*) und
später unter **Einstellungen**.

Zwei Dinge vorweg:

- **Der Schattenmodus ist hier für alles der Standard.** Im Schattenmodus protokolliert SunCore
  nur, was es getan hätte („hätte abgeworfen“, „hätte gedimmt“), und schreibt nichts.
- SunCore **beachtet** das Signal des Netzbetreibers. Es ist **nicht** der zertifizierte
  Steuerweg nach §14a EnWG. Die Geräte deines Netzbetreibers bleiben zuständig.

## 1. Bezugsseite

### Anschlussgrenze und Spitzenkappung

Trage unter **Grenzen** die **Anschlussgrenze für Bezug** ein (zum Beispiel deine
Hauptsicherung in W). SunCore plant und erlaubt nie eine Laststeigerung, die den Netzbezug über
diese Grenze hebt.

Die **Spitzenkappung** ist standardmäßig aus (in Deutschland haben die meisten Haushalte keinen
Leistungspreis). Schalte **Monatliche Netzbezugsspitze niedrig halten** ein, wenn dein
Netzbetreiber den höchsten 15-Minuten-Bezug des Monats abrechnet:

- Die wirksame Bezugsgrenze ist der kleinere Wert aus Anschlussgrenze und **Spitzengrenze**: der
  höchste 15-Minuten-Mittelwert des Bezugs in diesem Monat, aber nie unter der
  **Spitzen-Untergrenze** (Standard 2500 W). Die Spitze wird am 1. des Monats um Mitternacht
  Ortszeit zurückgesetzt.
- Der **Leistungspreis** (pro kW und Monat) ist optional; er lässt den Planer eine neue Spitze
  gegen ihre Kosten abwägen. 0 = nur die Spitze niedrig halten.
- Die Monatsspitze steht als Badge in der Ansicht **Jetzt** und im **Verlauf** (Netzereignisse
  und Monatsspitze, 30 Tage).

### Abwurf-Reihenfolge

Liegt der Bezug über der Grenze, fährt SunCore Geräte in dieser Reihenfolge zurück: zuerst
stetige Lasten (Wallbox-Strom, regelbare Lasten), dann Geräte mit einem ausdrücklichen Abwurf-Rang
(niedriger zuerst), dann der Rest in umgekehrter Priorität. Ein abgeworfenes Gerät kommt nur bei
echter Einspeisung zurück, nie solange das Haus über der Grenze bezieht.

Über der **Anschlussgrenze** (der Sicherung, nicht der Spitzengrenze) schaltet SunCore
nicht-thermische Geräte zusätzlich vor Ablauf ihrer Mindestlaufzeit und trotz Schaltlimit ab.
Wärmepumpen und Klimageräte werden so nie abgeschaltet. Ein laufendes verschiebbares Programm
wird nie abgeschaltet (siehe [Programme](programs.md)). Verringern ist immer erlaubt; geprüft
wird nur das Erhöhen.

Die Reihenfolge stellst du je Gerät unter **Einstellungen → Geräte → Netzschutz je Gerät** im
Feld **Abschaltreihenfolge (Shed-Rang)** ein: eine ganze Zahl von 0 bis 10.000, die niedrigste
Zahl geht zuerst. Leer lassen gilt die umgekehrte Priorität. Ein falscher Wert (zum Beispiel über
10.000) wird mit einer Meldung am Feld abgelehnt, es wird nichts gespeichert.

### §14a EnWG (Deutschland)

Setze **Meine Anlage ist nach §14a steuerbar** und wähle das **Signal des Netzbetreibers**: einen
Binärsensor oder Schalter (**Kontakt**) oder einen Wert für die **erlaubte Leistung (W)**. Solange
das Signal aktiv ist, teilen sich die für §14a markierten Geräte die erlaubte Leistung (Standard
**4200 W**, der gesetzliche Richtwert) in Prioritätsreihenfolge. SunCore protokolliert, zeigt das
Badge **§14a-Begrenzung aktiv** und hält die markierten Geräte unter dem Budget.

- Ein Gerät nimmt nur teil, wenn es markiert ist: **Einstellungen → Geräte → Netzschutz je
  Gerät**, **Steuerbare Verbrauchseinrichtung nach §14a EnWG** aktivieren (Wallbox, Wärmepumpe
  oder Klimaanlage über 4,2 kW, die dein Netzbetreiber dimmen darf).
- **Unbekanntes Signal** (die Entität ist nicht verfügbar): markierte Geräte werden eingefroren,
  nichts wird erhöht. **Keine Signal-Entität** zählt als „nicht aktiv“, mit Hinweis.
- Nach dem Ende der Begrenzung gibt es eine Abklingzeit (120 s), bevor wieder etwas erhöht wird.
- Zahlensignal: 0 W bedeutet „nicht gedimmt“. Ein positiver Wert gilt als erlaubte Leistung,
  höchstens deine Grenze. `UNVERIFIED` an einer echten Steuerbox.
- Eine Wallbox, die SunCore während der Begrenzung freigibt, wird nur pausiert und bekommt ihren
  alten Zustand nach Begrenzung und Abklingzeit zurück (Prüfung jede Minute).
- Im Schattenmodus wird nur „hätte gedimmt“ protokolliert.

### Ersatzstrom (Inselbetrieb)

Setze **Meine Anlage kann im Inselbetrieb laufen** und wähle die **Entität für den
Ersatzstrom-Zustand** (zum Beispiel der Modus von Wechselrichter oder Backup-Schalter). Das Panel
zeigt ihren Live-Zustand. Trage die **Zustände für Inselbetrieb** genau so ein, wie die Entität
sie meldet. `unavailable` und `unknown` werden abgelehnt.

- Inselbetrieb wird **nur** an dieser Entität erkannt. Aus den Messwerten wird nichts geraten.
- Im Inselbetrieb wirft SunCore jedes Gerät ab, das nicht auf der Behalte-Liste steht, erhöht
  nichts und lädt den Akku nie aus dem Netz. Nach der Netzrückkehr gilt dieselbe Abklingzeit von
  120 s.
- Eine nicht verfügbare Entität zählt als **Netz verbunden**, das Panel zeigt „Netzzustand
  unbekannt“.
- Die Behalte-Liste steht unter **Einstellungen → Geräte → Geräte im Notstrombetrieb
  behalten**. Sie braucht die Entität für den Notstromzustand von oben; ohne sie weist die Seite
  darauf hin. Behalte nur, was bei einem Stromausfall wirklich Strom braucht: SunCore schaltet
  ein Gerät auf der Liste im Inselbetrieb nie ab, Batterie und Wechselrichter müssen es tragen
  können.
- `UNVERIFIED`: welche Entität und welche Zustände ein Kostal BackUp Switch G3 zeigt. Der
  Simulator nutzt `island` und `on_grid`.

## 2. Einspeiseseite

### Statische Grenze, Nulleinspeisung, negative Preise

Wähle unter **Einspeisegrenze** **In Watt**, **In Prozent der installierten Leistung** oder
**Keine Grenze**. Deine statische Grenze bleibt im Wechselrichter: SunCore plant mit ihr, schreibt
sie aber nicht (`enforce_static` ist aus). Unter **Kette der Einspeisebegrenzung**:

- **Nulleinspeisung**: Überschuss vor Ort nutzen und nie absichtlich einspeisen.
- **0 W bei negativen Einspeisepreisen**: senkt das Wechselrichter-Limit, solange der
  Einspeisepreis negativ ist. Wirkt nur mit einem **geprüften** Wechselrichter-Adapter.

### Die Kette: Geräte, Batterie, Wechselrichter

SunCore nutzt den Überschuss in dieser Reihenfolge: zuerst die gesteuerten Geräte, dann lädt der
Hybrid-Wechselrichter die Batterie von selbst (SunCore hebt nur sein eigenes Ladelimit an, solange
du einspeisen würdest), und erst danach senkt es das Limit des Wechselrichters. Ein engeres Limit
wird erst geschrieben, nachdem die Einspeisung etwa zwei Minuten über der Grenze lag und nichts
mehr aufnehmen konnte; ein weiteres sofort. Wird kein Limit mehr gebraucht, ist ein Eingang
veraltet oder das Netz weg, kommt dein eigenes Limit zurück.

### Wechselrichter-Limit und der Verifikationstest

Ein Wechselrichter-Adapter schreibt das Limit. Vorlagen: `kostal.plenticore_power_limit` (REST
`Inverter:ActivePowerLimitation`, ein Einspeiselimit in W), `kostal.plenticore_g3_modbus_limit`
(Register 533, ein AC-Ausgangslimit in %) und `generic.inverter_limit` (eine Number-Entität).

- Der Wechselrichter startet im Schatten und bleibt **höchstens assistiert, bis das Limit
  verifiziert ist.** Unverifiziert schreibt SunCore nie selbst ein Limit (automatischer Modus),
  es schlägt nur vor. Nur der Verifikationstest setzt das Flag; Speichern der Konfiguration oder
  ein Import können es nicht setzen (ein Import setzt es zurück). Das Flag gehört zu einem
  Adapter: Wechselst du Vorlage, Entität, Register oder Fähigkeit, ist das Limit wieder
  unverifiziert.
- **Den Test ausführen.** Öffne den Block in Einrichtungsschritt 5 oder unter Einstellungen,
  *Deine Anlage*, und wähle *Verifikationstest starten*. Stelle den Wechselrichter vorher auf
  **assistiert**. Der Test:
  1. wartet auf stetige Sonne: mindestens 1 kW Einspeisung und 1,5 kW über Hauslast plus
     Batterieladung, 2 Minuten lang in einem Band von 1 kW (höchstens 15 Minuten);
  2. zeigt das vorgeschlagene Testlimit, `max(Hauslast + Batterieladung + 700 W, Einspeisung −
     1 kW)`, neben deinem eigenen Limit. **Nichts wird geschrieben, bevor du bestätigst**
     (innerhalb von 2 Minuten);
  3. schreibt das Testlimit einmal, über die Sicherheitsschicht, als deine eigene Aktion, und
     beobachtet die Einspeisung etwa eine Minute: Ein Einspeiselimit zeigt sich als Einspeisung ≈
     Limit, ein AC-Ausgangslimit als Einspeisung ≈ Limit − Hauslast (− Batterieladung);
  4. stellt dein eigenes Limit wieder her, liest es zurück und prüft, dass die Einspeisung
     zurückkommt.
- Der Test ist bestanden, wenn das beobachtete Verhalten zur eingestellten Fähigkeit passt und
  dein Limit wieder steht. Dann ist das Limit verifiziert, und der automatische Modus wird
  möglich. Passt das Verhalten nicht, folgt die Einspeisung nicht, schlägt das Zurücklesen fehl
  oder kommt dein Limit nicht zurück, wird das Limit **unverifiziert**; der Grund steht im Block.
- Der Test endet sofort und stellt dein Limit wieder her, wenn du den Dialog schließt, den
  Notaus drückst, die Verbindung zu Home Assistant abreißt, SunCore stoppt oder neu startet, die
  Hauslast so weit steigt, dass das Limit Netzbezug verursachen würde, oder sein Zeitfenster
  (höchstens 10 Minuten) abläuft. Währenddessen schlägt SunCore kein anderes
  Wechselrichter-Limit vor.
- Der Test ist die einzige Stelle, an der SunCore das Limit unter die aktuelle Einspeisung senkt,
  obwohl Geräte oder die Batterie den Überschuss noch aufnehmen könnten. Diese Ausnahme gilt nur
  im Zeitfenster des Tests, nur für den einen Schreibvorgang, den du bestätigt hast, und nie
  gegen die anderen Regeln (kein Netzbezug, der Bereich des Adapters, höchstens alle paar
  Sekunden).
- Ein AC-Limit (Register 533) hält Hauslast plus Batterieladung plus 500 W frei, damit das Limit
  selbst nie Netzbezug verursacht.
- **Register 533 hat keinen dokumentierten Timeout.** Stoppt SunCore oder reißt die Verbindung ab,
  bleibt ein AC-Limit bis zum Neustart des Wechselrichters stehen, und steigt die Hauslast
  darüber, wird die Differenz aus dem Netz bezogen. Nimm lieber das Einspeiselimit (REST-Zahl)
  statt Register 533.
- SunCore schreibt ein gehaltenes Limit regelmäßig neu (Heartbeat). Das Register ist flüchtig und
  geht bei einem Reset des Wechselrichters verloren, was SunCore erkennt und neu schreibt. Ein
  Absturz stoppt den Heartbeat, und der Neustart gibt das Limit frei.
- `UNVERIFIED`: ob sich Register 533 zurücklesen lässt, ob es die Batterieentladung einschließt,
  was `Inverter:ActivePowerLimitation` begrenzt und ob es einen Neustart des Wechselrichters
  übersteht. Die Watchdog-Prüfung „PV eingefroren“ (PV konstant mindestens 200 W für 15 Minuten)
  kann auslösen, solange ein Limit die PV-Leistung exakt konstant hält; beobachte zuerst den
  Schattenlauf.

## 3. Zurückgeben

Ein Not-Aus, „Zurück zu normal“, eine gestoppte App, ein Watchdog-Auslöser oder eine verlorene
Verbindung geben das Wechselrichter-Limit und jedes abgeworfene Gerät zurück, wie in der
[Überschusssteuerung](surplus-control.md#5-zurückgeben-und-not-aus) beschrieben. Die Freigabe
eines Limits ist erst vollständig, wenn das Zurücklesen deinen eigenen Wert zeigt; bis dahin wird
mit wachsendem Abstand wiederholt.

## 4. Bekannte Grenzen

- Alles auf dieser Seite lief nur im Simulator (Sicherung, Leistungspreis, Steuerbox, Backup-
  Schalter und Wechselrichter-Limit standen nicht zur Verfügung). Dein Schattenlauf ist der erste
  echte Test.
- Noch keine Benachrichtigung für Insel oder §14a (die Badges und der Verlauf zeigen sie).
- Keine Schieflastgrenzen je Phase, kein EEBus oder LPC, keine Anlagen mit mehreren
  Wechselrichtern.
- Nicht in Phase 6: eingebaute Rückfälle für PV- und Preisdaten.

Siehe auch: [Überschusssteuerung](surplus-control.md),
[Planung und Akkusteuerung](planning-and-battery.md), [E-Auto laden](ev-charging.md).
