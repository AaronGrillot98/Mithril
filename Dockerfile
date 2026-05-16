# --- builder ----------------------------------------------------------------
FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install --no-install-recommends -y \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install into a self-contained venv we can copy into the final image.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY pyproject.toml ./
COPY mithril ./mithril

RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir .

# --- runtime ----------------------------------------------------------------
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="Mithril"
LABEL org.opencontainers.image.description="A firewall for LLMs — block prompt injection, jailbreaks, and PII exfiltration."
LABEL org.opencontainers.image.source="https://github.com/AaronGrillot98/mithril"
LABEL org.opencontainers.image.licenses="Apache-2.0"

# Run as a non-root user.
RUN groupadd --system mithril && useradd --system --gid mithril --home /home/mithril --create-home mithril

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /data
RUN chown mithril:mithril /data
USER mithril

EXPOSE 8080

ENV MITHRIL_HOST=0.0.0.0 \
    MITHRIL_PORT=8080 \
    MITHRIL_DB_PATH=/data/mithril.db

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3).read(); sys.exit(0)" \
        || exit 1

ENTRYPOINT ["mithril"]
CMD ["serve"]
