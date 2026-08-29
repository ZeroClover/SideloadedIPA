# Troubleshooting Guide

This guide helps you identify and resolve common issues encountered while running the SideloadedIPA pipeline.

---

## 1. Source & Asset Selection Issues

### `source.asset-match-count` Error (0 or >1 Assets Found)
- **Cause**: The `release_glob` in `tasks.toml` did not match exactly one IPA file in the GitHub release.
- **Fix**: Inspect the release assets on GitHub and make `release_glob` more specific (e.g., use `LiveContainer.ipa` instead of `*.ipa` if multiple variant IPAs exist).

### Source SHA-256 Checksum Mismatch
- **Cause**: The downloaded file from `ipa_url` does not match the configured `ipa_sha256`.
- **Fix**: Recalculate the checksum with `shasum -a 256 <file>.ipa` and update `ipa_sha256` in `tasks.toml`.

---

## 2. Bundle & Extension Errors

### Unknown Profile-Bearing Bundle Found
- **Cause**: An upstream update added a new app extension (`.appex`) or helper binary that is not defined in `configs/tasks.toml`.
- **Fix**: Add a new `[[tasks.signing.bundles]]` entry in `tasks.toml` mapping the new `source_bundle_id`, desired `target_bundle_id`, and required capabilities.

---

## 3. Apple Developer & Provisioning Issues

### App Group Association Required (`manual-required`)
- **Cause**: Apple's public API cannot automatically associate an App Group with an App ID without Admin intervention in certain configurations.
- **Fix**: Open the Apple Developer Portal web interface, assign the App Group to the specified App ID, and add the alias to `manual_app_group_associations = ["<alias>"]` under `[tasks.signing]`.

### `apple.profile-entitlement-unauthorized`
- **Cause**: An entitlement requested in `tasks.toml` (or custom template) is not authorized by the current provisioning profile.
- **Fix**:
  1. Check that the required capability is enabled for that App ID in the Developer Portal.
  2. Run `sideloadedipa sync --apply` to generate an updated provisioning profile.

---

## 4. Signing & Verification Failures

### 128 Keychain Groups Missing (LiveContainer)
- **Cause**: LiveContainer requires 128 sequential keychain access groups (`.shared` through `.127`). If fewer groups are present, signing fell back to profile defaults.
- **Fix**: Ensure `entitlement_mode = "template"` is set for both the root and `LiveProcess` bundles, pointing to `configs/signing/livecontainer/root-process.plist`.

### XML and DER Entitlement Disagreement
- **Cause**: The Mach-O code signature contains inconsistent XML and DER entitlement blocks.
- **Fix**: Stop publication and re-run backend qualification (`sideloadedipa-qualify-backend`) to check `zsign` behavior against the macOS codesign oracle.

### Nested Signature Verification Failure
- **Cause**: An embedded framework or extension was signed with the wrong certificate, invalid profile, or out of order.
- **Fix**: Check the deepest failing path in the verification report. Ensure the signing order signs all child frameworks and extensions before the main root executable.

