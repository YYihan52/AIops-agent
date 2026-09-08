# Tiny runnable image for the recommendation service.
# The service self-drives load (background thread), so once running the
# container behaves like a live workload under sustained traffic.
# Pure stdlib -> no pip install needed at build time.
FROM python:3.11-slim

WORKDIR /app
COPY *.py /app/

ENV PORT=8080 \
    LOAD_RPS=200 \
    PYTHONUNBUFFERED=1

EXPOSE 8080
CMD ["python", "recommendation_server.py"]
