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

Ein Hosted-Weblate-Projekt ist geplant, aber **noch nicht eingerichtet**. Nutze vorerst einen
Pull Request. Diese Seite nennt das Projekt, sobald es existiert.

So wird es funktionieren:

- Drei Komponenten: `frontend` (das Panel, `frontend/src/lib/i18n/locales/*.json`, Quelle
  `en.json`), `frontend-params` (Namen und Hilfetexte der Parameter, `params.*.json`) und
  `app-options` (die Optionen der App, `addon/suncore/translations/*.yaml`). Die Werte jeder
  Einstellung stehen in
  [docs/i18n-weblate.md](https://github.com/pxljake/suncore/blob/main/docs/i18n-weblate.md)
  (englisch).
- Weblate prüft, dass deine Übersetzung die Platzhalter und die ICU-Plural-Struktur des
  englischen Textes behält.
- Weblate schiebt auf den Zweig `weblate` und öffnet einen Pull Request. Ein Workflow erzeugt
  danach die Backend-Texte (`suncore/i18n/catalogue/`) und die Optionsübersetzungen der Beta-App
  neu und committet sie in diesen Pull Request. Ein Text mit ICU-Syntax, die das Backend nicht
  kann, scheitert dort mit der Liste der Schlüssel; korrigiere ihn in Weblate.
- Eine neue Sprache nimmt ein Maintainer auf, weil das Panel dafür auch Code braucht. Frage in
  einem Issue.

### Für den Maintainer: Projekt einrichten

1. Auf `hosted.weblate.org` ein Projekt `suncore` anlegen und Libre-Hosting beantragen (ob der
   Libre-Plan kostenlos ist, ist `UNVERIFIED`; bitte auf `weblate.org` bestätigen).
2. GitHub verbinden (*Connect GitHub account*, Hosted-Weblate-App für das Repository). Als
   Versionsverwaltung **GitHub** und als *Push branch* `weblate` einstellen.
3. Drei Komponenten anlegen, Werte siehe
   [docs/i18n-weblate.md](https://github.com/pxljake/suncore/blob/main/docs/i18n-weblate.md):
   - `frontend`: Dateiformat *JSON file*, Maske `frontend/src/lib/i18n/locales/*.json`, Quelle
     `en.json`, Flag `icu-message-format`, Einrückung 2.
   - `frontend-params`: wie `frontend`, Maske `frontend/src/lib/i18n/locales/params.*.json`,
     Quelle `params.en.json`, Repository `weblate://<Projekt>/frontend`.
   - `app-options`: Dateiformat *YAML file*, Maske `addon/suncore/translations/*.yaml`, Quelle
     `en.yaml`, Einrückung 2, keine Zeilenumbrüche.
4. Der Zweig `weblate` darf nicht geschützt sein, `main` bleibt geschützt. Optional ein Token
   (nur dieses Repository, *Contents: write*) als Secret `WEBLATE_SYNC_TOKEN`, damit CI auch auf
   dem Commit des Workflows läuft.
5. Probe: einen deutschen Text in Weblate ändern und den Pull Request prüfen (`Weblate sync`,
   `CI`, `App lint` grün; Einrückung, Umlaute und Schlüsselreihenfolge unverändert).
6. Im README den auskommentierten Hinweis einschalten und den Satz „noch nicht eingerichtet“ oben
   durch den Link auf das Projekt ersetzen.

Die ausführliche Fassung steht im
[Plan, Frage 24](https://github.com/pxljake/suncore/blob/main/docs/plans/phase-7.md).

## Per Pull Request

1. Forke das Repository und lies
   [CONTRIBUTING.md](https://github.com/pxljake/suncore/blob/main/CONTRIBUTING.md) (englisch).
2. **Panel-Texte:** Füge jeden Schlüssel in derselben Änderung zu `en.json` (der Quelle) und zu
   deiner Sprachdatei hinzu. Die Texte nutzen ICU MessageFormat: Lass Platzhalter wie `{count}`
   und Plural-Strukturen unverändert.
3. **App-Optionen:** Bearbeite `addon/suncore/translations/<Sprache>.yaml` und führe dann
   `uv run python scripts/sync_beta_translations.py` aus (kopiert die Dateien in die Beta-App),
   nach Änderungen an Panel-Texten außerdem `uv run python scripts/gen_backend_catalogue.py`.
   `uv run python scripts/check_reasons.py` prüft, dass die Backend-Schlüssel in beiden Katalogen
   vorhanden sind.
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
