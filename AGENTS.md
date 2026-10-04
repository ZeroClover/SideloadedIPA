# Agent entry point

SideloadedIPA acquires iOS IPAs, reconciles Apple development-signing resources,
signs nested code, independently verifies artifacts, and publishes an OTA catalog.
Production runs on Linux; Python owns the pipeline and Next.js owns delivery.

## Working contract

Complete the requested local change and relevant validation without waiting for a
second invitation. Local fixture tests need no production credentials. Ask only
when a material requirement is unresolved or the next action exceeds authorization.
Apple mutations, real signing with private material, R2/Vercel publication, workflow
dispatch, and deployment require explicit authorization; examples in docs are not
permission to execute them.

Choose the simplest implementation using existing dependencies. Keep domain rules
deterministic and side effects behind ports/adapters. Remove obsolete paths rather
than adding compatibility layers, fallbacks, or migrations. Preserve unrelated
workspace changes and keep secrets, profiles, certificates, IPAs, and generated
work files out of commits and logs.

Signing-tool success is not acceptance: verification must fail closed on ambiguous
sources, unsafe archives, unknown bundles, invalid profiles/signatures, or lost
functional entitlements. Automated Apple changes are additive only. Publication
requires verified bytes and must protect already advertised artifact URLs.

## Read by task

Read the relevant module, not this entire list. Follow its source pointers only as
needed; a typo fix does not require an architecture or operations review.

| Task | Start here |
| --- | --- |
| Python implementation, refactoring, validation | [Development](docs/development.md) |
| Service boundaries, stages, cache/evidence identity | [Architecture](docs/architecture.md) |
| Task TOML, bundle rules, entitlement templates | [Configuration](docs/configuration.md) |
| Credentials, runtime or web environment | [Environment](docs/environment.md) |
| Archives, subprocesses, signing trust, CI security | [Security](docs/security.md) |
| Running stages, CI diagnosis, backend qualification | [Operations](docs/operator-runbook.md) |
| R2, registry, retirement, revalidation, recovery | [Publication](docs/publication.md) |
| Error investigation | [Troubleshooting](docs/troubleshooting.md) |
| OpenSpec proposals, requirements, apply or archive | [OpenSpec](docs/openspec.md) |
| Historical decisions or documentation maintenance | [Document map](docs/index.md) |

README.md is a human overview, not an instruction source. OpenSpec requirements
express intended behavior; code/tests describe implemented behavior. Report any
mismatch instead of silently treating either as permission to weaken a gate.
Completed tasks or archived evidence do not prove current production acceptance.

## Completion

Report the change, checks actually performed and their results, and any remaining
blocker or untested boundary. Keep the response concise and use the user's language.
Do not claim live signing, device installation, CI, or publication from fixture tests.
