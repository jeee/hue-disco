# Settings backup and restore

Hue Disco Club now supports versioned settings backup export and import from the Admin interface.

## Why this exists

Copying a raw config file can break across package upgrades if field names, defaults, or profile schemas change. The backup bundle stores a version marker and a normalized settings payload, then imports those settings into the current schema on restore.

## What is included

The export includes the current interface-editable settings, including:

- bridge and entertainment group settings
- detector and render defaults
- lights
- profiles and profile light groups
- strobe presets
- web settings and passwords
- bridge credentials and session secrets when present

Because the export may contain secrets, store the backup carefully.

## Import behavior

- Accepts the package backup JSON bundle
- Also accepts compatible JSON or YAML dictionaries containing settings
- Restores supported settings into the current config schema
- Normalizes imported profiles, groups, and strobe presets using the current package code

## Upgrade behavior

The import logic is designed so a backup from an older install can be restored into a newer package version without blindly replacing files. New defaults from the current package can still apply where the backup does not define a value.

## Recommended use

1. Configure the system from the Admin page.
2. Download a backup bundle.
3. Keep it in a safe place.
4. On a new install or upgraded package, open Admin and restore that backup.
5. Review the imported settings and save again if you make any further edits.
