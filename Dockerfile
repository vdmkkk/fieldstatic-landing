FROM python:3.12-alpine

WORKDIR /app
COPY server/app.py /app/app.py
COPY site/ /app/site/

ENV STATIC_DIR=/app/site \
    DB_PATH=/data/waitlist.db \
    PORT=8080

EXPOSE 8080
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD wget -qO- http://127.0.0.1:8080/healthz || exit 1

CMD ["python", "-u", "/app/app.py"]
