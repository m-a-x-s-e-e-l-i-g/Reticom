FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    HF_HOME=/var/lib/reticom/models

WORKDIR /app
RUN useradd --create-home --uid 10001 reticom \
    && mkdir -p /var/lib/reticom \
    && chown reticom:reticom /var/lib/reticom

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt faster-whisper==1.2.1
COPY src/ ./src/
COPY docker/entrypoint.py ./docker/entrypoint.py
COPY LICENSE ./LICENSE

USER reticom
EXPOSE 8780 4242
HEALTHCHECK --interval=15s --timeout=5s --start-period=45s --retries=4 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8780/api/health', timeout=4)"
ENTRYPOINT ["python", "/app/docker/entrypoint.py"]
CMD ["gateway"]
