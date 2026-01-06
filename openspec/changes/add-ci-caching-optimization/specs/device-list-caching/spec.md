# Device List Caching Specification

## ADDED Requirements

### Requirement: Device List Snapshot

The system SHALL create and maintain a snapshot of the App Store Connect device list in cache.

#### Scenario: Fetch current device list

- **WHEN** the workflow starts
- **THEN** the system SHALL fetch all enabled iOS devices from App Store Connect API
- **AND** the system SHALL include device ID, name, platform, device class, UDID, and status
- **AND** the system SHALL store the list in memory for comparison

#### Scenario: Generate device list checksum

- **WHEN** a device list is fetched
- **THEN** the system SHALL compute a SHA-256 checksum of the normalized device data
- **AND** the checksum SHALL be deterministic for identical device lists
- **AND** the checksum SHALL change if any device is added, removed, or modified

#### Scenario: Save device list to cache

- **WHEN** the workflow completes successfully
- **THEN** the system SHALL save the device list as `device-list.json` in GitHub Actions cache
- **AND** the file SHALL include devices array, last_updated timestamp, and checksum
- **AND** the cache key SHALL be deterministic for the workflow

### Requirement: Device List Comparison

The system SHALL compare the current device list with the cached version to detect changes.

#### Scenario: Restore cached device list

- **WHEN** the workflow starts
- **THEN** the system SHALL attempt to restore `device-list.json` from GitHub Actions cache
- **AND** if cache exists, the system SHALL load the cached device list
- **AND** if cache does not exist, the system SHALL proceed with full rebuild

#### Scenario: Detect device list changes via checksum

- **WHEN** comparing current and cached device lists
- **THEN** the system SHALL compare the SHA-256 checksums
- **AND** if checksums differ, the system SHALL set `rebuild_all` flag to `true`
- **AND** if checksums match, the system SHALL set `rebuild_all` flag to `false`

#### Scenario: Log device list changes

- **WHEN** device list changes are detected
- **THEN** the system SHALL log that devices have changed
- **AND** the system SHALL log the number of devices added and removed
- **AND** the system SHALL indicate that all profiles will be regenerated

#### Scenario: Handle cache miss

- **WHEN** no cached device list exists
- **THEN** the system SHALL log that this is the first run or cache expired
- **AND** the system SHALL set `rebuild_all` flag to `true`
- **AND** the system SHALL proceed with full rebuild

### Requirement: Full Rebuild Trigger

The system SHALL regenerate all provisioning profiles and rebuild all IPAs when device list changes.

#### Scenario: Execute full rebuild on device changes

- **WHEN** `rebuild_all` flag is `true` due to device list changes
- **THEN** the system SHALL regenerate provisioning profiles for all tasks
- **AND** the system SHALL download and sign all IPAs regardless of version cache
- **AND** the system SHALL upload all signed IPAs to the asset server

#### Scenario: Skip profile regeneration when devices unchanged

- **WHEN** `rebuild_all` flag is `false` (devices unchanged)
- **THEN** the system SHALL skip the profile sync step
- **AND** the system SHALL reuse existing provisioning profiles from previous run
- **AND** the system SHALL only process tasks with version changes
