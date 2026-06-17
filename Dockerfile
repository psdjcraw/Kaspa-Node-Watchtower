FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        procps \
        tini \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -r requirements.txt

COPY . .

RUN adduser --disabled-password --gecos "" --home /home/watchtower watchtower \
    && mkdir -p /config /state \
    && chown -R watchtower:watchtower /config /state /app

USER watchtower

VOLUME ["/config", "/state"]

ENTRYPOINT ["/usr/bin/tini", "--", "python", "/app/watchtower.py"]
CMD ["--version"]
