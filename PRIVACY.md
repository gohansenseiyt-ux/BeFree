# Privacy Policy

**Last updated: July 19, 2026**

This Privacy Policy explains how BeFree ("the app", "the software") handles
information when you install and use it on your Windows device. BeFree is a
personal-discipline application that blocks distracting applications and
websites with your explicit, opt-in consent.

We built BeFree around a simple principle: **your data is yours, it stays on
your machine, and it is never sent anywhere.**

---

## 1. Data We Collect

BeFree does not collect data in the sense of gathering it on a server — it
only *stores* information locally so the app can function. This includes:

- **User preferences** — interface language, theme, password/PIN hash (if
  you set one), and general app settings.
- **Blocklists** — the list of applications and websites you have chosen to
  block, and your whitelist of allowed applications.
- **Session data** — the schedules, durations, and modes (Free, Tunnel,
  Hardcore) of your focus sessions, used to compute your statistics
  (time focused, streaks, grade/score).
- **Usage statistics** — aggregated, locally-computed data such as time
  spent per application and session history, used only to display your own
  dashboard inside the app.
- **Quarantine records** — if you use Hardcore Mode's quarantine feature,
  BeFree keeps a local record of which executables were moved and where, so
  they can be restored.

BeFree does **not** collect your name, email address, IP address, device
identifiers, analytics/telemetry events, crash reports, or any other
personal data, unless you are explicitly told otherwise inside the app for a
specific optional feature.

## 2. Everything Stays Local — No External Servers

All the data listed above is written to plain configuration and data files
(e.g. `config.json`, `stats.json`, `whitelist.json`) stored **next to the
application on your own computer**. BeFree does not have a backend server,
does not require an account, and does not make network requests to transmit
your usage data anywhere.

The only network activity BeFree may ever perform is limited to
functionality you explicitly trigger yourself (for example, checking for a
newer release on GitHub, if such a feature is enabled). No personal data
is included in that kind of request, and BeFree never uploads your
blocklists, session history, or statistics to any server, cloud service, or
third party.

## 3. No Selling or Sharing of Data

Because your data never leaves your device, there is nothing for us to
sell, rent, share, or otherwise transfer to advertisers, data brokers, or
any other third party. BeFree contains no ad SDKs and no third-party
analytics or tracking libraries.

## 4. Your Rights and Control Over Your Data

Since all data is stored locally, you are always in full control of it:

- **Access** — you can open the app's data files directly (they are
  human-readable JSON) to see exactly what is stored.
- **Export** — the Statistics screen lets you export your session data.
- **Deletion** — uninstalling BeFree, or manually deleting its data files
  from your user profile folder, permanently erases all locally stored
  information. Nothing persists elsewhere, because nothing was ever sent
  elsewhere.
- **Modification** — you can edit your blocklists, whitelist, and
  preferences at any time from within the app.

There is no account to delete and no server-side copy to request the
removal of, because none exists.

## 5. Code Signing via SignPath Foundation

To help you verify that the BeFree installer and executable you download
are authentic and have not been tampered with, official release builds are
digitally signed using a code signing certificate provided by the
**[SignPath Foundation](https://signpath.org/)**, a non-profit that offers
free code signing to open-source projects.

Code signing is a security and integrity measure only — it verifies the
publisher and confirms the binary has not been altered after being built.
This process does not involve collecting, transmitting, or processing any
personal data about you as a user; it applies solely to the release
artifacts themselves, at build/publish time, before you ever download them.

## 6. Children's Privacy

BeFree is a general-purpose productivity tool and does not knowingly target
or collect information from children. Since the app collects no personal
data on any server, there is no remote information about any user,
including minors, for us to hold.

## 7. Changes to This Policy

We may update this Privacy Policy as BeFree evolves (for example, if a new
optional feature is added). Material changes will be reflected in the
"Last updated" date at the top of this document and, where relevant,
described in the project's release notes.

## 8. Contact

BeFree does not operate a support email, form, or server. If you have a
question about this Privacy Policy or how BeFree handles data, please open
an issue on the project's GitHub repository:

**[github.com/gohansenseiyt-ux/BeFree/issues](https://github.com/gohansenseiyt-ux/BeFree/issues)**
