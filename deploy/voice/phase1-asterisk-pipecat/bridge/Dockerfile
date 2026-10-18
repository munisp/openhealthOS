# Phase 1 bridge image. All dependencies are OSI-approved (MIT/BSD class);
# none is vendored into this repo — the image build pulls pinned versions.
FROM python:3.12-slim

RUN pip install --no-cache-dir \
      pipecat-ai==0.0.* \
      faster-whisper==1.* \
      wyoming==1.*            # Piper wire protocol client

# The tested policy core is bind-mounted from the platform repo so bridge
# behaviour upgrades ship with platform releases, not image rebuilds:
#   docker compose run -v ../../packages/voicebridge:/opt/voicebridge ...
ENV VOICEBRIDGE_PATH=/opt/voicebridge

COPY bridge.py /opt/bridge.py
CMD ["python3", "/opt/bridge.py"]
