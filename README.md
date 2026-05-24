# Qobuz Downloader Service

A containerized application that wraps `qobuz-cli` with a modern, glassmorphism-inspired web interface to easily search, download, and play Hi-Res music from Qobuz.

## Architecture

- **Frontend Container (`nginx:alpine`)**: Serves the static HTML/CSS/JS and acts as a reverse proxy via `localhost:8085`.
- **Backend Container (`python:3.13-slim`)**: Runs the FastAPI application on `localhost:8000` to interact with Qobuz and manage background `qcli` downloads.
- **Docker Compose** — Orchestrates both services using host networking (`network_mode: "host"`) to ensure proper DNS resolution and access to the internet.

## Prerequisites

- Docker and Docker Compose installed
- `qobuz-cli` installed and configured on the host (`~/.config/qobuz-cli/config.ini` MUST exist)

## Getting Started

1. Build and start the services:

```bash
docker compose up -d --build
```

2. Once the containers are running, open your browser and navigate to:
**http://localhost:8085**

## Features

- **Search**: Search for Tracks, Albums, and Artists
- **Download**: Choose audio quality (from MP3 to Hi-Res+) and trigger downloads directly from the UI
- **Active Downloads**: Monitor download progress and status in real-time
- **Library**: Browse downloaded files, play them in the browser, or download them locally

## Configuration

Downloads are saved by default to the `./downloads` directory inside this project folder.
The Qobuz credentials are read directly from your host's `~/.config/qobuz-cli` configuration folder, which is mounted into the container as read-only.
