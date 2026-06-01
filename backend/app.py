"""
Qobuz Search & Download Web Service - Backend API

A FastAPI service that wraps the Qobuz API for searching tracks/albums/artists
and manages downloads via qobuz-cli (qcli).
"""

import asyncio
import configparser
import hashlib
import logging
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from jose import JWTError, jwt
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("qobuz-service")

QOBUZ_API_BASE = "https://www.qobuz.com/api.json/0.2/"
DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", "/downloads")
CONFIG_PATH = os.environ.get(
    "QOBUZ_CONFIG", "/config/.config/qobuz-cli/config.ini"
)

# Quality mapping: user-friendly ID -> Qobuz format_id
QUALITY_MAP = {
    1: 5,   # MP3 320
    2: 6,   # CD (16/44.1)
    3: 7,   # Hi-Res (24/96)
    4: 27,  # Hi-Res+ (24/192)
}

QUALITY_LABELS = {
    1: "MP3 320kbps",
    2: "CD Quality (16-bit/44.1kHz)",
    3: "Hi-Res (24-bit/96kHz)",
    4: "Hi-Res+ (24-bit/192kHz)",
}

# ---------------------------------------------------------------------------
# Auth configuration
# ---------------------------------------------------------------------------

APP_USERNAME = os.environ.get("APP_USERNAME", "")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
JWT_SECRET = os.environ.get("JWT_SECRET", "")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_DAYS = 30

AUTH_ENABLED = bool(APP_USERNAME and APP_PASSWORD and JWT_SECRET)

_PUBLIC_PATHS = {"/api/auth/login", "/api/auth/status", "/api/health", "/health"}


def _create_token() -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=JWT_EXPIRE_DAYS)
    return jwt.encode({"sub": APP_USERNAME, "exp": expire}, JWT_SECRET, algorithm=JWT_ALGORITHM)


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

class QobuzConfig:
    """Loads and holds qobuz-cli configuration."""

    def __init__(self, config_path: str):
        self.app_id: str = ""
        self.secrets: list[str] = []
        self.token: str = ""
        self.email: str = ""
        self.password: str = ""
        self.quality: int = 2
        self.app_secret: str | None = None
        self._load(config_path)

    def _load(self, path: str) -> None:
        cfg = configparser.ConfigParser()
        cfg.read(path)
        section = cfg["DEFAULT"] if "DEFAULT" in cfg else cfg[cfg.sections()[0]]
        self.app_id = section.get("app_id", "")
        raw_secrets = section.get("secrets", "")
        self.secrets = [s.strip() for s in raw_secrets.split(",") if s.strip()]
        self.token = section.get("token", "")
        self.email = section.get("email", "")
        self.password = section.get("password", "")
        self.quality = int(section.get("quality", "2"))
        log.info(
            f"Config loaded: app_id={self.app_id[:4]}***, "
            f"secrets={len(self.secrets)}, "
            f"token={'set' if self.token else 'unset'}, "
            f"quality={self.quality}"
        )


# ---------------------------------------------------------------------------
# Qobuz API client (async, using httpx)
# ---------------------------------------------------------------------------

class QobuzAPI:
    """Lightweight async client for the Qobuz API."""

    def __init__(self, config: QobuzConfig):
        self.config = config
        self._client: httpx.AsyncClient | None = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=QOBUZ_API_BASE,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) "
                        "Gecko/20100101 Firefox/121.0"
                    ),
                    "X-App-Id": self.config.app_id,
                },
                timeout=30.0,
            )
        return self._client

    async def _call(self, endpoint: str, **params: Any) -> dict:
        client = await self._ensure_client()
        if self.config.token:
            params["user_auth_token"] = self.config.token
        resp = await client.get(endpoint, params=params)
        if resp.status_code == 401:
            raise HTTPException(status_code=401, detail="Qobuz authentication failed")
        if resp.status_code == 429:
            raise HTTPException(status_code=429, detail="Qobuz rate limit exceeded")
        resp.raise_for_status()
        return resp.json()

    async def search(
        self, query: str, media_type: str = "tracks", limit: int = 20
    ) -> dict:
        """Search for tracks, albums, or artists."""
        type_map = {
            "tracks": "track/search",
            "albums": "album/search",
            "artists": "artist/search",
        }
        endpoint = type_map.get(media_type)
        if not endpoint:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid type: {media_type}. Use tracks, albums, or artists.",
            )
        return await self._call(endpoint, query=query, limit=min(limit, 50))

    async def get_album(self, album_id: str) -> dict:
        return await self._call("album/get", album_id=album_id)

    async def get_track(self, track_id: str) -> dict:
        return await self._call("track/get", track_id=track_id)

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()


# ---------------------------------------------------------------------------
# Download Manager
# ---------------------------------------------------------------------------

