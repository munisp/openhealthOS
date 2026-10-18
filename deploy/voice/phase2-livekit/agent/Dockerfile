# Phase 2 agent image. Apache-2.0/MIT dependencies only; nothing vendored.
FROM python:3.12-slim

RUN pip install --no-cache-dir \
      "livekit-agents[stt,tts,silero]~=1.0" \
      livekit-plugins-silero~=1.0

# Same tested policy core as Phase 1, bind-mounted from the platform repo.
ENV VOICEBRIDGE_PATH=/opt/voicebridge

COPY agent.py /opt/agent.py
CMD ["python3", "/opt/agent.py", "start"]
