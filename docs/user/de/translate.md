# SunCore übersetzen

SunCore ist auf Englisch geschrieben und wird zuerst ins Deutsche übersetzt. Diese Seite sagt, was
übersetzt werden kann, wo die Texte liegen und wie du eine Übersetzung beiträgst.

## Was übersetzt wird und wie

| Was | Wo | Wie du beiträgst |
|---|---|---|
| Panel-Texte (die Oberfläche) | `frontend/src/lib/i18n/locales/*.json`, Quelle `en.json` | Weblate, sobald es eingerichtet ist; bis dahin ein Pull Request |
| Optionen der App | `addon/suncore/translations/*.yaml` | Weblate, sobald es eingerichtet ist; bis dahin ein Pull Request |
| Texte des Backends (Benachrichtigungen, Gründe) | aus den Panel-Katalogen erzeugt | nichts zu tun: übersetze die Panel-Texte |
| Diese Dokumentation | `docs/user/<Sprache>/` | ein Pull Request |

Die Entscheidung steht in
[ADR-0020](https://github.com/pxljake/suncore/blob/main/docs/adr/0020-docs-site-and-translation.md)
(englisch): Weblate dient für das Panel und die App-Optionen, nicht für diese Dokumentation (bis
1.0).

## Weblate

Ein Hosted-Weblate-Projekt ist geplant. Adresse und Termin sind noch nicht veröffentlicht. Das
Weblate-Projekt ist nicht eingerichtet, also nutze vorerst einen Pull Request. Diese Seite nennt
das Projekt, sobald es existiert.

## Per Pull Request

1. Forke das Repository und lies
   [CONTRIBUTING.md](https://github.com/pxljake/suncore/blob/main/CONTRIBUTING.md) (englisch).
2. **Panel-Texte:** Füge jeden Schlüssel in derselben Änderung zu `en.json` (der Quelle) und zu
   deiner Sprachdatei hinzu. Die Texte nutzen ICU MessageFormat: Lass Platzhalter wie `{count}`
   und Plural-Strukturen unverändert.
3. Führe `uv run python scripts/check_reasons.py` aus. Es prüft, dass die Backend-Schlüssel in
   beiden Katalogen vorhanden sind.
4. **Dokumentation:** Die englischen Seiten liegen in `docs/user/en/`. Lege deine Übersetzung unter
   demselben Pfad in `docs/user/<Sprache>/` ab und halte die Struktur identisch, damit der
   Sprachumschalter die Seite findet. Fehlt eine Seite in deiner Sprache, erscheint die englische.
5. Baue die Seite zur Kontrolle: `uv run --group docs mkdocs build --strict`.

## Was eine Übersetzung beibehalten muss

- Namen von Schaltflächen und Ansichten, wie sie im Panel erscheinen, in Fettschrift wie auf der
  englischen Seite.
- `UNVERIFIED`-Markierungen und die Sicherheitsaussagen: Schwäche sie nicht ab.
- Erzeugte Seiten (die [Parameter-Referenz](reference/parameters.md) und die
  [Kompatibilitätsliste](compatibility.md)) entstehen aus `scripts/gen_params.py` und
  `scripts/gen_compat.py`. Ändere sie nicht von Hand; die Sprache ihrer Texte steckt in den
  Skripten und den Panel-Katalogen.

## Eine neue Sprache

Eine dritte Sprache für das Panel braucht eine Katalogdatei mit jedem Schlüssel aus `en.json`.
Für die Dokumentationsseite braucht eine Sprache zusätzlich einen Eintrag unter `plugins`, `i18n`,
`languages` in `mkdocs.yml`. Frage zuerst in einem Issue nach, damit die Generatoren und die Seite
vorbereitet werden können.
