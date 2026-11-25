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
- The app needs no `hassio_api` permission for MQTT: it asks for the MQTT service only. It
  does not mount the Home Assistant configuration folder.
- Every request that changes something must carry the header `X-SunCore-Request: 1`, which the
  panel sends and a foreign web page cannot. Only setting the emergency stop works without it.
- SunCore is local: no account, no telemetry, no cloud requests of its own.
- Dependencies are pinned and updated by Dependabot, CodeQL runs on every push and release images
  are signed with Cosign.

## Where your secrets are

- The password of a manual MQTT broker is masked in the API, in exports, in the diagnostics
  export, in the configuration history and in the log. It is kept in the clear in the app's
  database `suncore.db`, which is part of the Home Assistant app backup (the 1.0 security review
  kept it so: a key inside SunCore would sit in the same backup). So:
  - prefer the automatic broker (the Mosquitto app): SunCore asks the Supervisor for its
    credentials at every connect and stores none;
  - give a manual broker a user of its own for SunCore;
  - turn on the encryption of your Home Assistant backups. A backup is encrypted only when a
    backup password or key is set; whether the onboarding sets one for you is `UNVERIFIED`.
- Anyone who can log in to the MQTT broker can press SunCore's entities: stop it, pause devices,
  lower modes, boost a device, start a replan. With the Mosquitto app that is every Home Assistant
  user and every app with the MQTT service, the same people who can press the entities in Home
  Assistant. Raising a mode stays off unless you allow it. If that is too wide for you, restrict the
  topic `suncore/#` with the access control list of your broker (see the documentation of the
  Mosquitto app).
- Do not post exports, logs or screenshots of your own installation in a public issue without
  checking them for tokens, passwords and addresses.
