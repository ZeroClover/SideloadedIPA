/**
 * itms-services distribution manifest builder.
 *
 * Byte-for-byte port of the former Python reference (build_itms_plist, removed
 * from the pipeline after the serverless cutover): 4-space indent, no trailing
 * whitespace, trailing newline. The golden-file check
 * (scripts/check-plist-golden.ts) diffs this output against the committed
 * fixtures — the fixtures are now the canonical expected format.
 *
 * Only the minimal keys modern iOS needs: a single software-package asset
 * plus bundle-identifier / bundle-version / kind / title.
 */

function xmlEscape(value: string): string {
  // Mirrors Python's xml.sax.saxutils.escape: & first, then < and >.
  return (value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

export function buildItmsPlist(
  ipaUrl: string,
  bundleId: string,
  version: string,
  title: string,
): string {
  return `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>items</key>
    <array>
        <dict>
            <key>assets</key>
            <array>
                <dict>
                    <key>kind</key>
                    <string>software-package</string>
                    <key>url</key>
                    <string>${xmlEscape(ipaUrl)}</string>
                </dict>
            </array>
            <key>metadata</key>
            <dict>
                <key>bundle-identifier</key>
                <string>${xmlEscape(bundleId)}</string>
                <key>bundle-version</key>
                <string>${xmlEscape(version)}</string>
                <key>kind</key>
                <string>software</string>
                <key>title</key>
                <string>${xmlEscape(title)}</string>
            </dict>
        </dict>
    </array>
</dict>
</plist>
`;
}