class DownloadStatus(BaseModel):
    id: str
    url: str
    status: str  # pending, downloading, completed, failed
    progress: float = 0.0
    title: str = ""
    error: str | None = None
    quality: int = 2
    quality_label: str = ""
    created_at: str = ""
    completed_at: str | None = None
    files: list[str] = []


class DownloadManager:
    """Manages download tasks using qcli subprocess calls."""

    def __init__(self, download_dir: str):
        self.download_dir = download_dir
        self.downloads: dict[str, DownloadStatus] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    def start_download(self, url: str, quality: int = 2) -> DownloadStatus:
        dl_id = str(uuid.uuid4())[:8]
        quality_label = QUALITY_LABELS.get(quality, "CD Quality")
        now = datetime.now(timezone.utc).isoformat()

        status = DownloadStatus(
            id=dl_id,
            url=url,
            status="pending",
            quality=quality,
            quality_label=quality_label,
            created_at=now,
        )
        self.downloads[dl_id] = status
        self._tasks[dl_id] = asyncio.create_task(self._run_download(dl_id))
        return status

    async def _run_download(self, dl_id: str) -> None:
        status = self.downloads[dl_id]
        status.status = "downloading"
        status.progress = 5.0  # Starting

        try:
            cmd = [
                "qcli", "download",
                "--quality", str(status.quality),
                "--embed-art",
                "--archive",
                status.url,
            ]
            log.info(f"[{dl_id}] Starting download: {' '.join(cmd)}")

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env={**os.environ, "TERM": "dumb", "NO_COLOR": "1"},
                cwd=self.download_dir,
            )

            output_lines: list[str] = []
            while True:
                line_bytes = await proc.stdout.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                output_lines.append(line)
                log.info(f"[{dl_id}] {line}")

                # Try to extract track title from output
                if not status.title:
                    for prefix in ("Downloading: ", "Track: ", "  "):
                        if line.startswith(prefix) and len(line) > len(prefix) + 3:
                            status.title = line[len(prefix):].strip()
                            break

                # Update progress based on output patterns
                if "downloading" in line.lower() or "track" in line.lower():
                    status.progress = min(status.progress + 10, 90)
                if "%" in line:
                    # Try to extract percentage
                    try:
                        for part in line.split():
                            if "%" in part:
                                pct = float(part.replace("%", "").strip())
                                if 0 <= pct <= 100:
                                    status.progress = pct
                                    break
                    except (ValueError, IndexError):
                        pass

            return_code = await proc.wait()

            if return_code == 0:
                status.status = "completed"
                status.progress = 100.0
                status.completed_at = datetime.now(timezone.utc).isoformat()
                # Discover downloaded files
                status.files = self._scan_recent_files()
                log.info(f"[{dl_id}] Download completed successfully")
            else:
                status.status = "failed"
                status.error = (
                    f"qcli exited with code {return_code}. "
                    + (output_lines[-1] if output_lines else "No output")
                )
                log.error(f"[{dl_id}] Download failed: {status.error}")

        except Exception as e:
            status.status = "failed"
            status.error = str(e)
            log.exception(f"[{dl_id}] Download error")

    def _scan_recent_files(self) -> list[str]:
        """Scan download directory for recently created files."""
        files = []
        base = Path(self.download_dir)
        if base.exists():
            for f in base.rglob("*"):
                if f.is_file() and not f.name.startswith("."):
                    files.append(str(f.relative_to(base)))
        return files[-20:]  # Return last 20

    def get_all_downloads(self) -> list[DownloadStatus]:
        return sorted(
            self.downloads.values(),
            key=lambda d: d.created_at,
            reverse=True,
        )

    def get_download(self, dl_id: str) -> DownloadStatus | None:
        return self.downloads.get(dl_id)

    def cancel_download(self, dl_id: str) -> bool:
        task = self._tasks.get(dl_id)
        if task and not task.done():
            task.cancel()
            if dl_id in self.downloads:
                self.downloads[dl_id].status = "failed"
                self.downloads[dl_id].error = "Cancelled by user"
            return True
        return False


# ---------------------------------------------------------------------------
# FastAPI Application
# ---------------------------------------------------------------------------

