import os
import sys
import logging
from typing import Optional
import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
import uvicorn

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("qobuz-mcp-server")

# Base API URL configuration
# Default to backend:8000 since we run inside the Docker Compose network
API_URL = os.environ.get("QOBUZ_API_URL", "http://backend:8000/api").rstrip("/")

# ---------------------------------------------------------------------------
# Transport Security Settings (DNS rebinding protection)
# ---------------------------------------------------------------------------
# ALLOWED_HOSTS: comma-separated list of Host header values to allow.
# When behind a reverse proxy (e.g. NGINX), add the public domain here so
# MCP 1.27+ DNS-rebinding protection doesn't reject requests with 421.
# Example: ALLOWED_HOSTS=qobuz-mcp.alberyt.xyz,localhost:8086
_allowed_hosts_env = os.environ.get("ALLOWED_HOSTS", "")
_allowed_hosts = [h.strip() for h in _allowed_hosts_env.split(",") if h.strip()]

if _allowed_hosts:
    _transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_allowed_hosts,
    )
    logger.info(f"DNS rebinding protection ENABLED. Allowed hosts: {_allowed_hosts}")
else:
    _transport_security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    logger.warning("ALLOWED_HOSTS not set — DNS rebinding protection DISABLED.")

# Initialize MCP Server
mcp = FastMCP(
    "Qobuz Downloader",
    transport_security=_transport_security,
)

# Helper function to get httpx async client with base url
def get_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=API_URL, timeout=30.0)

async def handle_api_request(method: str, path: str, **kwargs) -> dict:
    """Helper to perform HTTP requests to the backend API with error handling."""
    async with get_client() as client:
        try:
            response = await client.request(method, path, **kwargs)
            if response.status_code == 404:
                return {"error": f"Resource not found at {path}"}
            response.raise_for_status()
            return response.json()
        except httpx.ConnectError:
            logger.error(f"Could not connect to Qobuz Web Downloader service backend at {API_URL}.")
            return {
                "error": (
                    f"Could not connect to Qobuz service backend at {API_URL}. "
                    "Please ensure the backend container is running."
                )
            }
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error occurred: {e.response.text}")
            try:
                detail = e.response.json().get("detail", e.response.text)
            except Exception:
                detail = e.response.text
            return {"error": f"Backend API error: {detail}"}
        except Exception as e:
            logger.exception("Unexpected error in API request")
            return {"error": f"Unexpected error: {str(e)}"}

# ---------------------------------------------------------------------------
# Tool: Search Qobuz
# ---------------------------------------------------------------------------
@mcp.tool()
async def search_qobuz(query: str, media_type: str = "tracks", limit: int = 15) -> str:
    """
    Search Qobuz for tracks, albums, or artists.

    Args:
        query: The search query (e.g. track name, album title, artist name).
        media_type: Type of results: 'tracks', 'albums', or 'artists'. Default is 'tracks'.
        limit: Max number of results (1 to 50). Default is 15.
    """
    if media_type not in ("tracks", "albums", "artists"):
        return "Error: media_type must be one of: 'tracks', 'albums', or 'artists'."

    params = {"q": query, "type": media_type, "limit": limit}
    data = await handle_api_request("GET", "/search", params=params)

    if "error" in data:
        return data["error"]

    results = []
    if media_type == "tracks":
        items = data.get("tracks", {}).get("items", [])
        if not items:
            return f"No tracks found matching query '{query}'."

        results.append(f"### Track Search Results for '{query}'\n")
        results.append("| ID | Track Title | Artist | Album | Duration | Max Quality |")
        results.append("| --- | --- | --- | --- | --- | --- |")
        for item in items:
            track_id = item.get("id")
            title = item.get("title", "Unknown")
            artist = item.get("performer", {}).get("name", "Unknown")
            album = item.get("album", {}).get("title", "Unknown")
            duration_s = item.get("duration", 0)
            duration = f"{duration_s // 60}:{duration_s % 60:02d}"
            
            bit_depth = item.get("maximum_bit_depth", 16)
            sample_rate = item.get("maximum_sampling_rate", 44.1)
            quality = f"Hi-Res ({bit_depth}-bit/{sample_rate}kHz)" if bit_depth > 16 else "CD Quality"
            
            results.append(f"| `{track_id}` | **{title}** | {artist} | *{album}* | {duration} | {quality} |")

    elif media_type == "albums":
        items = data.get("albums", {}).get("items", [])
        if not items:
            return f"No albums found matching query '{query}'."

        results.append(f"### Album Search Results for '{query}'\n")
        results.append("| ID | Album Title | Artist | Tracks | Released | Max Quality |")
        results.append("| --- | --- | --- | --- | --- | --- |")
        for item in items:
            album_id = item.get("id")
            title = item.get("title", "Unknown")
            artist = item.get("artist", {}).get("name", "Unknown")
            tracks_count = item.get("tracks_count", 0)
            
            # Format release year
            released_at = item.get("released_at", 0)
            try:
                from datetime import datetime
                year = datetime.fromtimestamp(released_at).year if released_at else "N/A"
            except Exception:
                year = "N/A"
                
            bit_depth = item.get("maximum_bit_depth", 16)
            sample_rate = item.get("maximum_sampling_rate", 44.1)
            quality = f"Hi-Res ({bit_depth}-bit/{sample_rate}kHz)" if bit_depth > 16 else "CD Quality"
            
            results.append(f"| `{album_id}` | **{title}** | {artist} | {tracks_count} | {year} | {quality} |")

    elif media_type == "artists":
        items = data.get("artists", {}).get("items", [])
        if not items:
            return f"No artists found matching query '{query}'."

        results.append(f"### Artist Search Results for '{query}'\n")
        results.append("| ID | Artist Name | Albums Count |")
        results.append("| --- | --- | --- |")
        for item in items:
            artist_id = item.get("id")
            name = item.get("name", "Unknown")
            albums_count = item.get("albums_count", 0)
            results.append(f"| `{artist_id}` | **{name}** | {albums_count} |")

    return "\n".join(results)

