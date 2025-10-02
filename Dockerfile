FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY notification-controller/controller/ ./controller

RUN useradd -m -u 1000 flusk && chown -R flusk:flusk /app
USER flusk

CMD ["python", "-u", "controller/main.py"]