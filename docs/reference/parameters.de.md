<!-- Generiert von scripts/gen_params.py aus suncore/core/params.py. Nicht von Hand ändern. -->

# Parameter-Referenz

Alle Parameter des Expertenmodus mit Einheit, Standardwert, harter Grenze und Hilfetext. Die
Registry in `suncore/core/params.py` ist die einzige Quelle der Expertenseite, der Prüfung von `PUT
/api/v1/params` und dieser Seite.

- **Sicherheit**: Der Bereich ist die harte Grenze der Sicherheitsschicht. Eine Änderung braucht
  eine Bestätigung (`confirm_safety`), und kein Wert innerhalb des Bereichs schaltet eine Prüfung
  ab. Werte außerhalb werden immer abgelehnt.
- **Neustart**: wirkt erst nach einem Neustart der App. Parameter ohne diese Markierung gelten
  sofort (der Regler übergibt wie nach einer Geräteänderung; der Planer nutzt die neuen Werte ab dem
  nächsten Lauf).
- **Was-wäre-wenn**: kann in Was-wäre-wenn-Läufen und Backtests variiert werden.
- Der Expertenmodus schreibt nur Konfiguration, nie Geräte-Sollwerte oder Betriebsarten.
- Ein Wert gleich dem Standard wird nicht gespeichert; *Zurücksetzen* entfernt die Überschreibung.

## Regler und Sicherheit