# ---------------------------------------------------------------------------
# Tool: Get Album Details
# ---------------------------------------------------------------------------
@mcp.tool()
async def get_album_details(album_id: str) -> str:
    """
    Get detailed information about an album, including its tracklist and available quality.

    Args:
        album_id: The Qobuz Album ID (numerical string or UPC).
    """
    data = await handle_api_request("GET", f"/album/{album_id}")
    if "error" in data:
        return data["error"]

    title = data.get("title", "Unknown")
    artist = data.get("artist", {}).get("name", "Unknown")
    tracks_count = data.get("tracks_count", 0)
    upc = data.get("upc", "N/A")
    label = data.get("label", {}).get("name", "Unknown")
    
    bit_depth = data.get("maximum_bit_depth", 16)
    sample_rate = data.get("maximum_sampling_rate", 44.1)
    quality = f"Hi-Res ({bit_depth}-bit/{sample_rate}kHz)" if bit_depth > 16 else "CD Quality"
    
    results = [
        f"## Album Details: **{title}** by **{artist}**\n",
        f"- **Album ID:** `{album_id}` (UPC: {upc})",
        f"- **Publisher Label:** {label}",
        f"- **Max Quality Available:** {quality}",
        f"- **Tracks:** {tracks_count}\n",
        "### Track List",
        "| # | Track Title | Track ID | Duration | Streamable | Downloadable |",
        "| --- | --- | --- | --- | --- | --- |"
    ]
    
    tracks_data = data.get("tracks", {}).get("items", [])
    for track in tracks_data:
        num = track.get("track_number", 0)
        t_title = track.get("title", "Unknown")
        t_id = track.get("id")
        t_duration_s = track.get("duration", 0)
        t_duration = f"{t_duration_s // 60}:{t_duration_s % 60:02d}"
        streamable = "Yes" if track.get("streamable", False) else "No"
        downloadable = "Yes" if track.get("downloadable", False) else "No"
        
        results.append(f"| {num} | {t_title} | `{t_id}` | {t_duration} | {streamable} | {downloadable} |")

    return "\n".join(results)

# ---------------------------------------------------------------------------
# Tool: Get Track Details
# ---------------------------------------------------------------------------
@mcp.tool()
async def get_track_details(track_id: str) -> str:
    """
    Get details for a specific track.

    Args:
        track_id: The Qobuz Track ID (numerical string).
    """
    data = await handle_api_request("GET", f"/track/{track_id}")
    if "error" in data:
        return data["error"]

    title = data.get("title", "Unknown")
    artist = data.get("performer", {}).get("name", "Unknown")
    album = data.get("album", {}).get("title", "Unknown")
    album_id = data.get("album", {}).get("id")
    track_number = data.get("track_number", 0)
    duration_s = data.get("duration", 0)
    duration = f"{duration_s // 60}:{duration_s % 60:02d}"
    isrc = data.get("isrc", "N/A")
    downloadable = "Yes" if data.get("downloadable", False) else "No"
    
    results = [
        f"## Track Details: **{title}**\n",
        f"- **Artist:** {artist}",
        f"- **Album:** *{album}* (Album ID: `{album_id}`)",
        f"- **Track Number:** {track_number}",
        f"- **Duration:** {duration}",
        f"- **Track ID:** `{track_id}` (ISRC: {isrc})",
        f"- **Downloadable:** {downloadable}"
    ]
    return "\n".join(results)

