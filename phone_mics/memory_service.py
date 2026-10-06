"""Shared, explicit memory with grounded recall and confirmed, verified deletion."""
import asyncio
from datetime import datetime, timezone
import re
import time
from .memory_store import MAX_FACTS, fact_id
from .personality import mode_command, validate_mode
from .voice import voice_command, validate_voice

LANGUAGES={'en-IN','hi-IN','bn-IN','ta-IN','te-IN','gu-IN','kn-IN','ml-IN','mr-IN','pa-IN','od-IN'}
ACTIONS={'remember','forget','forget_all','recall','chat','confirm','cancel','set_mode','set_voice','choose_voice'}
SLOT=re.compile(r'^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+$')
SECRET=re.compile(r'\b(password|passcode|otp|pin code|api.?key|secret.?key|credit.?card|bank account)\b|पासवर्ड|ओटीपी',re.I)


def public_fact(doc):
    return {key:doc[key] for key in ('id','slot','text','updated_at','_etag') if key in doc}


def quick_intent(text,pending):
    clean=text.strip().rstrip('.!?').strip()
    lowered=clean.lower()
    base={'facts':[],'query':clean,'slot':'','language':'en-IN','sensitive':False}
    voice = voice_command(text)
    if voice:
        return dict(base, action='choose_voice' if voice == 'choose' else 'set_voice',
                    voice='' if voice == 'choose' else voice,
                    language='hi-IN' if re.search(r'[\u0900-\u097f]|\b(karo|bolo)\b', text) else 'en-IN')
    mode = mode_command(text)
    if mode:
        return dict(base, action='set_mode', mode=mode,
                    language='hi-IN' if re.search(r'[\u0900-\u097f]|\b(karo|aao|jao)\b', text) else 'en-IN')
    if pending and lowered in ('yes','yes please','confirm','confirm forget','yes forget it','yes delete it'):
        return dict(base,action='confirm')
    if pending and lowered in ('no','cancel','never mind','do not delete','keep it'):
        return dict(base,action='cancel')
    if lowered in ('forget everything','forget all memories','delete all memories','clear all memories','forget everything you remember'):
        return dict(base,action='forget_all')
    if lowered in ('forget my name','delete my name','forget my saved name'):
        return dict(base,action='forget',slot='user.name')
    if lowered in ('what is my name',"what's my name",'do you remember my name','tell me my name'):
        return dict(base,action='recall',slot='user.name')
    match=re.fullmatch(r'(?:please )?(?:remember(?: that)? )?my name is ([\w .\'-]{1,60})',clean,re.I)
    if match and len(match[1].split()) <= 5 and not re.search(r'\b(and|but|not|never|maybe|remember|forget)\b',match[1],re.I):
        return dict(base,action='remember',facts=[{'slot':'user.name','text':'The shared user name is '+match[1].strip()+'.'}])
    return None


def validate_intent(value):
    if not isinstance(value,dict) or value.get('action') not in ACTIONS:
        raise ValueError('Invalid memory action')
    if value['action'] == 'set_mode':
        validate_mode(value.get('mode'))
    if value['action'] == 'set_voice':
        validate_voice(value.get('voice'))
    facts=value.get('facts',[])
    if not isinstance(facts,list) or len(facts)>4:
        raise ValueError('Invalid fact list')
    if value['action']=='remember' and not facts:
        raise ValueError('No facts supplied')
    if value['action']!='remember' and facts:
        raise ValueError('Unexpected facts')
    for fact in facts:
        if not isinstance(fact,dict) or not isinstance(fact.get('slot'),str) or len(fact['slot'])>80 or not SLOT.fullmatch(fact['slot']):
            raise ValueError('Invalid fact slot')
        if not isinstance(fact.get('text'),str) or not 1<=len(fact['text'].strip())<=500:
            raise ValueError('Invalid fact text')
    if len({f['slot'] for f in facts})!=len(facts):
        raise ValueError('Conflicting updates in one request')
    if not isinstance(value.get('query',''),str) or len(value.get('query',''))>1000:
        raise ValueError('Invalid query')
    slot=value.get('slot','')
    if not isinstance(slot,str) or (slot and (len(slot)>80 or not SLOT.fullmatch(slot))):
        raise ValueError('Invalid recall slot')
    return value


