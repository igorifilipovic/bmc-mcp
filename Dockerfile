FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY bmc_mcp ./bmc_mcp

RUN useradd --system --uid 10001 --no-create-home mcp
USER 10001

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import ssl,urllib.request;urllib.request.urlopen('https://127.0.0.1:8001/healthz',context=ssl._create_unverified_context(),timeout=3)" || exit 1

CMD ["python", "-m", "bmc_mcp.server"]
