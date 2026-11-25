# Verschiebbare Programme (Waschmaschine, Geschirrspüler, Trockner)

Ein verschiebbares Programm ist ein Auftrag, der einmal laufen muss und dem der Zeitpunkt egal
ist, solange er rechtzeitig fertig wird: eine Waschmaschine, ein Geschirrspüler, ein Trockner. Du
belädst das Gerät, wählst das Programm und drückst Start (oder merkst es vor) und sagst SunCore,
**bis wann es fertig sein muss**. SunCore wählt den Start: mit Überschuss, wenn die Prognose es
erlaubt, sonst im günstigsten Fenster, und in jedem Fall früh genug für deine Frist.

Eine kurze Beschreibung der Steckdose und des Sicherheitsnetzes steht auch in der
[Überschusssteuerung](surplus-control.md#verschiebbare-programme-waschmaschine-geschirrspüler).

## 1. Startarten

| Art (Vorlage) | Wie SunCore das Gerät startet |
|---|---|
| **Smarte Steckdose** (`generic.program_plug`) | Ein `switch` als Steckdose vor dem Gerät. SunCore schaltet die Steckdose direkt nach dem Vormerken **aus** (solange das Gerät noch fast nichts zieht) und zum geplanten Start **ein**. Das Gerät muss sein Programm fortsetzen, wenn der Strom zurückkehrt. |
| **Fernstart** (`generic.program_remote`) | Eine `switch`-Entität, die das Gerät startet. Sie wird einmal eingeschaltet und **nie ausgeschaltet**. Eine `button`-Entität wird noch nicht unterstützt. |
| **Benachrichtigung** (`generic.program_notify`) | SunCore schreibt nichts. Zum geplanten Start schickt es eine Nachricht „Waschmaschine jetzt starten“, und du startest das Gerät. |

## 2. Einrichten

Wähle im Einrichtungsassistenten, Schritt *Verbraucher*, die Vorlage und ordne die Entitäten zu
(den Schalter und einen **Leistungssensor** des Geräts; für einen Start ist ein frischer
Leistungswert nötig). Trage ein:

- die **Laufzeit** des Programms in Minuten (die 120 der Vorlage sind ein Platzhalter),
- die **Spitzenleistung** des Geräts (Platzhalter in der Vorlage) und, wenn bekannt, die Energie
  je 15 Minuten eines Laufs,
- die **Leerlaufleistung** in W (Standard 5): darunter gilt das Gerät als fertig oder im
  Leerlauf.

Bei der Art **Steckdose** musst du **pro Gerät** bestätigen, dass es sein Programm fortsetzt, wenn
der Strom zurückkehrt (das Kontrollkästchen **Das Gerät läuft nach Stromrückkehr von selbst an**).
Probiere es vorher von Hand: Programm starten, Stecker ziehen, wieder einstecken. Viele Geräte
setzen nicht fort, dann nimm **Benachrichtigung** oder **Fernstart**. Bestätige außerdem das
Verhalten („Ich habe das Verhalten dieses Geräts geprüft und bestätige es“), wenn der Schattenmodus
gut aussah; bis dahin läuft das Programm-Gerät höchstens assistiert.

## 3. Benutzen

Auf der Karte des Programms in der Ansicht **Jetzt**: Gerät beladen, **Fertig bis** setzen und
**Vormerken** drücken. Die Karte zeigt den Auftragszustand (vorgemerkt, geplant, läuft, fertig,
abgebrochen) und den geplanten Start. SunCore plant den Start, sobald ein frischer Plan da ist.
Gibt es keinen Plan, gilt der späteste Start, der die Frist noch hält. **Auftrag abbrechen**
vergisst einen Auftrag, der noch nicht gestartet ist; ein laufendes Programm kann SunCore nicht
abbrechen.

Verstreicht die Frist ohne Start, gilt der Auftrag als **verpasst**, und du bekommst eine
Benachrichtigung. Zeigt der Leistungssensor, dass das Gerät unbemerkt gelaufen sein könnte, sagt
die Nachricht das.

## 4. Was SunCore nie tut, und Sicherheitsverhalten

- **Es unterbricht nie ein laufendes Programm.** Es schaltet die Steckdose oder einen
  Fernstart-Schalter nie aus, solange der Auftrag läuft, das Gerät die Leerlaufleistung oder mehr
  zieht, seine Leistung unbekannt ist oder innerhalb der Laufzeit plus 15 Minuten nach dem Start
  durch SunCore. Der Not-Aus und der Netz-Abwurf lassen ein laufendes Programm ebenfalls in Ruhe.
- **Der sichere Zustand der Steckdose ist EIN.** Immer wenn SunCore die Steckdose loslässt (du
  brichst den Auftrag ab, **Zurück zu normal**, ein Herunterfahren oder Update, ein Absturz, ein
  ausgefallener Leistungssensor, ein verpasster Auftrag), schaltet es sie **ein**, und eine
  wartende Maschine startet dann sofort: „lieber zu früh als nie“. Dasselbe passiert, wenn du den
  Modus auf Schatten absenkst.
- **Ausschalten nur beim Vormerken:** Die Steckdose wird nur innerhalb von 10 Minuten nach dem
  Vormerken ausgeschaltet, und nur solange das Gerät im Leerlauf ist. Eine Steckdose, die später
  an ist (du, eine Freigabe, die Wiederherstellung), bleibt für diesen Auftrag an.
- **Startbedingungen:** ein frischer Leistungswert des Geräts, ein vertrauenswürdiger
  Netzwert, kein Inselbetrieb, nicht durch Not-Aus oder Watchdog-Pause blockiert. Ein Start, den
  deine Frist erzwingt, darf bis zur Spitzenleistung des Programms beziehen, aber nie über
  Anschlussgrenze, §14a-Budget oder Inselregeln hinaus (siehe [Netzgrenzen](grid-limits.md)).
- **Schatten:** Es wird nichts geschaltet, das Gerät läuft, wie du es verlassen hast. Ein Programm
  mit **Benachrichtigung** bittet dich trotzdem um den Start, weil SunCore in keinem Modus etwas
  schreibt.
- **Absturz beim Warten:** Beim nächsten Start wird die Steckdose eingeschaltet und das Programm
  startet.

### Die Home-Assistant-Automation für eine Steckdose, die aus bleibt

Läuft SunCore **gar nicht**, während die Steckdose aus ist (gestoppte App, ein defektes Home
Assistant), schaltet sie niemand ein. Lege eine Home-Assistant-Automation an, die sie einschaltet,
wenn sie länger aus war, als du ein Programm je verzögern würdest. Das YAML steht in der
[Überschusssteuerung](surplus-control.md#verschiebbare-programme-waschmaschine-geschirrspüler);
ersetze die Entity-ID und wähle die Stunden passend zu deiner längsten „Fertig bis“. SunCore legt
diese Automation nicht selbst an.

## 5. Bekannte Grenzen und UNVERIFIED

- Nur Simulator: keine echte Waschmaschine oder Geschirrspüler hinter einer Steckdose.
- Ob ein Gerät nach Stromrückkehr fortsetzt, ist pro Gerät ungeprüft (du bestätigst es).
- Fernstart geht nur mit einem `switch`. Für keine Integration wurde ein Fernstart-Schalter
  geprüft.
- Das Vormerken geht nur im Panel. Es gibt noch keine HA-Entitäten oder MQTT-Befehle dafür (siehe
  [Home-Assistant-Entitäten](home-assistant-entities.md)).
- Die Steckdose darf nur in den ersten 10 Minuten nach dem Vormerken ausgeschaltet werden. Merke
  direkt nach dem Beladen vor.
- Ein Gerät, das direkt nach dem Start mehr als die Leerlaufleistung zieht (Türschloss, Pumpe),
  gilt als laufend, und SunCore verzögert es nicht mehr.
- Platzhalter in den Vorlagen (Laufzeit 120 min, Leerlauf 5 W, Spitzenleistung) musst du selbst
  setzen.

Siehe auch: [Benachrichtigungen](notifications.md), [Netzgrenzen](grid-limits.md).
