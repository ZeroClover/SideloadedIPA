## MODIFIED Requirements

### Requirement: Aggregated side-effect-free validation

The system SHALL report all configuration and current-source inventory-policy validation errors that can be determined in one pass before applying Apple changes or starting a signing subprocess.

#### Scenario: Selected tasks contain multiple policy errors

- **WHEN** current selected source IPAs reveal uncovered bundles, absent required rules, invalid target mappings, or invalid entitlement templates
- **THEN** the production preflight SHALL report every knowable diagnostic across the selected tasks
- **AND** SHALL perform no Apple mutation, signing, cache-success promotion, upload, or registry mutation

#### Scenario: Current upstream graph differs from prior cached evidence

- **WHEN** the resolved source asset inventories to a graph that differs from cached or prior-run evidence
- **THEN** policy reconciliation SHALL run against the new graph before Apple apply
- **AND** an uncovered or ambiguous graph SHALL block all selected-task Apple mutations under batch preflight policy
