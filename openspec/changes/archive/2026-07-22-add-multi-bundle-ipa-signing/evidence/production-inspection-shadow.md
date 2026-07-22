# Production inspection shadow run

Recorded on 2026-07-21 with the read-only `sideloadedipa inspect` command. The command
resolved the latest stable GitHub release for every active production task, verified each
advertised asset digest while streaming the download, performed safe extraction, and built
the structural code graph before attempting strict entitlement inspection. It did not call
Apple, a signing backend, R2, the registry publisher, or cache-success state.

The canonical redacted JSON report was 66,222 bytes with SHA-256
`1aa87a1731a9ad7a20256b9df207eb0ed80ab0de5330c1a5dbd497c5913c9f7b`. A scan found no
temporary absolute path, URL query token, authorization header, or credential environment
name.

| Task | Release asset | Source SHA-256 | Structural evidence | Strict result |
| --- | --- | --- | --- | --- |
| JHenTai | `v8.0.14+323` / `466939522` / `JHenTai_8.0.14+323.ipa` / 15,692,732 bytes | `f95896fc5d958bf86f3525e8120f670ce5855e0ce83cdcd5d36853a21c193d10` | 302 entries, 52 code nodes, one profile bundle, `4f4260eba07298447da0a3acc2098799471228daa5b92f465d45af0fdbae59c6` | `inventory.entitlements_invalid`: `Payload/Runner.app/Runner` has no embedded code signature |
| Eros FE | `v1.9.2+566` / `444690435` / `Eros-FE_1.9.2+566.ipa` / 24,741,532 bytes | `5fcbe9ef39e578116932edec18c1dd8715e91a7e7441cc35167ca5b295e31f39` | 397 entries, 43 code nodes, one profile bundle, `dd717f6fcdade0c830fd8f1e0bb591ee16d7589ed5f089362276aa4c25606487` | `inventory.entitlements_invalid`: `Payload/fehviewer.app/fehviewer` has no embedded code signature |
| Asspp | `4.2.1` / `446987832` / `Asspp.ipa` / 15,162,563 bytes | `07119bdc1447406fe8a813ffaf3abc1e735ded83a7c7a14d06ed44cdbc9b6625` | 56 entries, 2 code nodes, one profile bundle, `6b845c3019aed58a8869ce02abc68c27836eb1dc68316179e9906ab42880f223` | `inventory.entitlements_invalid`: `Payload/Asspp.app/Asspp` has no embedded code signature |
| PiliPlus | `2.1.0` / `474086137` / `PiliPlus_ios_2.1.0+5109.ipa` / 23,440,601 bytes | `c6ef7e1ebe45351a6a2fafa26180583e53b0e1d9a1c10b8f3663ca83df4363a4` | 447 entries, 30 code nodes, one profile bundle, `3e21925871f86d110d2165fdbb2c328530bce5a3ee99140be8648728191bf3b7` | `inventory.entitlements_invalid`: `Payload/Runner.app/Runner` has no embedded code signature |
| StikDebug | `3.1.6` / `449866943` / `StikDebug-3.1.6.ipa` / 11,028,710 bytes | `8525e946e40168f5be6b7b5289a6fc973ada79ffe344775643409be52316962f` | 18 entries, 1 code node, one profile bundle, `a6298d04e5f5f2fc22734ec8457510ad55aee386433bb43633cdee1fbefbea5d` | `inventory.entitlements_invalid`: `Payload/StikDebug.app/StikDebug` has no embedded code signature |

The first shadow run also exposed executable-mode bits on ordinary CSS and localization
resources. ZIP mode alone is not evidence that a non-Mach-O resource contains executable
code, so discovery now ignores those files while continuing to fail on invalid `.dylib`
members, unknown executable bundle types, and all valid Mach-O code it cannot classify.

All five current upstream assets are intentionally or effectively unsigned inputs. Treating
their absent entitlement evidence as an empty document would violate the change's fail-closed
inventory contract. The difference is therefore resolved as an explicit blocker rather than
an ignore: the new engine remains disabled for these legacy production tasks until a reviewed
source-entitlement contract exists, while the compatibility engine remains unchanged. The
report still retains the successfully verified source and structural findings for diagnosis.
