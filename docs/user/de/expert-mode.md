# Expertenmodus

Der Expertenmodus zeigt jeden einstellbaren Parameter von SunCore mit Einheit, Standardwert,
erlaubtem Bereich und Hilfetext und lässt dich ihn ändern. Er ist **standardmäßig aus**, und
nichts in SunCore braucht ihn. Die Standardwerte sind das Verhalten, das die anderen Anleitungen
beschreiben.

## 1. Einschalten

Nutze den Schalter **Expertenmodus** unter **Einstellungen → Erweitert**. Die Wahl wird pro
Browser gespeichert. Eingeschaltet zeigt dieselbe Seite den Bereich **Experten-Einstellungen**,
und **Analyse** bekommt den Reiter **Was-wäre-wenn**.

## 2. Einen Parameter ändern

- Die Parameter sind gruppiert (Regler, Planer, Prognose, Tarif). Nutze **Parameter suchen**
  oder **Nur geänderte**.
- Jedes Feld zeigt **Standard**, **Erlaubt** (den harten Bereich) und Markierungen:
  **Geändert**, **Nicht gespeichert**, **Sicherheit**, **Neustart nötig**.
- **Zurücksetzen** setzt einen Parameter oder eine ganze Gruppe auf den Standard zurück. Das
  entfernt den gespeicherten Wert, du folgst also dem Standard, den die nächste Version liefert.
- **Änderungen speichern** sendet nur, was du geändert hast. Werte außerhalb des Bereichs werden
  mit einer Meldung am Feld abgelehnt.

### Sicherheitsparameter

Als **Sicherheit** markierte Parameter (Schaltgrenzen, Beruhigungszeiten, Höchstalter von Daten,
das Defizit-Akkubudget und Ähnliches) lassen sich nur **innerhalb der harten Grenzen der
Sicherheitsschicht** ändern. Ein Dialog zeigt alten und neuen Wert und bittet um Bestätigung.
**Keine Einstellung kann eine Sicherheitsprüfung abschalten**: Jeder Bereich hat ein positives
Minimum (die einzige 0 ist das Defizitbudget, dort bedeutet 0 kein Defizit erlaubt). Wird eine
Grenze in einer späteren Version enger, wird ein gespeicherter Wert außerhalb beim Start
begrenzt und protokolliert.

Wiederherstellen und Importieren einer Konfiguration übernehmen die gespeicherten Werte wie
bisher strikt.

## 3. Debug-Ansichten

Unter den Parametern: die rohen Live-Signale, wie der Server sie sendet (W, Vorzeichen wie
dokumentiert), der aktuelle Plan als JSON mit Kopieren-Schaltfläche und die Solver-Statistik. Sie
lesen nur.

## 4. Parameter-Referenz

Die vollständige, generierte Liste ist die
[Parameter-Referenz](reference/parameters.md):
Einheit, Standard, Bereich, Sicherheits-Markierung und Hilfetext jedes Parameters. Sie wird aus
dem Register im Code erzeugt. Ist sie falsch, ist der Code falsch, nicht die Seite.

## 5. Grenzen

- Heute sind 32 Parameter registriert. Mindestlauf- und Pausenzeiten je Gerät sind
  Geräteeinstellungen, keine Expertenparameter.
- Konfigurationsparameter wie die Regeln für negative Einspeisung werden in ihrem eigenen
  Abschnitt gespeichert, der zuerst existieren muss (Assistent abschließen).
- Die Referenz für die Regel bei negativen Preisen ist ein Sensor mit dem aktuellen Spotpreis;
  spätere Intervalle einer Quelle mit Endpreisen behalten eine Näherungsregel.
- Die Markierung "Neustart nötig" gibt es, aber noch kein Parameter nutzt sie.

## Siehe auch

[Was-wäre-wenn und Backtest](what-if-and-backtesting.md) ·
[Planung und Akkusteuerung](planning-and-battery.md)
