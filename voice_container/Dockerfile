FROM python:3.12-slim

WORKDIR /app

# Install ffmpeg and curl
RUN apt-get update && \
    apt-get install -y git ffmpeg curl && \
    rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
COPY uv.lock .
COPY voice_container /app/voice_container
COPY cobot /app/cobot

RUN curl -Ls https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"
RUN uv pip install --system .

CMD ["uv", "run", "python", "-m", "cobot.voice_bot"]
