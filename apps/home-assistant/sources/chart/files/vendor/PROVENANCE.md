# Vendored OIDC Integration

Owner approved this community authentication dependency on October 19, 2026,
including after discussing its additional security exposure. No HACS or
unofficial Home Assistant image is introduced.

- Project: https://github.com/christiaangoossens/hass-oidc-auth
- Release: v1.3.0 (reviewed November 26, 2026 before initial activation)
- Artifact: https://github.com/christiaangoossens/hass-oidc-auth/releases/download/v1.3.0/hass-oidc-auth.zip
- SHA256: ff987029cf5888bb922ba3374e758aa44824d28944a5b4e5a1515965e7dddccb
- License: accompanying hass-oidc-auth-LICENSE.md (MIT).
- Startup verifies the artifact hash, checks dependency availability, and
  extracts to an emptyDir mounted read-only into HA. No package installation.
- Official pinned HA 2026.10.2 linux/amd64 image archive inspection found
  aiofiles 25.1.0, Jinja2 3.1.6, joserfc 1.7.5, cryptography 50.0.1 and
  psycopg2 2.9.12. Dependencies are covered by the HA image digest.
- An initially downloaded extra joserfc wheel was not needed and is excluded.
- v1.3.0 still declares aiofiles and joserfc, and uses Jinja2; no new external
  dependency was found in its manifest. Existing YAML keys remain supported.
  Its provider-hook detection retains a path for HA versions before 2026.11.
  This is source review, not a runtime compatibility test. MIT license unchanged.
- Replaces vulnerable v1.2.1: device-code login advisory GHSA-qq92-c2rf-6p3h
  lists <=1.2.1 affected and 1.3.0 patched:
  https://github.com/christiaangoossens/hass-oidc-auth/security/advisories/GHSA-qq92-c2rf-6p3h
- Advisory GHSA-gg9c-6c2r-6x28 lists versions 1.0.0-1.1.1 as affected and 1.2.0
  as patched: https://github.com/christiaangoossens/hass-oidc-auth/security/advisories/GHSA-gg9c-6c2r-6x28

Pinning prevents unexpected changes, not undiscovered vulnerabilities. Source
inspection and manifest checks are not a full security audit or an end-to-end
HA/OIDC test. The component runs inside HA with HA's privileges.

Before updating: review upstream advisories, release changes and HA compatibility;
verify the new artifact hash/license; update the archive, installer checksum and
regression test together; confirm dependencies in the new official HA image;
test local recovery, SSO, denied groups and admin mapping after owner sync.
Do not enable automatic account linking or automatic redirect without review.
