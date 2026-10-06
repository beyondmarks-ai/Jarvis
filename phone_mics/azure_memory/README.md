# Shared household memory

One household partition is shared by all microphone clients. A phone name is a
device label, not a verified speaker identity. A first-person name introduction
updates the shared `user.name`; use explicit names for facts about different people.
Everyone with the microphone access key can read and delete household memories.
The Normal/Gen Z personality selection is a separate `kind=settings` document in
the same household partition, without an embedding. It never appears in fact recall
or the saved-facts list and is not removed by “forget all memories”. Both modes use
the same facts and deletion confirmation rules.
The Male/Female speaking voice uses another separate settings document,
`settings.voice`, also excluded from semantic recall and forget-all operations.

## Behaviour

- “My name is Rakesh” and explicit “remember …” requests save durable facts.
- Corrections replace the same canonical attribute, rather than accumulating old
  competing values. Arbitrary attributes are interpreted by the language model;
  review the saved-facts panel if exact wording or grouping matters.
- Personal questions use exact attribute lookup where possible, otherwise
  multilingual semantic search followed by a relevance check.
- “Forget my name” previews matching facts. Say “confirm forget” within 90 seconds
  to delete. A different request, cancel, expiry or service restart cancels the
  preview. Confirmation is household-wide, not speaker-authenticated.
- The panel lists every saved fact and can delete an exact fact after confirmation.
  ETags prevent an old preview from deleting a newly changed fact.
- Ordinary conversation is not durably archived. Up to four recent turns are kept
  in process memory, expiring after ten minutes of inactivity. Mutation clears
  prior context. Successful deletion retains no preview in conversation history.
- Obvious passwords, PINs, OTPs and financial credentials are refused. This is a
  best-effort guard, not a secret vault or comprehensive sensitive-data detector.
- Maximum 500 live facts, up to four updates per utterance, top eight semantic
  candidates. This is bounded personal memory, not an unlimited document library.

Speech recognition, intent classification and answers can still make mistakes.
This feature does not guarantee perfect recall, identity verification or wake-word
accuracy. Confirm critical facts in the panel. Replies use the supported Sarvam
language set; multilingual retrieval does not add unsupported TTS languages.

## Storage and privacy

Azure Cosmos DB serverless NoSQL stores one document containing a fact and its
384-dimensional vector. The flat cosine index provides exact vector ranking for
this small collection. E5 embeddings run locally in the hub, not at an embedding
API. Requests and selected facts still go to the existing Sarvam answer provider.
No microphone access key, provider key or raw audio is stored as a memory fact.

Deletion removes the live fact and vector together, verifies point-read absence,
and clears application conversation context. The Azure account is configured for
periodic backups every four hours with eight-hour retention. Deletion does not
purge existing backup copies immediately or erase speech-provider records. Do not
restore old backups into production without reconciling facts that were forgotten.

Cloud access uses the hub's managed identity with Cosmos DB data permissions scoped
to the Jarvis database. Local account-key authentication is disabled on the deployed
account. The HTTP API uses the existing microphone bearer key, does not accept a
partition/profile argument, and sends `Cache-Control: no-store`. Saved facts render
as text, never HTML. The hub must remain single-process/single-replica: turn arbitration,
confirmation state and write serialization are in memory.

## Provisioning with Azure CLI

The current deployment uses `rg-jarvis-voice`, account `jarvis-memory-617db5`,
database `jarvis`, container `memories`, partition key `/household`, and app
`jarvis-phone-hub`. Cosmos DB serverless charges for usage and storage; existing
Container Apps compute and speech-model usage are additional. No fixed monthly
price or free tier is implied.

For a new deployment, choose an available account name and substitute your own
resource group, location, app and principal IDs. Do not recreate an existing account.

```powershell
az cosmosdb create -g RESOURCE_GROUP -n ACCOUNT --locations regionName=centralindia --capabilities EnableServerless EnableNoSQLVectorSearch --default-consistency-level Session --backup-policy-type Periodic --backup-interval 240 --backup-retention 8 --backup-redundancy Local
az cosmosdb sql database create -g RESOURCE_GROUP -a ACCOUNT -n jarvis
az cosmosdb sql container create -g RESOURCE_GROUP -a ACCOUNT -d jarvis -n memories --partition-key-path /household --vector-embeddings '@phone_mics/azure_memory/embedding_policy.json' --idx '@phone_mics/azure_memory/index_policy.json' --ttl -1
az containerapp identity assign -g RESOURCE_GROUP -n APP --system-assigned
az cosmosdb sql role assignment create -g RESOURCE_GROUP -a ACCOUNT --principal-id APP_PRINCIPAL_ID --role-definition-id 00000000-0000-0000-0000-000000000002 --scope /dbs/jarvis
```

Allow time for capability and role propagation. Deploy the image built with
`phone_mics/Dockerfile`, keeping all existing secret references. Set
`JARVIS_MEMORY_ENABLED=1` and
`JARVIS_MEMORY_ENDPOINT=https://ACCOUNT.documents.azure.com:443/` on the app.
The image includes the embedding assets at `/app/memory-model`. Startup checks DB
access and loads the model before marking the revision ready. Once identity access
is verified, disable account keys with `az cosmosdb update -g RESOURCE_GROUP -n
ACCOUNT --disable-local-auth true`. Do not put database credentials in phones.

Roll back application memory by deploying the prior image or setting
`JARVIS_MEMORY_ENABLED=0`; this leaves saved facts intact. Disabled memory means the
previous voice assistant does not perform persistent remember/forget operations.

## Verification

```powershell
.\.tmp_phone_env\Scripts\python.exe -m pytest phone_mics realtime_relay -q
node phone_mics/test_audio.mjs
.\.tmp_phone_env\Scripts\python.exe phone_mics/check_browser_memory.py
```

The browser check needs a running local hub and Playwright/Chrome. Its synthetic
API fixtures verify key handling, save rendering, XSS-safe text, deletion
confirmation/cancellation, and mobile layout without writing cloud facts.

For an explicitly opted-in live test, provide `JARVIS_MEMORY_ENDPOINT` and
`SARVAM_API_KEY`, log in with Azure CLI on PATH, and grant that test identity the
same database-scoped data role. Run `python -m phone_mics.check_memory_live`.
It uses a unique test partition, real embeddings, real Cosmos queries and Sarvam;
tests persistence, correction, Hindi recall, vector ranking, partition separation,
ETag conflicts, confirmed deletion and classification; then verifies cleanup in
`finally`. A failed cleanup must be investigated before assuming test data is gone.

Embedding source: [multilingual-e5-small](https://huggingface.co/intfloat/multilingual-e5-small),
MIT, pinned revision `614241f622f53c4eeff9890bdc4f31cfecc418b3`.
The downloader records artifact hashes. This does not change the separate
non-commercial licence restrictions of the Hey Jarvis wake-word weights.
