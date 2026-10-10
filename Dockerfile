FROM python:3.11-slim-bookworm@sha256:2333bd330d12de02514770b3585cad313644316047cdee24a7acfdece6de6efb

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    LANG=C.UTF-8 \
    HOME=/tmp \
    RESUME_DATA_DIR=/data

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates texlive-xetex texlive-lang-chinese texlive-latex-extra \
      texlive-fonts-recommended poppler-utils \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 resume \
    && useradd --uid 10001 --gid resume --no-create-home --home-dir /tmp resume \
    && install -d -o resume -g resume /data /app

# Debian lists TeX Gyre as a recommendation; the resume template requires it.
RUN apt-get update && apt-get install -y --no-install-recommends tex-gyre fonts-texgyre \
    && rm -rf /var/lib/apt/lists/*

COPY deploy/install_fontawesome.py /tmp/install_fontawesome.py
COPY deploy/vendor/fontawesome5-5.15.4.zip /tmp/fontawesome5.zip
RUN python /tmp/install_fontawesome.py && mktexlsr && updmap-sys --enable Map=fontawesome5.map \
    && rm /tmp/install_fontawesome.py /tmp/fontawesome5.zip

WORKDIR /app
COPY requirements.txt requirements-sms.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt -r requirements-sms.txt
COPY *.py AGENTS.md ./
COPY backend/ ./backend/
COPY frontend/ ./frontend/
COPY 模板/ ./模板/
COPY deploy/check_runtime.py ./deploy/check_runtime.py

USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"
CMD ["python", "-m", "uvicorn", "backend.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
