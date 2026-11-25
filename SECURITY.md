# Security policy

SunCore controls real electrical equipment. We take reports seriously.

## Supported versions

Only the latest stable release and the latest beta receive fixes.

## Reporting a vulnerability

Please do **not** open a public issue. Use GitHub's private vulnerability
reporting on this repository ("Security" tab → "Report a vulnerability")
or e-mail <jake.frietsch@outlook.de>. You will get an acknowledgement within
7 days and a fix or mitigation plan within 30 days for confirmed issues.

Include: version, installation type (app or standalone), steps to
reproduce, and the diagnostics export if relevant (Settings → Advanced →
Diagnostics; secrets are masked and ids pseudonymised). Never send tokens
or passwords.

For the user-facing summary of what SunCore stores and exposes, see
[`docs/user/en/security.md`](docs/user/en/security.md).

## Design notes

- Ingress-only by default; no open port unless the user enables the API
  port with a token.
- The Supervisor token is never logged or exported.
- The safety layer is the single writer to devices; shadow mode is the
  default for every new device.
- Dependencies are pinned and updated by Dependabot; CodeQL runs on every
  push; release images are signed with Cosign.
