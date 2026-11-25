# Dashboard-Rezept

!!! note "Diese Seite ist ein Platzhalter"

    Das fertige Dashboard zum Einfügen ist noch nicht geschrieben. Es ist Arbeitspaket 11 von
    Phase 7 und braucht einen Test an einem echten Home Assistant. Nichts unten ist ein fertiges
    Rezept.

## Was geplant ist

SunCore liefert für 1.0 keine eigene Lovelace-Karte mit. Stattdessen zeigt diese Seite ein
Home-Assistant-Dashboard (Abschnittsansicht), das nur aus Karten gebaut ist, die Home Assistant
mitbringt, auf den Entitäten, die SunCore über MQTT veröffentlicht (siehe
[Home-Assistant-Entitäten](home-assistant-entities.md)):

- Status und Modus,
- die Energiezahlen (Ersparnis, Autarkie, geplante Kosten, Prognosefehler, nächste Aktion),
- Not-Aus und „Zurück zum Normalbetrieb“,
- der Modus jedes Geräts,
- die Netzansicht,
- eine kompakte Variante für ein kleines Wandtablet.

Die Seite zeigt außerdem, wie du deine eigenen Entitäts-IDs findest, statt sie zu raten.

Die Entscheidung und ihre Gründe stehen in
[ADR-0021](https://github.com/pxljake/suncore/blob/main/docs/adr/0021-ha-dashboard-surface-for-1-0.md)
(englisch). Der Plan für die Arbeit steht im
[Plan für Phase 7](https://github.com/pxljake/suncore/blob/main/docs/plans/phase-7.md).

## Bis dahin

- Das SunCore-Panel in der Seitenleiste von Home Assistant ist die vollständige Oberfläche.
- Die Entitäten aus [Home-Assistant-Entitäten](home-assistant-entities.md) lassen sich mit den
  Karten, die du kennst, in jedes Dashboard setzen. Die Entitäts-IDs findest du unter
  **Einstellungen, Geräte & Dienste, Entitäten**, indem du nach `suncore` suchst. UNVERIFIED: Das
  genaue Präfix der Entitäts-IDs hängt von deiner Einrichtung ab; schlage sie dort nach.