# ---------------------------------------------------------------------------
# Tool: Download Music
# ---------------------------------------------------------------------------
@mcp.tool()
async def download_music(url_or_id: str, item_type: str = "album", quality: int = 4) -> str:
    """
    Download a track or album from Qobuz.

    Args:
        url_or_id: Either a direct Qobuz play URL (e.g. 'https://play.qobuz.com/album/...') OR a track/album ID.
        item_type: If using a raw ID, specify 'album' or 'track'. Default is 'album'.
        quality: Desired download quality:
                 1 = MP3 (320kbps)
                 2 = CD (16-bit / 44.1 kHz)
                 3 = Hi-Res (24-bit / 96 kHz)
                 4 = Hi-Res+ (24-bit / 192 kHz) - Default.
                 It automatically falls back to the maximum quality available for the item if higher.
    """
    if quality not in (1, 2, 3, 4):
        return "Error: quality must be an integer between 1 and 4."

    url = url_or_id.strip()
    
    # If not a URL, construct it using play.qobuz.com
    if not (url.startswith("http://") or url.startswith("https://")):
        if item_type not in ("album", "track"):
            return "Error: item_type must be either 'album' or 'track' when providing a raw ID."
        url = f"https://play.qobuz.com/{item_type}/{url}"

    payload = {"url": url, "quality": quality}
    data = await handle_api_request("POST", "/download", json=payload)

    if "error" in data:
        return data["error"]

    dl_id = data.get("id")
    status = data.get("status")
    title = data.get("title", "Initiating...")
    q_label = data.get("quality_label", "")

    quality_names = {
        1: "MP3 320kbps",
        2: "CD Quality",
        3: "Hi-Res (24/96)",
        4: "Hi-Res+ (24/192)",
    }
    req_quality = quality_names.get(quality, "Highest")

    return (
        f"### Download Queued Successfully!\n"
        f"- **Download ID:** `{dl_id}`\n"
        f"- **URL Queue:** {url}\n"
        f"- **Target Item:** {title}\n"
        f"- **Requested Quality:** {req_quality} ({q_label})\n"
        f"- **Status:** {status}\n\n"
        f"You can monitor the progress by calling `list_downloads` or `get_download_status(download_id=\"{dl_id}\")`."
    )

# ---------------------------------------------------------------------------
# Tool: List Downloads
# ---------------------------------------------------------------------------
@mcp.tool()
async def list_downloads() -> str:
    """List all recent and active downloads managed by the service backend."""
    data = await handle_api_request("GET", "/downloads")
    if "error" in data:
        return data["error"]

    downloads = data.get("downloads", [])
    if not downloads:
        return "No recent or active downloads found."

    results = [
        "## Recent & Active Downloads\n",
        "| ID | Title / URL | Status | Progress | Quality | Error |",
        "| --- | --- | --- | --- | --- | --- |"
    ]

    for dl in downloads:
        dl_id = dl.get("id")
        title = dl.get("title") or dl.get("url") or "Unknown"
        # truncate title if too long
        if len(title) > 50:
            title = title[:47] + "..."
            
        status = dl.get("status", "unknown")
        progress = f"{int(dl.get('progress', 0))}%"
        quality = dl.get("quality_label", "Unknown")
        err = dl.get("error") or ""
        
        status_str = status.upper()
        if status == "completed":
            status_str = "✅ Completed"
        elif status == "failed":
            status_str = "❌ Failed"
        elif status == "downloading":
            status_str = "⏳ Downloading"
        elif status == "pending":
            status_str = "⏱️ Pending"

        results.append(f"| `{dl_id}` | {title} | {status_str} | {progress} | {quality} | {err} |")

    return "\n".join(results)

# ---------------------------------------------------------------------------
# Tool: Get Download Status
# ---------------------------------------------------------------------------
@mcp.tool()
async def get_download_status(download_id: str) -> str:
    """
    Get detailed status of a specific download.

    Args:
        download_id: The short ID of the download (e.g. 'a1b2c3d4').
    """
    data = await handle_api_request("GET", f"/downloads/{download_id}")
    if "error" in data:
        return data["error"]

    dl_id = data.get("id")
    url = data.get("url")
    status = data.get("status", "unknown").upper()
    progress = f"{data.get('progress', 0)}%"
    title = data.get("title", "Unknown")
    err = data.get("error")
    quality = data.get("quality_label", "Unknown")
    created = data.get("created_at", "N/A")
    completed = data.get("completed_at", "N/A")
    files = data.get("files", [])

    results = [
        f"## Download Status: `{dl_id}`\n",
        f"- **Item Title/Target:** {title}",
        f"- **Source URL:** {url}",
        f"- **Status:** {status}",
        f"- **Progress:** {progress}",
        f"- **Quality Mode:** {quality}",
        f"- **Created At:** {created}",
        f"- **Completed At:** {completed}"
    ]

    if err:
        results.append(f"- **Error Details:** {err}")

    if files:
        results.append("\n### Downloaded Files:")
        for f in files:
            results.append(f"- `{f}`")

    return "\n".join(results)

