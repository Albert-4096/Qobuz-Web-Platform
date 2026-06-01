FROM python:3.13-slim

WORKDIR /app

# Install qobuz-cli for the download functionality globally
RUN pip install --no-cache-dir qobuz-cli

# Install backend dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend application
COPY app.py .

# Create configuration and downloads directories with broad permissions
RUN mkdir -p /config /downloads && chmod 777 /config /downloads

# Expose port
EXPOSE 8000

# Run the FastAPI application
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]
