# FAQ

## Schaltet SunCore meine Geräte, sobald ich es installiere?

Nein. Der Schattenmodus ist die Voreinstellung für das ganze System, für jedes neue Gerät und für
den Akku. SunCore berechnet und protokolliert, was es getan **hätte**, und schreibt nichts. Ein
Gerät wird erst gesteuert, wenn du den Gesamtmodus **und** den eigenen Modus des Geräts auf
Automatik (oder auf assistiert, wo du jede Aktion bestätigst) stellst. Siehe
[Konzepte](concepts.md).

## Brauche ich ein Konto oder die Cloud?

Nein. SunCore läuft lokal, braucht kein Konto, sendet keine Telemetrie und stellt von sich aus
keine Cloud-Anfragen. Prognosen und Preise kommen aus den Integrationen, die du in Home Assistant
ohnehin betreibst.

## Welche Geräte, Tarife und Prognosen werden unterstützt?

Die [Kompatibilitätsliste](compatibility.md) führt jede eingebaute Vorlage auf. Eine Vorlage
heißt nicht, dass das Gerät gesteuert wird: Eine Vorlage, die nur im Simulator getestet wurde, lief
nie an echter Hardware. Fehlt dein Gerät, nimm eine allgemeine Vorlage oder
[trage eine bei](device-templates.md).

## Ist das an echter Hardware getestet?

Noch nicht. Planer, Akkusteuerung, E-Auto-Laden, Wärmepumpen- und Programmsteuerung, die
Netzgrenzen und die Benachrichtigungen sind nur im Simulator gelaufen. Dafür ist die Phase im
Schattenmodus da. Die Anleitungen nennen, was `UNVERIFIED` ist.

## Wie schneidet SunCore gegenüber evcc, EMHASS und Predbat ab?

- **evcc** ist die beste Wahl für die Steuerung von Ladepunkten und hat Jahre an
  Hardware-Erfahrung. SunCore kann es als Aktor nutzen (siehe [E-Auto-Laden](ev-charging.md)).
- **EMHASS** bietet ein starkes lineares Programm, das du mit eigenen Automatisierungen
  verdrahtest.
- **Predbat** ist stark bei Akku-Arbitrage mit unterstützten Wechselrichtern, vor allem bei
  britischen Tarifen. Seine Lizenz erlaubt nur die private Nutzung.
- **SunCore** vereint Prognosen, Planer, Echtzeit-Überschusssteuerung und Sicherheitsschicht in
  einer App mit geführter Einrichtung, Schattenmodus und einem Grund für jede Entscheidung. Sein
  Planer und seine Wallbox-Steuerung sind neben diesen Werkzeugen neu und unerprobt.

## Was kann SunCore noch nicht?

Siehe [Was SunCore noch nicht kann](quick-start.md#was-suncore-noch-nicht-kann). Kurz: Nichts ist
an echter Hardware gelaufen, ein Kostal-Modbus-Akku bleibt auf assistiert begrenzt, bis sein
Rückfall-Timer gemessen ist, die Wechselrichter-Grenze bleibt aus, bis sie geprüft ist, und es gibt
keine Begleit-Integration und keine eigene Lovelace-Karte (siehe das
[Dashboard-Rezept](dashboard-recipe.md)).

## Kann ich es ohne Akku, Wallbox oder Wärmepumpe betreiben?

Ja. Der Akku-Schritt bietet **Keine Batterie** an, und Wallboxen, Wärmepumpen und andere
Verbraucher sind Geräte, die du nur hinzufügst, wenn du sie hast (siehe den
[Schnellstart](quick-start.md)). UNVERIFIED: Jede Kombination fehlender Teile wurde nur im
Simulator ausprobiert.

## Warum tut ein Gerät im Modus assistiert nichts?

Assistiert **schlägt nur vor**. Schau in der Ansicht **Jetzt** unter **Vorschläge warten auf dich**
und bestätige oder verwirf. Ein Vorschlag gilt 2 Minuten. Siehe
[Überschusssteuerung](surplus-control.md).

## Kann ich SunCore ohne Home Assistant OS nutzen?

SunCore ist als Home-Assistant-App (früher Add-on) gebaut. Andere Installationsarten sind hier
nicht beschrieben. UNVERIFIED: der Betrieb allein außerhalb des Supervisors.

## Wo liegen meine Daten?

Im Ordner `/data` der App: `suncore.db` mit deiner Konfiguration und dem Entscheidungsprotokoll
und `timeseries.db` mit Messwerten, Prognosen und Preisen. Die Konfiguration gehört zur
App-Sicherung; `timeseries.db` ist davon ausgenommen, und SunCore baut seinen
Viertelstunden-Verlauf aus der Statistik von Home Assistant neu auf, wenn die Datei fehlt.

## Wie melde ich ein Problem oder stelle eine Frage?

Eröffne ein Issue unter <https://github.com/pxljake/suncore/issues>. Was du anhängst, steht in der
[Fehlersuche](troubleshooting.md). Sicherheitsprobleme gehen über den privaten Weg in
[Sicherheit](security.md).
