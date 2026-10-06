"""Multilingual E5 embeddings, entirely inside the hub; no external embedding API."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import threading
import urllib.request
import numpy as np

MODEL = "intfloat/multilingual-e5-small"
REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
DIMENSIONS = 384
FILES = {"model.onnx":"onnx/model_qint8_avx512_vnni.onnx", "tokenizer.json":"tokenizer.json"}


def download():
    root = Path(os.getenv("JARVIS_EMBEDDING_DIR", ".tmp_memory_model"))
    root.mkdir(parents=True, exist_ok=True)
    records=[]
    for name, remote in FILES.items():
        path=root/name
        if not path.exists():
            print("Downloading embedding asset:", name, flush=True)
            with urllib.request.urlopen(f"https://huggingface.co/{MODEL}/resolve/{REVISION}/{remote}",timeout=120) as source, path.with_suffix('.partial').open('wb') as target:
                shutil.copyfileobj(source,target)
            path.with_suffix('.partial').replace(path)
        with path.open('rb') as stream:
            digest=hashlib.file_digest(stream,'sha256').hexdigest()
        records.append({"file":name,"sha256":digest})
    (root/'manifest.json').write_text(json.dumps({"model":MODEL,"revision":REVISION,"license":"MIT","files":records},indent=2))
    print("Embedding model ready:",MODEL,flush=True)


class Embedder:
    def __init__(self, directory=None):
        import onnxruntime as ort
        from tokenizers import Tokenizer
        root=Path(directory or os.getenv("JARVIS_EMBEDDING_DIR", ".tmp_memory_model"))
        self.tokenizer=Tokenizer.from_file(str(root/'tokenizer.json'))
        self.tokenizer.enable_truncation(max_length=512)
        options=ort.SessionOptions()
        options.intra_op_num_threads=1
        options.inter_op_num_threads=1
        self.session=ort.InferenceSession(str(root/'model.onnx'),sess_options=options,providers=['CPUExecutionProvider'])
        self.lock=threading.Lock()

    def embed(self,text,query=False):
        with self.lock:
            encoded=self.tokenizer.encode(('query: ' if query else 'passage: ')+text)
            ids=np.asarray([encoded.ids],dtype=np.int64)
            mask=np.asarray([encoded.attention_mask],dtype=np.int64)
            inputs={'input_ids':ids,'attention_mask':mask}
            if any(item.name=='token_type_ids' for item in self.session.get_inputs()):
                inputs['token_type_ids']=np.zeros_like(ids)
            hidden=self.session.run(None,inputs)[0]
            if hidden.ndim==3:
                hidden=(hidden*mask[...,None]).sum(axis=1)/np.maximum(mask.sum(axis=1,keepdims=True),1)
            vector=hidden[0].astype(np.float32)
            if vector.shape!=(DIMENSIONS,) or not np.all(np.isfinite(vector)):
                raise RuntimeError('Embedding model returned invalid dimensions')
            vector/=max(float(np.linalg.norm(vector)),1e-12)
            return vector.tolist()


if __name__=='__main__':
    download()
