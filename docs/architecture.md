# Architecture and invariants

Read for boundaries, orchestration, cache identity, or evidence flow.
CLI operations are in [Operations](operator-runbook.md).

## Module responsibilities

All Python paths below are relative to src/sideloadedipa/.

| Layer | Responsibility |
| --- | --- |
| domain/ | Immutable values, identifier/entitlement/capability rules, reconciliation |
| ports.py | Signing, verification and external service contracts |
| adapters/ | Apple ASC CLI, patched zsign and R2 implementations |
| config/, sources/, ipa/ | Validate task policy, acquire source, safely inventory bundles |
| apple/ | Resource intents and plan/apply coordination |
| signing/ | Validate inputs/profiles, transform bundles, plan order, execute signing |
| verification/ | Independent output signatures, profiles and entitlement checks |
| cache/ | Fingerprints, decisions, storage and reuse |
| pipeline/production.py | Compose stages and reuse prepared inputs within one transaction |
| pipeline/stages/ | Source inventory, Apple, signing, verification, publication and evidence |
| pipeline/publication_service.py | Verified publication transaction and retention |
| util/ | Atomic I/O, subprocess boundary, retry and workspace primitives |
| tools/ | Manually invoked backend qualification; not a production stage |

cli.py maps commands to application requests. scripts/ contains the dependency-audit
helper, not legacy signing wrappers. Keep business logic out of CLI/orchestrator.
Check tests/test_production_stage_architecture.py and
tests/test_production_reachability.py when moving responsibilities.

## Evidence flow

Source → inventory → Apple plan/apply → sign/cache reuse → independent verify → publish.
Persisted stage/input manifests under work/pipeline/<run-id>/ bind inputs and reports
by digest. They support cross-process commands; in-process run reuses prepared
immutable inputs without repeating unchanged reads. A manifest is evidence, not a
guarantee that external Apple or R2 state can never change.

A profile-bearing root/nested app or extension gets one explicit target App ID and
validated development profile. Frameworks/dylibs are profile-free but still signed
and verified. Unknown profile-bearing nodes fail closed. Sign deepest children
first and the containing app last; preserve the planned executable graph.

Apple reconciliation uses a held transaction snapshot and merges verified additive
mutation responses. Manual capability/App Group prerequisites remain distinct from
automatable intents. Profile authorization, exact certificate identity, device
eligibility and entitlement policy must agree before signing.

## Cache and independent verification

cache/fingerprint.py and pipeline/stages/signing_cache.py own signing identity:
source/inventory, policy/templates, Apple/profile/certificate inputs and qualified
backend identity. Compare semantic/digest contracts before changing serialization.

A cache hit is not acceptance. verification/service.py independently checks the
cached or newly signed IPA. Reuse evidence only for the same bytes, plan, policies
and required rigor. Avoid a duplicate pass within one unchanged transaction; do not
replace the one independent pass with signing-tool self-report. See the
signed-ipa-verification and multi-bundle-signing baseline specs.

## Web and publication boundary

R2 site/apps.json is the catalog source; web/lib/apps.ts validates it before page
or plist use. Fixture data is explicit, never a production fallback.
web/lib/itms-route.ts and web/lib/plist.ts own safe manifest generation.

Publication exposes verified immutable objects by updating the registry, then
expires the Next apps cache. Advertised objects need delayed retirement, not
immediate deletion. Timing, single-publisher assumptions and rollback limits live
in [Publication](publication.md). The transaction does not supply distributed
locking or compare-and-swap against other publishers.