| Schlüssel | Typ | Einheit | Standard | Bereich | Markierung | Beschreibung |
|---|---|---|---|---|---|---|
| `controller.balance_tolerance_pct` | float | % | 20 | 5 – 30 | Sicherheit | **Toleranz der Leistungsbilanz in Prozent.** Relativer Anteil der Bilanztoleranz. Eine größere Toleranz akzeptiert schlechtere Daten und ist deshalb begrenzt. Gespeichert im Abschnitt `expert`. |
| `controller.balance_tolerance_w` | float | W | 500 | 100 – 1000 | Sicherheit | **Toleranz der Leistungsbilanz.** Der gemessene Hausverbrauch darf um diesen Betrag von PV plus Netz minus Akku abweichen, bevor die Daten als widersprüchlich gelten. Eine größere Toleranz akzeptiert schlechtere Daten und ist deshalb begrenzt. Gespeichert im Abschnitt `expert`. |
| `controller.default_hysteresis_delay_s` | float | s | 60 | 0 – 3600 | – | **Standard-Hysterese (Zeit).** Wie lange eine Bedingung gelten muss, bevor ein Gerät schaltet, für Geräte ohne eigene Einstellung. Gespeichert im Abschnitt `expert`. |
| `controller.default_hysteresis_w` | float | W | 100 | 0 – 5000 | – | **Standard-Hysterese (Leistung).** Leistungsband zwischen Ein- und Ausschalten eines Geräts, für Geräte ohne eigene Einstellung. Ein breiteres Band schaltet seltener. Gespeichert im Abschnitt `expert`. |
| `controller.default_max_switches_per_hour` | int | 1/h | 6 | 1 – 30 | Sicherheit | **Standard-Höchstzahl Schaltspiele pro Stunde.** Obergrenze der Schaltvorgänge je Gerät und Stunde, für Geräte ohne eigene Einstellung. Schützt Relais und Kompressoren; von der Sicherheitsschicht begrenzt. Gespeichert im Abschnitt `expert`. |
| `controller.default_settle_s` | float | s | 10 | 1 – 60 | Sicherheit | **Standard-Einschwingzeit.** Wartezeit nach einem Schaltvorgang, bevor das Ergebnis zurückgelesen wird, für Geräte ohne eigene Einstellung. Eine zu kurze Zeit liest das Gerät, bevor es reagiert hat. Sie bestimmt auch den Abstand der Schritte, mit denen SunCore über der Bezugsgrenze zurückregelt, und ist deshalb auf eine Minute begrenzt. Gespeichert im Abschnitt `expert`. |
| `controller.deficit_budget_wh` | float | Wh | 50 | 0 – 500 | Sicherheit | **Akku-Budget bei Defizit.** Akku-Energie, die ein einzelnes Defizit ziehen darf, bevor der Regler zurücknimmt. Von der Sicherheitsschicht begrenzt. Gespeichert im Abschnitt `expert`. |
| `controller.deficit_delay_s` | float | s | 30 | 5 – 120 | Sicherheit | **Verzögerung bei Defizit.** Wie lange gesteuerte Geräte Netzbezug oder Akku-Entladung verursachen dürfen, bevor der Regler zurücknimmt. Eine längere Verzögerung lässt das Defizit länger bestehen und ist deshalb begrenzt. Gespeichert im Abschnitt `expert`. |
| `controller.frozen_pv_min_w` | float | W | 200 | 50 – 1000 | Sicherheit | **Mindestleistung für die PV-Hänger-Prüfung.** Unterhalb dieser PV-Leistung gilt die Prüfung auf eingefrorene Werte nicht (Nacht, Schnee). Gespeichert im Abschnitt `expert`. |
| `controller.frozen_pv_s` | float | s | 900 | 60 – 3600 | Sicherheit | **Zeit für eingefrorene PV-Leistung.** PV-Leistung, die bei Tageslicht so lange unverändert bleibt, gilt als Hänger des Wechselrichters. Eine längere Zeit erkennt ihn später. Gespeichert im Abschnitt `expert`. |
| `controller.inputs_max_age_ticks` | int | ticks | 3 | 1 – 10 | Sicherheit | **Maximales Alter der Anlagendaten.** Leistungsflüsse und Überschuss, die älter als diese Anzahl Regelintervalle sind, gelten als unbekannt; die Sicherheitsschicht lehnt dann jede Lasterhöhung ab. Zusammen mit dem Regelintervall sind es nie mehr als 180 Sekunden; ein größerer Wert wird verkleinert. Gespeichert im Abschnitt `expert`. |
| `controller.intent_max_age_s` | float | s | 30 | 1 – 120 | Sicherheit | **Maximales Alter eines Befehls.** Ein Befehl, der älter ist als dieser Wert, wenn er die Sicherheitsschicht erreicht, wird als veraltet verworfen. Ein längerer Wert ließe veraltete Entscheidungen durch und ist deshalb begrenzt. Gespeichert im Abschnitt `expert`. |
| `controller.max_jump_w_per_s` | float | W/s | 20000 | 1000 – 50000 | Sicherheit | **Plausible Leistungsänderung.** Anlagenleistung, die sich schneller ändert, gilt als Einheiten- oder Sensorfehler. Ein größerer Wert akzeptiert unplausible Messwerte und ist deshalb begrenzt. Gespeichert im Abschnitt `expert`. |
| `controller.min_write_interval_s` | float | s | 5 | 1 – 60 | Sicherheit | **Minimales Schreibintervall.** Kürzeste Zeit zwischen zwei Schreibzugriffen auf dasselbe Gerät. Schützt Geräte und Home Assistant vor schnellen Schreibzugriffen und kann die harte Grenze nicht unterschreiten. Sie verzögert auch das Zurückregeln über der Bezugsgrenze und ist deshalb auf eine Minute begrenzt. Gespeichert im Abschnitt `expert`. |
| `controller.proposal_ttl_s` | float | s | 120 | 10 – 3600 | – | **Gültigkeit von Vorschlägen.** Im Assistenzmodus verfällt ein Vorschlag nach dieser Zeit, wenn Sie ihn nicht bestätigen. Gespeichert im Abschnitt `expert`. |
| `controller.release_deadline_s` | float | s | 10 | 5 – 10 | Sicherheit | **Frist für die Rückgabe.** Beim Beenden oder bei einer Konfigurationsänderung müssen alle Geräte innerhalb dieser Zeit zurückgegeben sein. Gespeichert im Abschnitt `expert`. |
| `controller.reserve_w` | float | W | 100 | 50 – 10000 | Sicherheit | **Überschuss-Reserve.** Leistung, die nie an Geräte vergeben wird, als Spielraum für Messrauschen und kleine Lasten. Eine kleinere Reserve rückt näher an die Bezugsgrenze und kann deshalb die harte Grenze nicht unterschreiten. Gespeichert im Abschnitt `expert`. |
| `controller.safe_state_cooldown_s` | float | s | 120 | 30 – 3600 | Sicherheit | **Wartezeit nach sicherem Zustand.** Nach dem Ende einer Störung wird die Steuerung erst wieder aufgenommen, wenn die Störung so lange ausgeblieben ist. Eine kürzere Zeit nimmt die Steuerung früher wieder auf und kann die harte Grenze nicht unterschreiten. Gespeichert im Abschnitt `expert`. |
| `controller.smoothing_s` | float | s | 60 | 0 – 900 | – | **Glättung des Überschusses.** Zeitfenster, über das der gemessene Überschuss gemittelt wird, bevor Geräte geschaltet werden. Längere Glättung vermeidet Schalten bei kurzen Wolken. Gespeichert im Abschnitt `expert`. |
| `controller.stale_cadence_factor` | float | x | 2 | 1.5 – 3 | Sicherheit | **Faktor für veraltete Daten.** Ein langsamer Sensor gilt als veraltet, wenn er nach seinem üblichen Aktualisierungsintervall mal diesem Faktor unverändert bleibt. Ein größerer Faktor toleriert ältere Daten und ist deshalb begrenzt. Gespeichert im Abschnitt `expert`. |
| `controller.tick_s` | float | s | 5 | 2 – 60 | – | **Regelintervall.** Wie oft der Regler den Überschuss prüft und entscheidet. Kürzer reagiert schneller, schreibt aber öfter; jeder Schreibzugriff bleibt durch das Schreibintervall begrenzt. Gespeichert im Abschnitt `expert`. |

