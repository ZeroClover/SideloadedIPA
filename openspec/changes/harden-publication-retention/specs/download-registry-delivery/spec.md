## MODIFIED Requirements

### Requirement: Explicit tagged registry caching
The web application SHALL opt the R2 registry read into the framework's persistent data cache and SHALL associate it with the `apps` revalidation tag.

#### Scenario: Read registry during normal service
- **WHEN** a page or ITMS request needs application data
- **THEN** the server fetch SHALL explicitly use persistent cache semantics
- **AND** the cached entry SHALL carry the `apps` tag with a 60-second refresh interval
- **AND** origin requests SHALL have a 10-second timeout

#### Scenario: Pipeline requests registry revalidation
- **WHEN** the authenticated POST-only revalidation endpoint receives the reviewed secret in its request header after an atomic registry update
- **THEN** it SHALL expire the `apps` tag immediately
- **AND** the secret SHALL NOT appear in the URL, response, or retained logs

#### Scenario: Revalidation authentication fails
- **WHEN** the revalidation header is missing or incorrect
- **THEN** the route SHALL reject the request without changing cache state

#### Scenario: R2 refresh fails with a prior valid cache entry
- **WHEN** a tagged registry refresh encounters a transport, HTTP, JSON, or schema failure after a valid registry was cached
- **THEN** the failure SHALL NOT replace the catalog with an empty synthesized registry
- **AND** requests unable to obtain validated data after immediate expiry SHALL fail explicitly rather than advertise unchecked or fabricated entries

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

#### Scenario: Manifest dependency is unavailable
- **WHEN** registry loading fails rather than returning a validated catalog
- **THEN** the manifest SHALL return a redacted HTTP 503 with no-store caching
- **AND** it SHALL NOT report an unknown application or expose origin details
