# Konzepte

Diese Seite erklärt die vier Ideen, die du brauchst, um SunCore zu verstehen: was der **Planer**
tut, was der **Regler** tut, was die **Sicherheitsschicht** garantiert und was die drei
**Betriebsmodi** bedeuten. Die Anleitungen am Ende gehen ins Detail.

## Das große Bild

SunCore arbeitet auf zwei Zeitskalen.

| Teil | Zeitskala | Aufgabe |
|---|---|---|
| Prognosen | Stunden bis Tage | Erwartete Solarerzeugung, Hausverbrauch und Preise. |
| Planer | alle 15 Minuten und bei Ereignissen | Ein Plan für die nächsten 48 Stunden in 15-Minuten-Schritten. |
| Regler | alle 5 Sekunden | Verteilt den echten Überschuss auf Geräte und Akku. |
| Sicherheitsschicht | bei jedem einzelnen Schreibzugriff | Prüft jede Aktion erneut und ist der einzige Schreiber. |

Der Planer schaut voraus und setzt **Bänder und Ziele**, zum Beispiel „Akku zuerst laden, bis der
Morgenpuffer erreicht ist“. Der Regler folgt diesen Bändern in Echtzeit mit dem, was Sonne und
Haus tatsächlich tun. Fehlt der Plan oder ist er veraltet, gibt es nur weniger Einschränkungen:
Der Regler verhält sich dann wie ohne Plan.

## Planer

Der Planer löst ein Optimierungsproblem (lineare Programmierung mit dem Solver HiGHS) über
Erzeugungsprognose, Verbrauchsprognose, deinen Tarif und den Akku. Ein regelbasierter Plan springt
ein, wenn der Solver in seinem Zeitbudget nicht fertig wird. Du wählst eine **Strategie**
(Eigenverbrauch, Akku möglichst voll halten oder ein Ziel-Ladestand bis zu einer Uhrzeit), eine
**Risikostufe** (standardmäßig vorsichtig) und optional das Laden aus dem Netz (standardmäßig
aus).

Jedes Planintervall hat einen **Grund** in Klartext. Die Ansicht **Plan** zeigt den Plan neben den
Messwerten, und **Warum?** erklärt ein einzelnes Intervall. Siehe
[Planung und Akkusteuerung](planning-and-battery.md) und [Plan-Diagramm](plan-chart.md).

Ein Plan ist eine Prognose, kein Versprechen. Der Planer ist bisher nicht an echter Hardware
gelaufen.

## Regler

Der Regler läuft alle 5 Sekunden (App-Option `controller_interval_s`). Er berechnet den freien
Überschuss, glättet ihn gegen Wolkenflackern, bedient die Geräte in deiner Prioritätenreihenfolge
und wendet Hysterese und Schutzzeiten an. Seine zentrale Regel: **Gesteuerte Geräte dürfen nur
echten Überschuss nutzen**, SunCore verursacht also selbst keinen Netzbezug. Der Regler **schlägt
nur vor**, was zu tun ist. Siehe [Überschusssteuerung](surplus-control.md).

## Sicherheitsschicht

Jeder Schreibzugriff auf ein Gerät oder den Akku läuft durch die Sicherheitsschicht. Nur sie darf
ein Gerät anfassen. Sie prüft jede Aktion gegen Invarianten (zum Beispiel kein von SunCore
verursachter Netzbezug, Grenzen des Anschlusses, Mindestlauf- und Mindestpausenzeiten) und kann sie
mit einem Grund ablehnen. Sie bietet außerdem:

- **Rücksicht auf Handeingriffe:** Wenn du ein Gerät selbst änderst, weicht SunCore zurück.
- **Zurückgeben:** Der Zustand vor der Übernahme wird beim Beenden, beim Not-Aus, bei
  Verbindungsverlust und beim Absenken eines Modus wiederhergestellt.
- **Not-Aus:** Eine Schaltfläche in der Kopfzeile jeder Ansicht. Sie gibt jedes Gerät innerhalb
  von 10 Sekunden zurück, schreibt nichts, bis du ihn beendest, und übersteht einen Neustart.
- **Ehrliche Daten:** Veraltete, nicht verfügbare oder ungültige Werte werden als solche
  markiert und nicht geraten.

## Betriebsmodi

Es gibt drei Modi, je Gerät und für das ganze System (der Gesamtmodus).

| Modus | Was SunCore tut |
|---|---|
| **Schatten** (Standard) | Berechnet und protokolliert, was es getan **hätte**. Schreibt nichts. |
| **Assistiert** | Schlägt Aktionen im Panel vor und wartet auf deine Bestätigung. |
| **Automatik** | Handelt selbstständig innerhalb aller Grenzen. |

Ein Gerät handelt im **niedrigeren** von Gesamtmodus und eigenem Modus. Jedes neue Gerät startet
im Schattenmodus, ein Import einer Konfiguration setzt den Gesamtmodus auf Schatten zurück, und
ein gespeichertes Dokument kann von sich aus keinen Modus erhöhen. Der empfohlene Weg: Alles im
Schattenmodus lassen, ein bis zwei Tage das **Entscheidungsprotokoll** lesen, dann **ein** Gerät
auf Assistiert und danach auf Automatik stellen.

## Weiterlesen

- [Überschusssteuerung](surplus-control.md) für Regler und Modi.
- [Planung und Akkusteuerung](planning-and-battery.md) für Prognosen, Tarife und den Plan.
- [Entscheidungen und "Warum?"](decision-log.md) für die Gründe hinter jeder Entscheidung.
- [Architektur](https://github.com/pxljake/suncore/blob/main/docs/architecture.md) für den
  Modulaufbau (Entwicklerdokument, englisch).
