# Update manifests

This branch is **not source**. It is served by GitHub Pages and holds the update
manifests the Bytesraw ERP desktop client reads to discover new versions.

- `erp/stable.json` - the stable channel
- `erp/beta.json` - added when a beta channel exists

Do not merge this branch into `main`, and do not edit a manifest by hand once
the release workflow writes them: a manifest that disagrees with the release it
points at is how a fleet of tills ends up failing a checksum in unison.

## Why the manifest lives on a URL of our own

The URL is compiled into every shipped binary and can never be changed for a
till already installed in the field. Serving it from a domain we control means
hosting can move later without stranding the installed base - and until a custom
domain exists, `next_manifest_url` lets a manifest tell existing clients where
to look from now on.

## Fields

| Field | Meaning |
| --- | --- |
| `schema` | Manifest format version. A client refuses one it does not understand rather than guessing |
| `version` | The version being offered, matching `constants.APP_VERSION` of that build |
| `minimum_supported` | Below this, the update is not optional |
| `mandatory` | Offer cannot be dismissed |
| `rollout` | Percentage of clients that should take it, for a staged release |
| `signed` | Whether the artifact carries an Authenticode signature. Clients enforce signature verification when this is true |
| `notes_url` | Where the changelog for this version lives |
| `next_manifest_url` | Set when the manifest moves; clients persist it and check there afterwards |
| `artifact.sha256` | Verified before the installer is executed, always |
| `artifact.silent_args` | How to invoke the installer unattended |
