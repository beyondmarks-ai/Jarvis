# Recovery and private backup checklist

## Before tuning

1. Push `main` and `backup-before-wake-tuning` to your private GitHub repository.
2. Keep an encrypted copy of the real `secrets.yaml` outside Git.
3. Keep Sarvam credentials and the matching relay device key in a password manager or cloud secret store.
4. Keep the existing full 8 MB device dump outside Git. It contains credentials and is **not** safe to publish.
5. Record the Azure resource group/app/registry in your private deployment records. The source backup deliberately avoids your account-specific deployment details.

The original pre-realtime full-flash backup, created earlier in this setup, had SHA-256:
`902FAF6E6B0EBDC4A5C0406A8DB2E2ECF03DBF8279D8AE95C1386C715187C3E8`.
It predates the working multilingual version. It is a last-resort historical backup, not this baseline's compiled firmware.

## Recover source without discarding current edits

Create a separate worktree from the baseline tag; the target folder must not already exist:

```powershell
git worktree add ../jarvis-restore backup-before-wake-tuning
```

Restore your private configuration into that folder, install the pinned tools, validate and build. Confirm the hardware and serial port before intentionally uploading. Rebuild from source; full-flash restoration can overwrite Wi-Fi configuration, partitions and device state and should be planned separately.

## Recover the cloud service

The firmware and cloud service are separate. Checking out a Git tag does not roll back the running cloud service.

Build the baseline `realtime_relay/Dockerfile`, publish the resulting image to your own registry, then deliberately update the existing container app. Preserve runtime secrets, port 8000, TLS/WebSocket ingress and the hostname used by the device. Check `/health`, then run a live question test. Do not recreate Azure resources or overwrite secret values just to restore source code.

## What the backup does not contain

- Wi-Fi passwords, provider/device keys, real environment files.
- Azure infrastructure state or resource ownership/access.
- Full flash dumps, generated firmware binaries, recordings, local logs.
- Local Python/compiler environments, caches, inactive experimental projects.

GitHub alone cannot recover those items. A complete disaster-recovery plan needs the private backups above as well as this source repository.