## Planer und Solver

| Schlüssel | Typ | Einheit | Standard | Bereich | Markierung | Beschreibung |
|---|---|---|---|---|---|---|
| `planner.budget_s` | float | s | 8 | 0.5 – 120 | Was-wäre-wenn | **Zeitbudget des Solvers.** Zeit, die ein Planerlauf höchstens brauchen darf. Verfehlt der Solver sie, deckt der einfache Heuristik-Plan die Lücke. Gilt ab dem nächsten Lauf. Gespeichert im Abschnitt `expert`. |
| `planner.min_event_gap_s` | float | s | 60 | 10 – 3600 | – | **Mindestabstand ausgelöster Läufe.** In dieser Zeit wird höchstens ein Planerlauf durch ein Ereignis gestartet (Preis, Prognose oder Konfigurationsänderung). Gespeichert im Abschnitt `expert`. |
| `planner.mip_gap` | float | ratio | 0.01 | 0 – 0.5 | Was-wäre-wenn | **Optimalitätslücke des Solvers.** Relativer Abstand zum Optimum, bei dem der Solver aufhört. Kleiner ist genauer und langsamer. Gespeichert im Abschnitt `expert`. |
| `planner.net_deviation_s` | float | s | 1800 | 300 – 7200 | – | **Dauer der Abweichung der Nettoleistung.** Wie lange die Abweichung der Nettoleistung anhalten muss, bevor ein neuer Plan angefordert wird. Gespeichert im Abschnitt `expert`. |
| `planner.net_deviation_w` | float | W | 1500 | 100 – 10000 | – | **Abweichung der Nettoleistung für einen neuen Plan.** Ein neuer Plan wird angefordert, wenn PV minus Verbrauch über die Abweichungsdauer so weit von der Prognose abweicht. Gespeichert im Abschnitt `expert`. |
| `planner.soc_deviation_pct` | float | % | 10 | 1 – 50 | – | **SoC-Abweichung für einen neuen Plan.** Ein neuer Plan wird angefordert, wenn die Akkuladung so weit vom Plan abweicht. Gespeichert im Abschnitt `expert`. |
| `planner.stale_after_s` | float | s | 7200 | 900 – 86400 | – | **Veralten des Plans.** Ein Plan, der älter ist, wird als veraltet markiert und nicht mehr an den Regler gegeben; dieser fällt dann auf sein Standardverhalten zurück. Gespeichert im Abschnitt `expert`. |

## Tarif

| Schlüssel | Typ | Einheit | Standard | Bereich | Markierung | Beschreibung |
|---|---|---|---|---|---|---|
| `tariff.export.negative_reference_entity` | entity | – | nicht gesetzt | – | – | **Spotpreis-Sensor für die Negativpreis-Regel.** Optionaler Sensor mit dem Spotpreis je kWh. Bei einem All-in-Tarif wie Tibber ist der Spotpreis unbekannt, die Regel beurteilt dann den Endpreis des Bezugs. Mit diesem Sensor beurteilt sie für den aktuellen Zeitabschnitt den echten Spotpreis; spätere Abschnitte nutzen weiter den Endpreis. Leer belässt den Endpreis. Gespeichert im Abschnitt `tariff` unter `export.negative_reference_entity`. |
| `tariff.export.remunerate_negative` | bool | – | aus | – | – | **Auch bei negativen Preisen vergütet.** Einschalten, wenn Ihre Anlage auch in Stunden mit negativem Spotpreis eine Einspeisevergütung erhält (zum Beispiel ältere Anlagen, für die § 51 EEG nicht gilt). Dann wird die Regel der Region „keine Vergütung“ nicht auf Ihren Einspeisepreis angewendet. Aus belässt die Regel der Region. Gespeichert im Abschnitt `tariff` unter `export.remunerate_negative`. |

## Prognose

| Schlüssel | Typ | Einheit | Standard | Bereich | Markierung | Beschreibung |
|---|---|---|---|---|---|---|
| `forecast.learning.load_ml_mode` | enum | – | shadow | `off` / `shadow` / `auto` | – | **ML-Lastmodell.** Aus: nicht verwendet. Schatten: läuft parallel und wird nur bewertet und angezeigt. Automatisch: wird verwendet, wenn es in der Genauigkeitsstatistik die Profilprognose schlägt. Gespeichert im Abschnitt `forecast` unter `learning.load_ml_mode`. |
| `forecast.learning.min_history_days` | int | d | 30 | 1 – 365 | – | **Tage Historie vor dem Lernen.** Solange weniger Tage Historie vorliegen, sammeln Kalibrierung und das ML-Lastmodell nur Statistiken und ändern nichts. Gespeichert im Abschnitt `forecast` unter `learning.min_history_days`. |
