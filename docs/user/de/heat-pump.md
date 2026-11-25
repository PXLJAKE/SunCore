# Wärmepumpe

SunCore kann Solarüberschuss nutzen, um einen Warmwasserspeicher etwas stärker als üblich zu
erwärmen. Das geht auf zwei Wegen:

- **SG-Ready** (Vorlage `generic.heat_pump_sg_ready`): zwei potenzialfreie Kontakte, über zwei
  Home-Assistant-Entitäten `switch` geschaltet, versetzen die Wärmepumpe in den Betrieb „normal“
  oder „verstärkt“.
- **Sollwert-Anhebung** (Vorlage `generic.heat_pump_dhw_setpoint`): eine Entität `water_heater`,
  deren Solltemperatur SunCore innerhalb eines von dir gesetzten Bandes anhebt.

**Das ist der am wenigsten getestete Teil von SunCore.** Es stand keine Wärmepumpe zur
Verfügung: Alles lief nur im Simulator. Wie jedes Gerät startet sie im Schattenmodus und läuft
höchstens assistiert, bis du das Verhalten bestätigst.

## 1. Einrichten

Wähle im Einrichtungsassistenten, Schritt *Verbraucher*, die Vorlage und ordne die Entitäten zu:

- **SG-Ready:** die beiden Kontakt-Schalter (A und B) und, dringend empfohlen, einen
  **Speicher-Temperaturfühler** (ein Temperatursensor des Warmwasserspeichers). Trage die
  elektrische Leistung ein, die die Pumpe im Betrieb zieht. Ohne Fühler geht SunCore nie über
  Zustand 2 hinaus.
- **Sollwert-Anhebung:** die Entität `water_heater`. SunCore schreibt nur Werte innerhalb von
  Minimum und Maximum der Entität.
- Beide: das **Komfortband** (Standard 45 bis 55 °C) und das Kontrollkästchen „Ich habe das
  Verhalten dieses Geräts geprüft und bestätige es“, wenn der Schattenmodus gut aussah.

Die Standard-Kontaktbelegung ist **normal = A aus, B aus; verstärkt = A aus, B an; Zwang = A an,
B an; (Sperre = A an, B aus)**. Das ist die übliche Verdrahtung, aber das SG-Ready-
Label-Dokument wurde nicht gelesen: **Prüfe die Anleitung deiner Wärmepumpe, bevor du auf
assistiert gehst.**

## 2. Was SunCore tut

| Aspekt | Verhalten |
|---|---|
| SG-Zustand 2 (normal) | Der Ruhezustand. |
| SG-Zustand 3 (verstärkt) | Bei Überschuss und wenn der Speicher unter dem Maximum des Komfortbands liegt. |
| SG-Zustand 4 (Zwang ein) | **Nur mit deiner ausdrücklichen Freigabe** (`allow_forced`). Standardmäßig aus. |
| SG-Zustand 1 (Sperre des Netzbetreibers) | **Nie.** SunCore schreibt ihn nicht, und die beiden Kontakte werden nacheinander geschaltet, damit ein Wechsel nie durch ihn hindurchgeht. |
| Verweildauer | Mindestens 10 Minuten zwischen zwei Zustandswechseln, zusätzlich zur Mindestlaufzeit (20 min), Mindestpause (10 min) und 3 Schaltungen pro Stunde. |
| Komfortband | Nie auf oder über das Maximum des Bandes angehoben, und nicht bei unbekanntem oder veraltetem Fühler. Wiedereinschalt-Hysterese: 3 K unter dem Maximum. |
| Sollwert-Anhebung | Bis zu 5 K in 0,5-K-Schritten, innerhalb des Komfortbands, nie unter deinem eigenen Sollwert. |
| Fail-safe | Sofort zurück auf Zustand 2 (oder deinen eigenen Sollwert), ohne Rücksicht auf Verweildauer und Schaltlimits, wenn der Fühler unbekannt oder veraltet ist, die Wärmegrenze erreicht ist, kein Fühler da ist, §14a aktiv ist (Gerät markiert) oder das Haus im Inselbetrieb ist. Solange so eine Ursache gilt, wird nichts erhöht. |
| Freigabe | Ein Not-Aus, der Schattenmodus, ein Auslöser oder eine gestoppte App stellen die Kontakte oder den Sollwert wieder her, die du vorher hattest. Eine Sperre, die jemand anderes gesetzt hat (Zustand 1, oder Zustand 4 ohne Freigabe), bleibt wie sie ist und wird nie aufgehoben. |

## 3. Planung, Modell und COP

Der Planer behandelt den Speicher als thermisches Modell erster Ordnung. Hat der Lernalgorithmus
genug Verlauf (etwa zwei Wochen Fühler-, Leistungs- und Wetterdaten in 15-Minuten-Auflösung),
passt er den Wärmeverlust gegen die **Außentemperatur** und den **COP** der Pumpe gegen die
Außentemperatur an. Bis dahin nutzt der Planer eine allgemeine COP-Kurve für Luft/Wasser und ein
reines Energiemodell. Die Gerätekarte zeigt den **SG-Zustand**, die **Anhebung**, die COP-Spanne
(als „angenommen“ markiert, wenn es die allgemeine Kurve ist) und den Fehler des gelernten
Modells. Den COP beim aktuellen Wetter zeigt sie noch nicht.

Der Plan heizt den Speicher mit Überschuss vor. Vorheizen mit günstigem **Netz**strom wird nur
geplant, wenn du zustimmst (`grid_preheat`), und **die Steuerung führt es noch nicht aus.**

## 4. Bekannte Grenzen und UNVERIFIED

- Nur Simulator. Die Kontaktbelegung, das SG-Ready-Verhalten des Herstellers und welche
  Integrationen Warmwasser als `water_heater` anbieten (und wie schnell sie ein Ziel umsetzen),
  sind ungeprüft.
- Die Vorlagen-Standardwerte (Verweildauer 600 s, Mindestlaufzeit 1200 s, Pause 600 s, 3 pro
  Stunde, Komfortband 45 bis 55 °C, Wiedereinschalten 3 K) sind vorsichtige Entwurfswerte, keine
  Herstellervorgaben oder Legionellenregeln.
- Das Modell lernt Verluste gegen die Außentemperatur. Ein Speicher im Haus verliert an den Raum,
  und ein kleiner, schneller Speicher bleibt bei 15-Minuten-Daten bei reiner Energie.
- Eine Sollwert-Anhebung bei unbekannter Temperatur hat kein zusätzliches Fail-safe: Der Sollwert
  selbst ist durch das Komfortband und den Bereich der Entität begrenzt.
- Es gibt noch keine Vorlage für SG-Ready über eine Select-Entität und noch keine
  Wärmepumpen-Entitäten in Home Assistant (siehe
  [Home-Assistant-Entitäten](home-assistant-entities.md)). Heizkreise, Kühlen und Raumheizung
  werden nicht gesteuert.

Siehe auch: [Überschusssteuerung](surplus-control.md), [Netzgrenzen](grid-limits.md),
[Planung und Akkusteuerung](planning-and-battery.md).
