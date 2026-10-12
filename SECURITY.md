# Security policy

SunCore controls real electrical equipment. We take reports seriously.

## Supported versions

Until 1.0, only the latest beta and the latest stable release receive
fixes.

## Reporting a vulnerability

Please do **not** open a public issue. Use GitHub's private vulnerability
reporting on this repository ("Security" tab → "Report a vulnerability")
or e-mail <jake.frietsch@outlook.de>. You will get an acknowledgement within
7 days and a fix or mitigation plan within 30 days for confirmed issues.

Include: version, installation type (app or standalone), steps to
reproduce, and the anonymised diagnostics export if relevant.

## Design notes

- Ingress-only by default; no open port unless the user enables the API
  port with a token.
- The Supervisor token is never logged or exported.
- The safety layer is the single writer to devices; shadow mode is the
  default for every new device.
- Dependencies are pinned and updated by Dependabot; CodeQL runs on every
  push; release images are signed with Cosign.
