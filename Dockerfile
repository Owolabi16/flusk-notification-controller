FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY notification-controller/controller/ ./controller/

# Run as non-root user
RUN useradd -m -u 1000 flusk && chown -R flusk:flusk /app
USER flusk

# Run the controller
CMD ["python", "-u", "controller/main.py"]