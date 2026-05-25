FROM python:3.13-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy server code
COPY mcp_server.py .

# Expose port
EXPOSE 8086

# Run the MCP server
CMD ["python", "mcp_server.py"]
