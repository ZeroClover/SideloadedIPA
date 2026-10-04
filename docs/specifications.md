# Capability map

Audience: Agents working on requirements. Behavioral instructions live in
[AGENTS.md](../AGENTS.md); workflow mechanics in [OpenSpec](openspec.md).
This file routes domain context and does not duplicate versions, CI commands or
migration policy.

## Product and boundaries

Python owns acquisition, Apple resource reconciliation, multi-bundle signing,
independent verification, caching and R2 publication. Next.js owns catalog and OTA
plist delivery. Production signing runs on Linux with a qualified patched zsign and
verified ASC CLI. Current dependencies/pins live in manifests and workflows.

For implementation boundaries use [Architecture](architecture.md), task policy
[Configuration](configuration.md), and external effects
[Publication](publication.md) / [Security](security.md).

## Read capabilities by change

Links below select baseline capabilities; implementation anchors live in the task modules.

| Affected behavior | Baseline capability |
| --- | --- |
| Task/source TOML and pinned direct downloads | [task-configuration](../openspec/specs/task-configuration/spec.md) |
| Bundle policy and entitlement templates | [signing-task-configuration](../openspec/specs/signing-task-configuration/spec.md) |
| Release selection and bounded asset acquisition | [github-release-tracking](../openspec/specs/github-release-tracking/spec.md) |
| Safe IPA extraction and bundle graph | [ipa-bundle-inventory](../openspec/specs/ipa-bundle-inventory/spec.md) |
| App IDs, capabilities, profiles and manual prerequisites | [apple-signing-resource-sync](../openspec/specs/apple-signing-resource-sync/spec.md) |
| Nested signing order/backend invariants | [multi-bundle-signing](../openspec/specs/multi-bundle-signing/spec.md) |
| Independent verification and evidence reuse | [signed-ipa-verification](../openspec/specs/signed-ipa-verification/spec.md) |
| Stages, manifests, publication and cancellation | [signing-workflow-orchestration](../openspec/specs/signing-workflow-orchestration/spec.md) |
| Registry caching and plist delivery | [download-registry-delivery](../openspec/specs/download-registry-delivery/spec.md) |
| Dependency/tool pinning and qualification | [toolchain-reproducibility](../openspec/specs/toolchain-reproducibility/spec.md) |
| CI gates and workflow analysis | [ci-validation](../openspec/specs/ci-validation/spec.md) |
| Schedule/dispatch | [scheduled-execution](../openspec/specs/scheduled-execution/spec.md) |
| Device-driven invalidation | [device-list-caching](../openspec/specs/device-list-caching/spec.md) |
| Cached execution decisions | [workflow-optimization](../openspec/specs/workflow-optimization/spec.md) |

Read relevant active deltas before interpreting a baseline as the implemented state.
Use openspec list --json to discover changes. Archived proposals/spec deltas are
snapshots; do not apply their old commands or compatibility policy as current rules.
