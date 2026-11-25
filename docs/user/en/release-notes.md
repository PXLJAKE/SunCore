# Release notes and upgrade

## Release notes

The complete list of changes per version is the
[changelog](https://github.com/pxljake/suncore/blob/main/CHANGELOG.md). It is generated from the
commit messages when a release is made. The
[releases page](https://github.com/pxljake/suncore/releases) has the notes of each release and,
for stable releases, this documentation as a ZIP (`suncore-docs-<version>.zip`) for older
versions.

SunCore is **pre-alpha**. The newest tagged version is 0.2.0. While no stable release exists, the
quick start has you install the beta app (**SunCore (beta)**). The numbering of the 1.0 release
and of its betas is not final: this page is updated when it is.

## Before you update

1. **Export your configuration.** Open **Settings** and use **Export configuration**. The file
   is your way back.
2. **Keep the app backup.** The configuration is part of the app backup in Home Assistant. The
   measurement database `timeseries.db` is left out of it; SunCore rebuilds its quarter-hour
   history from Home Assistant's statistics when the file is missing.
3. **Know that a database upgrade is one-way.** A newer version migrates the database, and an
   older version cannot open a database with a newer schema. The app
   description names the database schema of your version (schema 6 in the current beta). Going
   back means restoring the backup or importing the export.
4. **Read the notes** of the release for changes in the safety or mode handling.

## What an update does to your modes

- A device whose entities or template you change falls back to shadow mode. UNVERIFIED: that
  every device keeps its mode across a version update has not been tested on a real upgrade.
- Importing a configuration always resets the overall mode to shadow.
- An active emergency stop survives the update.

## Supported upgrade paths

UNVERIFIED. An automated test that starts the 0.2.0 image with seeded data, stops it and starts the
candidate release on the same data is planned for Phase 7 (work package 13) but does not exist
yet in this version. Until it does, treat every upgrade as untested: export first, and check the
**Settings** page and the **Decision log** afterwards. An export of 0.2.0 importing into a newer
version is planned to be covered by the same work package.

## After an update

- Open the panel and check that the overall mode is what you expect.
- Run the system check at the end of the setup assistant (**Run setup again** does not delete
  your settings; **Earlier versions** restores a previous save).
- Report problems with the version number and the log, see [Troubleshooting](troubleshooting.md).
