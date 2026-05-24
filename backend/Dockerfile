FROM python:3.13-slim

WORKDIR /app

# Install pipx and qobuz-cli for the download functionality
RUN pip install --no-cache-dir pipx && \
    pipx install qobuz-cli && \
    ln -s /root/.local/bin/qcli /usr/local/bin/qcli

# Install backend dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend application
COPY app.py .

# Create downloads directory
RUN mkdir -p /downloads

# Expose port
EXPOSE 8000

# Run the FastAPI application
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]
