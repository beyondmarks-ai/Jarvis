FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libgomp1 && rm -rf /var/lib/apt/lists/*
COPY phone_mics/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY phone_mics /app/phone_mics
COPY realtime_relay/app.py /app/realtime_relay/app.py
ENV JARVIS_MODEL_DIR=/app/models
RUN python -m phone_mics.download_models
ENV JARVIS_EMBEDDING_DIR=/app/memory-model
RUN python -m phone_mics.memory_embeddings
EXPOSE 8000
CMD ["uvicorn","phone_mics.app:app","--host","0.0.0.0","--port","8000","--workers","1","--ws-max-size","4096","--ws-max-queue","4","--no-access-log"]
