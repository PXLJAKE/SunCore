# Security

SunCore controls real electrical equipment, so security reports are taken seriously. The binding
text is the
[security policy](https://github.com/pxljake/suncore/blob/main/SECURITY.md) in the repository.

## Report a vulnerability

Please do **not** open a public issue. Use GitHub's private vulnerability reporting on the
repository ("Security" tab, "Report a vulnerability") or write to the e-mail address in the
security policy. You get an acknowledgement within 7 days and, for confirmed issues, a fix or a
mitigation plan within 30 days.

Include the version, the installation type, the steps to reproduce and, if it is relevant, the
anonymised diagnostics export (see [Troubleshooting](troubleshooting.md) for its state).

## Supported versions

Until 1.0 only the latest beta and the latest stable release receive fixes.

## What the design does

- The panel is reachable through Home Assistant's ingress only, for administrators. No port is
  open unless you enable the API port with a token. Direct access is refused ("Access denied.
  Open SunCore from Home Assistant.").
- The Supervisor token is never logged or exported. Log output has tokens redacted.
- The safety layer is the single writer to devices, and shadow mode is the default for every new
  device. Lowering a mode applies at once; raising a mode from Home Assistant is refused unless
  you allow it in the settings.
- The app needs no `hassio_api` permission for MQTT: it asks for the MQTT service only.
- SunCore is local: no account, no telemetry, no cloud requests of its own.
- Dependencies are pinned and updated by Dependabot, CodeQL runs on every push and release images
  are signed with Cosign.

## Where your secrets are

- The MQTT password is stored as a secret and is masked in the API, in exports and in the
  configuration history. UNVERIFIED: it is kept in the clear in the app's database `suncore.db`,
  which is part of the Home Assistant app backup. Protect your backups (Home Assistant can
  encrypt them). A security review of this point is planned for Phase 7.
- Do not post exports, logs or screenshots of your own installation in a public issue without
  checking them for tokens, passwords and addresses.
