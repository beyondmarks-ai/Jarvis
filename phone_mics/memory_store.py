"""Persistent household facts and vectors in one Cosmos DB document per fact."""
import hashlib
import os
from azure.cosmos.aio import CosmosClient
from azure.cosmos.exceptions import CosmosResourceNotFoundError
from azure.identity.aio import AzureCliCredential, ManagedIdentityCredential

MAX_FACTS = 500
MODE_ID = 'settings.personality'
VOICE_ID = 'settings.voice'


def fact_id(slot):
    return hashlib.sha256(slot.encode('utf-8')).hexdigest()


class CosmosMemoryStore:
    def __init__(self, endpoint=None, household='household', cli=False):
        self.household=household  # Never selectable by an HTTP client.
        self.credential=AzureCliCredential() if cli else ManagedIdentityCredential()
        self.client=CosmosClient(endpoint or os.environ['JARVIS_MEMORY_ENDPOINT'],self.credential,
                                 consistency_level='Session', connection_timeout=10, request_timeout=10)
        self.container=self.client.get_database_client('jarvis').get_container_client('memories')

    async def ready(self):
        await self.container.read()

    async def close(self):
        await self.client.close()
        await self.credential.close()

    async def get_mode(self):
        from .personality import validate_mode
        doc = await self.get(MODE_ID)
        return validate_mode(doc['mode']) if doc else 'normal'

    async def get_voice(self):
        from .voice import validate_voice
        doc = await self.get(VOICE_ID)
        return validate_voice(doc['voice']) if doc else 'male'

    async def set_voice(self, voice):
        from .voice import validate_voice
        await self.container.upsert_item({'id': VOICE_ID, 'household': self.household,
                                         'kind': 'settings', 'voice': validate_voice(voice)})
        if await self.get_voice() != voice:
            raise RuntimeError('Voice save verification failed')

    async def set_mode(self, mode):
        from .personality import validate_mode
        await self.container.upsert_item({'id': MODE_ID, 'household': self.household,
                                         'kind': 'settings', 'mode': validate_mode(mode)})
        if await self.get_mode() != mode:
            raise RuntimeError('Mode save verification failed')

    async def get(self, identifier):
        try:
            return await self.container.read_item(identifier,partition_key=self.household)
        except CosmosResourceNotFoundError:
            return None

    async def list(self):
        query='SELECT TOP 501 c.id,c.slot,c.text,c.created_at,c.updated_at,c._etag FROM c WHERE c.household=@h AND c.kind="fact" ORDER BY c.updated_at DESC'
        return [row async for row in self.container.query_items(query,parameters=[{'name':'@h','value':self.household}],partition_key=self.household)]

    async def put(self, documents):
        for doc in documents:
            if doc.get('household') != self.household or doc.get('kind')!='fact':
                raise ValueError('Invalid memory partition')
        await self.container.execute_item_batch([('upsert',(doc,)) for doc in documents],partition_key=self.household)

    async def search(self, vector):
        # Exact flat vector index; bounded retrieval and partition filtering.
        query='SELECT TOP 8 c.id FROM c WHERE c.household=@h AND c.kind="fact" ORDER BY VectorDistance(c.embedding,@vector)'
        rows=[row async for row in self.container.query_items(query,parameters=[
            {'name':'@h','value':self.household},{'name':'@vector','value':vector}],partition_key=self.household)]
        # Re-read authoritative live documents; never serve a cached deleted fact.
        result=[]
        for row in rows:
            doc=await self.get(row['id'])
            if doc:
                result.append(doc)
        return result

    async def delete(self, targets):
        # ETags prevent a confirmed old preview from deleting a subsequently updated fact.
        # Each batch is atomic. If a later batch fails, the caller reports incomplete deletion.
        for offset in range(0,len(targets),100):
            operations=[('delete',(row['id'],),{'if_match_etag':row['_etag']}) for row in targets[offset:offset+100]]
            await self.container.execute_item_batch(operations,partition_key=self.household)
        for row in targets:
            if await self.get(row['id']) is not None:
                raise RuntimeError('Deletion verification failed')
