FROM python:3.11-slim

WORKDIR /app

# Install Flask
RUN pip install --no-cache-dir flask==3.0.0

# Copy application
COPY app.py .

# Expose port
EXPOSE 8080

# Run the application
CMD ["python", "app.py"]