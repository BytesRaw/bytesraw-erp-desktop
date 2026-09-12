# Security policy

Bytesraw ERP holds Odoo credentials and drives an authenticated session, so a
defect in it can expose a customer's ERP. Reports are welcome and taken
seriously.

## Reporting a vulnerability

**Do not open a public issue.** Use either:

- GitHub's private vulnerability reporting, from the **Security** tab of this
  repository; or
- email to **admin@bytesraw.com**, with `SECURITY` in the subject.

Please include the app version (it is in the app bar, and under
**Settings -> About**), the Windows version, and enough detail to reproduce.

We aim to acknowledge a report within three business days and to ship a fix, or
give you a timetable for one, within thirty. We will credit you in the changelog
unless you would rather we did not.

## Supported versions

Before 1.0.0, only the latest release is supported. Fixes ship in the next
version rather than as patches to an older one.

| Version | Supported |
| --- | --- |
| Latest release | yes |
| Anything older | no - upgrade |

## What the app already does

Useful context for judging whether something is a bug:

- **Passwords never enter `accounts.json`, a log line, or an `Account` object.**
  They live in the Windows Credential Manager under the service name in
  `constants.KEYRING_SERVICE`, and are read only to authenticate.
- **Each account gets its own `QWebEngineProfile`,** with its own cookie jar and
  local storage on disk, so two logins on one server cannot see each other.
- **Credentials are never typed into Odoo's HTML login form.** Sign-in is
  JSON-RPC, and the resulting session cookies are injected into the profile.
- **TLS certificate errors are rejected** unless that specific account has
  `allow_untrusted_certificate` set, and even then only for its own host. The
  option exists for an on-premise server with a self-signed certificate; the
  account form warns before it is enabled.
- **Removing an account deletes its vault entry and its browsing data** from the
  machine. Nothing is changed on the Odoo server.
- The app writes nothing beside its executable, so the program directory can
  stay read-only under a per-machine install.

## Known limitations, not vulnerabilities

- **Released binaries are not yet code-signed,** so Windows SmartScreen warns on
  download. Verify a download against `SHA256SUMS.txt` on the release. Signing
  is wired into the release workflow and turns itself on once the certificate
  secrets exist.
- **There is no in-app update check yet** (roadmap M7.3). Updates are installed
  by running a newer installer, which upgrades in place.
- **Enabling `allow_untrusted_certificate`** disables certificate validation for
  that account's host, by design and on the operator's explicit choice.

## Out of scope

- The Odoo server itself, and anything installed on it. This project is a
  client and installs no addon on any database. Report Odoo defects to
  [Odoo S.A.](https://github.com/odoo/odoo/security/policy).
- Findings that require an attacker who already has an interactive session on
  the Windows account running the app. At that point the credential vault, the
  cookie jar and the screen are all theirs regardless of this app.
- Vulnerabilities in Qt, Chromium or other dependencies, unless this project
  pins an affected version longer than it should. Report those upstream; tell
  us so we can bump the pin.
