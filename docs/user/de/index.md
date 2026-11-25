# SunCore-Dokumentation

SunCore ist eine Home-Assistant-App, die Solarerzeugung, Verbrauch und Preise prognostiziert, die
nächsten 24 bis 48 Stunden plant und deinen echten Solarüberschuss alle paar Sekunden auf
Verbraucher und Akku verteilt. Jeder Schreibzugriff auf ein Gerät läuft durch eine
Sicherheitsschicht, und der Schattenmodus ist die Voreinstellung: Es wird nichts geschaltet, bis
du es erlaubst.

SunCore ist **Pre-Alpha**. Weder der Planer noch die Akkusteuerung, das E-Auto-Laden, die
Wärmepumpensteuerung oder die Netzgrenzen sind bisher an echter Hardware gelaufen. Lies
[Was SunCore noch nicht kann](quick-start.md#was-suncore-noch-nicht-kann), bevor du dich darauf
verlässt.

## Hier anfangen

- [Schnellstart](quick-start.md): App installieren und den Einrichtungs-Assistenten durchlaufen.
- [Konzepte](concepts.md): Planer, Regler, Sicherheitsschicht und die drei Betriebsmodi.
- [FAQ](faq.md) und [Fehlersuche](troubleshooting.md).

## Anleitungen

- [Überschusssteuerung](surplus-control.md), [Planung und Akkusteuerung](planning-and-battery.md),
  [E-Auto-Laden und evcc](ev-charging.md), [Wärmepumpe](heat-pump.md),
  [Programme](programs.md) und [Netzgrenzen](grid-limits.md).
- [Verlauf und Analyse](history-and-analysis.md),
  [Entscheidungen und "Warum?"](decision-log.md), [Plan-Diagramm](plan-chart.md),
  [Was-wäre-wenn und Backtest](what-if-and-backtesting.md) und
  [Expertenmodus](expert-mode.md).
- [Home-Assistant-Entitäten](home-assistant-entities.md),
  [Benachrichtigungen](notifications.md) und das [Dashboard-Rezept](dashboard-recipe.md).

## Nachschlagen

- [Kompatibilitätsliste](compatibility.md) und
  [Gerätevorlage beitragen](device-templates.md).
- [Parameter-Referenz](reference/parameters.md).
- [Versionshinweise und Update](release-notes.md), [Sicherheit](security.md) und
  [Übersetzen](translate.md).

## Entwicklerdokumente

Die Spezifikation, die Architektur, die Entscheidungsdokumente (ADRs) und die Roadmap gehören
nicht zu dieser Seite. Sie liegen im
[Repository auf GitHub](https://github.com/pxljake/suncore/tree/main/docs) und ändern sich mit dem
Code. Sie sind englisch.
