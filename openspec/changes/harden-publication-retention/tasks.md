# Tasks

## 1. Storage and publication

- [x] 1.1 Add full content identities, streaming verification and durable 48-hour retirement; verify storage/adapter tests including partial deletes and response closure.
- [x] 1.2 Preserve potentially advertised uploads on rollback and prior references during promotion; verify transaction recovery tests and document single-publisher retention.

## 2. Web delivery

- [x] 2.1 Require authenticated POST immediate expiry, bounded registry reads and safe manifest failures; verify Python transport and web route/decoder tests and document coordinated deployment.

- [x] 2.2 Update vulnerable web dependencies within Next 16, remove obsolete overrides/expired exceptions, and verify the unchanged audit gate plus web tests/build.

## 3. Integration

- [x] 3.1 Run complete offline Python tests/coverage, formatting, strict types, frontend tests/build and OpenSpec validation; review diffs before logical commits and non-forced push.
