# PDA release manifest

Place `gm_wms.json` beside the APK download URL. In platform settings, **Read version manifest** loads its release metadata into the unsaved draft. Review and save to publish it. Reading does not change the APK URL, minimum supported version or display flags.

```json
{
  "versionName": "1.4.2",
  "versionCode": 14,
  "releasedAt": "2026-10-31T13:21:00+09:00",
  "sizeBytes": 19503514,
  "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
}
```

Replace the example size and hash with the actual APK values. `version` is accepted as an alias for `versionName`; a leading `v` is removed. The version is required. Optional fields omitted from a newly read manifest clear the corresponding old release metadata, avoiding a previous APK's hash or size being retained. `releaseDate` (`YYYY-MM-DD`) is accepted when `releasedAt` is unavailable. Release timestamps require an explicit time zone.

The browser reads the manifest without authentication credentials, rejects redirects, limits it to 64 KiB and times out after 10 seconds. A different download origin must permit the WMS origin through CORS. A failed or invalid read preserves the draft. Metadata is declared by the release publisher; reading it does not download or independently verify the APK.