class MemoryService:
    def __init__(self,store,embedder,model):
        self.store,self.embedder,self.model=store,embedder,model
        self.lock=asyncio.Lock()
        self.pending=None
        self.history=[]
        self.last_turn=0.
        self.mode_loaded=False
        self.model.mode='normal'
        self.voice='male'
        self.voice_loaded=False

    async def _load_voice(self):
        if not self.voice_loaded:
            self.voice=await self.store.get_voice()
            self.voice_loaded=True

    async def get_voice(self):
        async with self.lock:
            await self._load_voice()
            return self.voice

    async def _set_voice(self, voice):
        validate_voice(voice)
        await self.store.set_voice(voice)
        self.voice=voice
        self.voice_loaded=True
        self.pending=None
        self.history.clear()

    async def set_voice(self, voice):
        async with self.lock:
            await self._set_voice(voice)
            return self.voice

    def _reply(self, answer, action, language, **extra):
        # Snapshot under the turn lock: a later UI change cannot alter this utterance.
        return {'answer':answer, 'action':action, 'language':language, 'voice':self.voice, **extra}

    async def _load_mode(self):
        if not self.mode_loaded:
            self.model.mode=await self.store.get_mode()
            self.mode_loaded=True

    async def get_mode(self):
        async with self.lock:
            await self._load_mode()
            return self.model.mode

    async def _set_mode(self, mode):
        validate_mode(mode)
        await self.store.set_mode(mode)
        self.model.mode=mode
        self.mode_loaded=True
        self.pending=None
        self.history.clear()

    async def set_mode(self, mode):
        async with self.lock:
            await self._set_mode(mode)
            return self.model.mode

    async def list(self):
        async with self.lock:
            return [public_fact(doc) for doc in await self.store.list()]

    async def delete_one(self,identifier,etag):
        async with self.lock:
            doc=await self.store.get(identifier)
            if not doc or doc.get('_etag')!=etag:
                raise ValueError('Memory changed or no longer exists; refresh before deleting')
            self.history.clear(); self.pending=None
            await self.store.delete([{'id':identifier,'_etag':etag}])

    async def candidates(self,intent):
        if intent.get('slot'):
            doc=await self.store.get(fact_id(intent['slot']))
            return [doc] if doc else []
        query=intent.get('query','')
        vector=await asyncio.to_thread(self.embedder.embed,query,True)
        candidates=await self.store.search(vector)
        if not candidates:
            return []
        selected=await self.model.select(query,candidates)
        return [doc for doc in candidates if doc['id'] in selected]

    async def respond(self,text,language=None):
        if not isinstance(text,str) or not 1<=len(text.strip())<=1000:
            raise ValueError('Use a request of 1-1000 characters')
        async with self.lock:
            await self._load_mode()
            await self._load_voice()
            now=time.monotonic()
            if now-self.last_turn>600:
                self.history.clear()
            if self.pending and now>=self.pending['expires']:
                self.pending=None
            intent=validate_intent(quick_intent(text,bool(self.pending)) or await self.model.analyze(text,bool(self.pending)))
            language=language if language in LANGUAGES else intent.get('language','en-IN')
            if language not in LANGUAGES:
                language='en-IN'
            action=intent['action']
            if action not in ('confirm','cancel'):
                self.pending=None
            if action=='choose_voice':
                answer=await self.model.say('No voice change was made. Ask whether the user wants a male or female voice. They can say "Change to a female voice" or "Change to a male voice".',language)
                return self._reply(answer, action, language)
            if action=='set_voice':
                await self._set_voice(intent['voice'])
                answer=await self.model.say(f'Verified switched to the {self.voice} speaking voice for the household. Personality mode and memories are unchanged.',language)
                return self._reply(answer, action, language)
            if action=='set_mode':
                await self._set_mode(intent['mode'])
                label='Gen Z' if self.model.mode=='genz' else 'Normal'
                answer=await self.model.say(f'Verified switched to {label} mode for all household phones. Saved memories are unchanged.',language)
                return self._reply(answer, action, language, mode=self.model.mode)
            if action=='remember':
                if intent.get('sensitive') or SECRET.search(text) or any(SECRET.search(f['text']+' '+f['slot']) for f in intent['facts']):
                    answer=await self.model.say('Nothing was saved. Shared household memory is not a safe place for passwords, PINs, or financial credentials.',language)
                    return self._reply(answer, 'refused', language)
                existing=await self.store.list()
                slots={doc['slot']:doc for doc in existing}
                if len(slots)+sum(f['slot'] not in slots for f in intent['facts'])>MAX_FACTS:
                    answer=await self.model.say('Memory is full. Nothing was saved. Forget some facts before adding more.',language)
                    return self._reply(answer, 'full', language)
                stamp=datetime.now(timezone.utc).isoformat()
                documents=[]
                for fact in intent['facts']:
                    vector=await asyncio.to_thread(self.embedder.embed,fact['text'])
                    documents.append({'id':fact_id(fact['slot']),'household':self.store.household,'kind':'fact',
                        'slot':fact['slot'],'text':fact['text'].strip(),'embedding':vector,
                        'embedding_model':'multilingual-e5-small-384-v1',
                        'created_at':slots.get(fact['slot'],{}).get('created_at',stamp),'updated_at':stamp})
                await self.store.put(documents)
                for doc in documents:
                    stored=await self.store.get(doc['id'])
                    if not stored or stored['text']!=doc['text']:
                        raise RuntimeError('Memory write verification failed')
                self.history.clear()  # Updated facts must not compete with old conversation text.
                answer=await self.model.say('Verified saved to shared household memory: '+str([f['text'] for f in documents]),language)
            elif action in ('forget','forget_all'):
                targets=await self.store.list() if action=='forget_all' else await self.candidates(intent)
                if not targets:
                    answer=await self.model.say('No matching saved memory was found. Nothing was deleted.',language)
                elif len(targets)>3 and action!='forget_all':
                    answer=await self.model.say('Several memories matched. Please specify exactly what to forget, or review the Memory panel. Nothing was deleted.',language)
                else:
                    self.pending={'targets':[{'id':d['id'],'_etag':d['_etag']} for d in targets],
                                  'expires':time.monotonic()+90}
                    scope=f'ALL {len(targets)} saved household facts' if action=='forget_all' else str([d['text'] for d in targets])
                    answer=await self.model.say('Nothing deleted yet. Ask permission to delete '+scope+'. Tell the user to say "confirm forget" within 90 seconds, or say cancel.',language)
            elif action=='confirm':
                pending=self.pending; self.pending=None
                if not pending:
                    answer=await self.model.say('There is no pending deletion. Please say what to forget first.',language)
                else:
                    self.history.clear()
                    try:
                        await self.store.delete(pending['targets'])
                    except Exception:
                        # A network error or changed ETag must never sound like successful erasure.
                        answer=await self.model.say('Deletion could not be fully verified. Some memories may remain. Please review memory and ask to forget again.',language)
                        return self._reply(answer, 'delete_failed', language)
                    answer=await self.model.say(f'Verified deleted {len(pending["targets"])} saved facts and their search vectors from live shared memory.',language)
                    # Do not retain a deletion preview or forgotten text in short-term context.
                    self.last_turn=time.monotonic()
                    return self._reply(answer, 'forgotten', language)
            elif action=='cancel':
                self.pending=None
                answer=await self.model.say('Deletion cancelled. Nothing was deleted.',language)
            else:
                facts=await self.candidates(intent) if action=='recall' else []
                if action=='recall' and not facts:
                    answer=await self.model.say('That personal or household information is not saved in memory. Do not guess it.',language)
                else:
                    answer=await self.model.answer(text,language,facts,self.history,action=='recall')
            # Forget previews must not leave sensitive forgotten content in transcript caches.
            if action not in ('forget','forget_all','cancel'):
                self.history=(self.history+[{'user':text,'assistant':answer}])[-4:]
            self.last_turn=time.monotonic()
            return self._reply(answer, action, language)
