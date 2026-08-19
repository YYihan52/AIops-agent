# s4 capstone: tiny runnable image for the recommendation service.
# The leak revision self-drives load (background thread), so once running the
# container's RSS climbs monotonically until the memory limit OOM-kills it.
# Pure stdlib -> no pip install needed at build time.
FROM python:3.11-slim

WORKDIR /app
COPY recommendation_server.py ranking.py /app/

ENV PORT=8080 \
    LOAD_RPS=200 \
    PYTHONUNBUFFERED=1

EXPOSE 8080
CMD ["python", "recommendation_server.py"]
