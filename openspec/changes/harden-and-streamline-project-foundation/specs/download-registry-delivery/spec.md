## ADDED Requirements

### Requirement: Validated download registry source
The web application MUST decode the R2 `apps.json` document into a validated registry before any entry is rendered or used to generate an ITMS manifest.

#### Scenario: Load a valid production registry
- **WHEN** the production application reads a registry containing valid unique slugs, names, bundle identifiers, versions, and HTTPS artifact URLs
- **THEN** the decoder SHALL return typed application entries
- **AND** the page and ITMS route SHALL consume that same validated result

#### Scenario: Registry schema or entry is invalid
- **WHEN** the registry root, applications collection, required string, slug uniqueness, or remote URL contract is invalid
- **THEN** the read SHALL fail with a redacted field-level diagnostic
- **AND** no unchecked value SHALL be rendered or inserted into an ITMS manifest

#### Scenario: Production registry location is absent
- **WHEN** a production deployment starts without its R2 registry location
- **THEN** the build or request SHALL fail configuration validation
- **AND** the application SHALL NOT silently use the bundled fixture

#### Scenario: Validation build uses a fixture
- **WHEN** a local, test, or CI validation build explicitly selects fixture data mode
- **THEN** the bundled registry fixture SHALL pass through the same decoder
- **AND** deployed production configuration SHALL reject fixture data mode

### Requirement: Explicit tagged registry caching
The web application SHALL opt the R2 registry read into the framework's persistent data cache and SHALL associate it with the `apps` revalidation tag.

#### Scenario: Read registry during normal service
- **WHEN** a page or ITMS request needs application data
- **THEN** the server fetch SHALL explicitly use persistent cache semantics
- **AND** the cached entry SHALL carry the `apps` tag

#### Scenario: Pipeline requests registry revalidation
- **WHEN** the authenticated revalidation endpoint receives the reviewed secret in its request header after an atomic registry update
- **THEN** it SHALL mark the `apps` tag stale using the `max` stale-while-revalidate profile
- **AND** the secret SHALL NOT appear in the URL, response, or retained logs

#### Scenario: Revalidation authentication fails
- **WHEN** the revalidation header is missing or incorrect
- **THEN** the route SHALL reject the request without changing cache state

#### Scenario: R2 refresh fails with a prior valid cache entry
- **WHEN** a tagged registry refresh encounters a transport, HTTP, JSON, or schema failure after a valid registry was cached
- **THEN** the previous valid cached registry SHALL remain eligible to serve
- **AND** the failure SHALL NOT replace it with an empty synthesized registry

#### Scenario: Initial registry load fails
- **WHEN** no valid cached registry exists and the configured production origin cannot return a valid document
- **THEN** the page or route SHALL fail explicitly
- **AND** it SHALL NOT render an apparently successful empty catalog

### Requirement: Safe ITMS manifest delivery
The ITMS route MUST generate installation manifests only from one validated registry entry and MUST encode all application-controlled XML text safely.

#### Scenario: Request a known application slug
- **WHEN** a request identifies exactly one validated registry entry
- **THEN** the route SHALL generate a plist containing that entry's HTTPS IPA URL, bundle identifier, version, and display name
- **AND** XML-significant characters SHALL be escaped

#### Scenario: Request an unknown application slug
- **WHEN** no validated registry entry matches the requested slug
- **THEN** the route SHALL return not found
- **AND** it SHALL NOT infer or construct an artifact URL from the slug

#### Scenario: Serve a generated manifest
- **WHEN** the route returns a valid ITMS plist
- **THEN** it SHALL use the XML content type and require revalidation rather than advertising an immutable manifest