app = FastAPI(title="Qobuz Service", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    if not AUTH_ENABLED or request.url.path in _PUBLIC_PATHS:
        return await call_next(request)

    token: str | None = None
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
    else:
        # Allow token as query param for file serving / audio streaming
        token = request.query_params.get("token")

    if not token:
        return JSONResponse(status_code=401, content={"detail": "Not authenticated"})

    try:
        jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError:
        return JSONResponse(status_code=401, content={"detail": "Invalid or expired token"})

    return await call_next(request)

# Globals initialized on startup
qobuz_api: QobuzAPI | None = None
download_manager: DownloadManager | None = None


@app.on_event("startup")
async def startup() -> None:
    global qobuz_api, download_manager
    try:
        config = QobuzConfig(CONFIG_PATH)
        qobuz_api = QobuzAPI(config)
        download_manager = DownloadManager(DOWNLOAD_DIR)
        log.info("Qobuz service started successfully")
    except Exception as e:
        log.error(f"Failed to initialize: {e}")
        raise


@app.on_event("shutdown")
async def shutdown() -> None:
    if qobuz_api:
        await qobuz_api.close()


# --- Health ---

@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


# --- Auth ---

class LoginRequest(BaseModel):
    username: str
    password: str


@app.get("/api/auth/status")
async def auth_status() -> dict:
    return {"auth_enabled": AUTH_ENABLED}


@app.post("/api/auth/login")
async def login(req: LoginRequest) -> dict:
    if not AUTH_ENABLED:
        raise HTTPException(status_code=503, detail="Authentication not configured")
    if req.username != APP_USERNAME or req.password != APP_PASSWORD:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return {"token": _create_token(), "username": APP_USERNAME}


# --- Search ---

@app.get("/api/search")
async def search(
    q: str = Query(..., min_length=1, description="Search query"),
    type: str = Query("tracks", description="Search type: tracks, albums, artists"),
    limit: int = Query(20, ge=1, le=50, description="Number of results"),
) -> dict:
    """Search Qobuz for tracks, albums, or artists."""
    if not qobuz_api:
        raise HTTPException(status_code=503, detail="Service not initialized")
    return await qobuz_api.search(q, type, limit)


# --- Album details ---

@app.get("/api/album/{album_id}")
async def get_album(album_id: str) -> dict:
    """Get album details including track list."""
    if not qobuz_api:
        raise HTTPException(status_code=503, detail="Service not initialized")
    return await qobuz_api.get_album(album_id)


# --- Track details ---

@app.get("/api/track/{track_id}")
async def get_track(track_id: str) -> dict:
    """Get track details."""
    if not qobuz_api:
        raise HTTPException(status_code=503, detail="Service not initialized")
    return await qobuz_api.get_track(track_id)


# --- Downloads ---

class DownloadRequest(BaseModel):
    url: str
    quality: int = 2


@app.post("/api/download")
async def start_download(req: DownloadRequest) -> dict:
    """Start a new download."""
    if not download_manager:
        raise HTTPException(status_code=503, detail="Service not initialized")
    if req.quality not in QUALITY_MAP:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid quality: {req.quality}. Must be 1-4.",
        )
    status = download_manager.start_download(req.url, req.quality)
    return status.model_dump()


@app.get("/api/downloads")
async def list_downloads() -> dict:
    """List all downloads."""
    if not download_manager:
        raise HTTPException(status_code=503, detail="Service not initialized")
    downloads = download_manager.get_all_downloads()
    return {"downloads": [d.model_dump() for d in downloads]}


@app.get("/api/downloads/{dl_id}")
async def get_download_status(dl_id: str) -> dict:
    """Get status of a specific download."""
    if not download_manager:
        raise HTTPException(status_code=503, detail="Service not initialized")
    status = download_manager.get_download(dl_id)
    if not status:
        raise HTTPException(status_code=404, detail="Download not found")
    return status.model_dump()


@app.delete("/api/downloads/{dl_id}")
async def cancel_download(dl_id: str) -> dict:
    """Cancel a download."""
    if not download_manager:
        raise HTTPException(status_code=503, detail="Service not initialized")
    cancelled = download_manager.cancel_download(dl_id)
    if not cancelled:
        raise HTTPException(
            status_code=404, detail="Download not found or already completed"
        )
    return {"status": "cancelled"}


# --- Library (browse downloaded files) ---

@app.get("/api/library")
async def list_library() -> dict:
    """List all downloaded files (limited to 5000)."""
    files = []
    base = Path(DOWNLOAD_DIR)
    if base.exists():
        for f in sorted(base.rglob("*")):
            if f.is_file() and not f.name.startswith("."):
                stat = f.stat()
                files.append({
                    "path": str(f.relative_to(base)),
                    "name": f.name,
                    "size": stat.st_size,
                    "modified": datetime.fromtimestamp(
                        stat.st_mtime, tz=timezone.utc
                    ).isoformat(),
                })
                if len(files) >= 5000:
                    break
    return {"files": files}


@app.get("/api/files/{file_path:path}")
async def serve_file(file_path: str) -> FileResponse:
    """Serve a downloaded file for playback or download."""
    full_path = Path(DOWNLOAD_DIR) / file_path
    if not full_path.exists() or not full_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    # Security: prevent path traversal
    try:
        full_path.resolve().relative_to(Path(DOWNLOAD_DIR).resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied")
    return FileResponse(
        path=str(full_path),
        filename=full_path.name,
        media_type="application/octet-stream",
    )
