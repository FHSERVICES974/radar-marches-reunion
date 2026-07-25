# Threat Model

## Project Overview

A single-file static web page (`index.html`) listing markets, fairs, and call-for-applications events for artisans and creators on Réunion Island (La Réunion). No backend, no database, no authentication. Served by Python `http.server` for development; not currently deployed to production.

Tech stack: plain HTML / CSS / JavaScript (zero npm dependencies). All data (events, organisations, contact info) is hardcoded in the same file.

## Assets

- **Contact information** — WhatsApp number and email address (`shadowneox@gmail.com`) hardcoded in the source file. These are intentionally public contact details for the page owner and carry low sensitivity.
- **Page content** — event listings and organisation directory. All data is static and publicly intended.

There are no user accounts, sessions, passwords, API keys, payment data, or application secrets.

## Trust Boundaries

- **Browser to static file server** — the only boundary. The Python `http.server` serves the single HTML file verbatim. There is no application logic server-side.
- **Client-side only** — all JavaScript runs in the browser with no outbound API calls to external services.

## Scan Anchors

- Sole entry point: `index.html` (single file, ~67 KB)
- No authenticated surface
- No admin surface
- No server-side code other than Python's built-in static file server
- Not deployed; `isDeployed: false`

## Threat Categories

### Information Disclosure

The email and WhatsApp number are embedded in `index.html` source. Because they are intentional public contact details, this is by design and not a finding. No secrets, API keys, or PII beyond the owner's own contact info are present.

### Tampering / XSS

`innerHTML` is used to render event cards and KPI counters, but the data source is entirely hardcoded JavaScript constants (`EVENTS`, `ORGS`, `CONTACT`) with no user input or external fetch calls. There is no path for attacker-controlled data to reach a DOM sink.

### Denial of Service

The page is a static file. No rate-limiting concern exists beyond what the hosting platform provides for static assets.

### Elevation of Privilege / Injection

No server-side execution, no database, no shell commands. Injection and privilege escalation attack classes do not apply.

### Spoofing / Repudiation

No authentication, sessions, or user accounts exist. These categories do not apply.
