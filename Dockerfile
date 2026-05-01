FROM python:3.11.15-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential gosu ruby-full \
    && gem install --no-document anystyle -v 1.6.0 \
    && gem install --no-document anystyle-cli -v 1.5.0 \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p \
        /srv/reference_gen2/runtime/security \
        /srv/reference_gen2/runtime/reports \
        /srv/reference_gen2/runtime/jobs \
        /srv/reference_gen2/runtime/uploads \
    && chown -R appuser:appuser /srv/reference_gen2 \
    && chmod 700 \
        /srv/reference_gen2/runtime \
        /srv/reference_gen2/runtime/security \
        /srv/reference_gen2/runtime/reports \
        /srv/reference_gen2/runtime/jobs \
        /srv/reference_gen2/runtime/uploads

WORKDIR /app

COPY constraints.txt requirements.txt pyproject.toml README.md /app/
COPY docker/entrypoint.sh /usr/local/bin/reference-gen2-entrypoint
COPY reference_gen2 /app/reference_gen2
COPY scripts /app/scripts

RUN python -m pip install --upgrade pip setuptools wheel \
    && python -m pip install -c constraints.txt . \
    && chmod 755 /usr/local/bin/reference-gen2-entrypoint

ENV REFERENCE_GEN2_REPORT_SERVING_TMP_DIR=/srv/reference_gen2/runtime/reports
ENV REFERENCE_GEN2_REPORT_SERVING_JOB_DIR=/srv/reference_gen2/runtime/jobs
ENV REFERENCE_GEN2_API_SECURITY_STATE_DB_PATH=/srv/reference_gen2/runtime/security/phase7.sqlite3
ENV REFERENCE_GEN2_UPLOAD_TMP_DIR=/srv/reference_gen2/runtime/uploads

USER appuser

ENTRYPOINT ["reference-gen2-entrypoint"]
CMD ["python", "-m", "uvicorn", "reference_gen2.api.phase7_app:app", "--host", "0.0.0.0", "--port", "8000"]