# ---------------------------------------------------------------------------
# Tool: Cancel Download
# ---------------------------------------------------------------------------
@mcp.tool()
async def cancel_download(download_id: str) -> str:
    """
    Cancel an active or pending download.

    Args:
        download_id: The short ID of the download.
    """
    data = await handle_api_request("DELETE", f"/downloads/{download_id}")
    if "error" in data:
        return data["error"]

    return f"Download `{download_id}` has been cancelled successfully."

# ---------------------------------------------------------------------------
# Tool: Browse Library
# ---------------------------------------------------------------------------
@mcp.tool()
async def list_library() -> str:
    """List downloaded files present in the service library folder."""
    data = await handle_api_request("GET", "/library")
    if "error" in data:
        return data["error"]

    files = data.get("files", [])
    if not files:
        return "The library is currently empty. Start downloading some music!"

    library_tree = {}
    for file_info in files:
        path = file_info.get("path", "")
        parts = path.split("/")
        if len(parts) >= 3:
            artist = parts[0]
            album = parts[1]
            track = "/".join(parts[2:])
        elif len(parts) == 2:
            artist = parts[0]
            album = "Single/Unsorted"
            track = parts[1]
        else:
            artist = "Unsorted"
            album = "Unsorted"
            track = path

        if artist not in library_tree:
            library_tree[artist] = {}
        if album not in library_tree[artist]:
            library_tree[artist][album] = []
        
        library_tree[artist][album].append(track)

    results = ["## Downloaded Library Music\n"]
    for artist, albums in sorted(library_tree.items()):
        results.append(f"### 🎙️ Artist: **{artist}**")
        for album, tracks in sorted(albums.items()):
            results.append(f"  - **📁 Album: {album}**")
            for track in sorted(tracks):
                results.append(f"    - 🎵 {track}")
        results.append("")

    return "\n".join(results)

# ---------------------------------------------------------------------------
# Token Authentication Middleware
# ---------------------------------------------------------------------------
class TokenAuthMiddleware(BaseHTTPMiddleware):
    """Starlette middleware to require a secret token for all requests."""

    def __init__(self, app, token: str):
        super().__init__(app)
        self.token = token

    async def dispatch(self, request, call_next):
        # Allow OPTIONS request (CORS)
        if request.method == "OPTIONS":
            return await call_next(request)

        # Allow simple health check endpoint without token
        if request.url.path in ("/health", "/"):
            return await call_next(request)

        # Retrieve token from headers or query params
        auth_header = request.headers.get("authorization")
        req_token = None

        if auth_header:
            if auth_header.lower().startswith("bearer "):
                req_token = auth_header[7:]
            else:
                req_token = auth_header
        else:
            # Fallback to custom header or query param
            req_token = request.headers.get("x-mcp-token") or request.query_params.get("token")

        if not req_token or req_token != self.token:
            logger.warning(f"Unauthorized access attempt from {request.client.host if request.client else 'unknown'}")
            return Response("Unauthorized: Invalid or missing token", status_code=401)

        return await call_next(request)

# ---------------------------------------------------------------------------
# Main Entry Point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    token = os.environ.get("MCP_TOKEN")
    port = int(os.environ.get("PORT", "8086"))
    host = os.environ.get("HOST", "0.0.0.0")
    # FastMCP uses Starlette under the hood for SSE
    app = mcp.sse_app()

    # Wrap the app with token auth middleware if set
    if token:
        app.add_middleware(TokenAuthMiddleware, token=token.strip())
        logger.info(f"MCP Server token authentication ENABLED (Token length: {len(token)})")
    else:
        logger.warning("MCP Server token authentication DISABLED! Specify MCP_TOKEN env variable.")

    # Simple health check endpoint on the root app
    async def health_check(request):
        return Response("OK", status_code=200)

    app.add_route("/health", health_check)

    # Start the server using uvicorn
    # proxy_headers=True + forwarded_allow_ips="*" ensures uvicorn trusts the
    # Host / X-Forwarded-* headers set by NGINX, preventing 421 errors.
    logger.info(f"Starting Qobuz MCP SSE server on {host}:{port}")
    uvicorn.run(app, host=host, port=port, proxy_headers=True, forwarded_allow_ips="*")
