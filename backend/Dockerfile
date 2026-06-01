FROM python:3.13-slim

WORKDIR /app

# Install gosu and qobuz-cli
RUN apt-get update && apt-get install -y --no-install-recommends gosu && \
    rm -rf /var/lib/apt/lists/* && \
    pip install --no-cache-dir qobuz-cli

# Install backend dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend application and entrypoint script
COPY app.py .
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

# Create configuration and downloads directories
RUN mkdir -p /config /downloads

# Expose port
EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]

# Run the FastAPI application
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]
