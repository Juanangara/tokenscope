FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY demo ./demo

# /data holds the learned drift reference; it is created here, owned by the service user, so a
# volume mounted on it is writable too.
RUN useradd --system --no-create-home tokenscope \
    && mkdir /data \
    && chown tokenscope /data
USER tokenscope

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
