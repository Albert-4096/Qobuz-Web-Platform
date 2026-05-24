/* ========================================================================
   Qobuz Service — Frontend Application
   ======================================================================== */

(() => {
  'use strict';

  // --------------- Constants ---------------
  const API = '/api';
  const DEBOUNCE_MS = 300;
  const POLL_MS = 2000;
  const MAX_HISTORY = 8;
  const QUALITY_OPTIONS = [
    { id: 1, label: 'MP3 320',  detail: '320 kbps',     cssClass: 'quality-mp3' },
    { id: 2, label: 'CD',       detail: '16-bit / 44.1 kHz', cssClass: 'quality-cd' },
    { id: 3, label: 'Hi-Res',   detail: '24-bit / 96 kHz',   cssClass: 'quality-hires' },
    { id: 4, label: 'Hi-Res+',  detail: '24-bit / 192 kHz',  cssClass: 'quality-hiresplus' },
  ];

  // --------------- State ---------------
  let currentTab = 'search';
  let searchType = 'tracks';
  let searchTimer = null;
  let pollTimer = null;
  let activeDropdown = null;

  // --------------- DOM Refs ---------------
  const $ = (sel, ctx = document) => ctx.querySelector(sel);
  const $$ = (sel, ctx = document) => [...ctx.querySelectorAll(sel)];

  const searchInput = $('#search-input');
  const searchEmpty = $('#search-empty');
  const searchSkeleton = $('#search-skeleton');
  const resultsGrid = $('#results-grid');
  const downloadsList = $('#downloads-list');
  const downloadsEmpty = $('#downloads-empty');
  const downloadBadge = $('#download-badge');
  const libraryTree = $('#library-tree');
  const libraryEmpty = $('#library-empty');
  const searchHistory = $('#search-history');
  const toastContainer = $('#toast-container');

  // Audio player refs
  const audioEl = $('#audio-player');
  const playerBar = $('#player-bar');
  const playerTitle = $('#player-title');
  const playerArtist = $('#player-artist');
  const playerPlayBtn = $('#player-play');
  const playerProgress = $('#player-progress');
  const playerCurrent = $('#player-current');
  const playerDuration = $('#player-duration');
  const playerCloseBtn = $('#player-close');

  // --------------- Utilities ---------------
  function formatDuration(seconds) {
    if (!seconds || isNaN(seconds)) return '0:00';
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return `${m}:${s.toString().padStart(2, '0')}`;
  }

  function formatSize(bytes) {
    if (!bytes) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB'];
    let i = 0;
    let size = bytes;
    while (size >= 1024 && i < units.length - 1) { size /= 1024; i++; }
    return `${size.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
  }

  function formatDate(ts) {
    if (!ts) return '';
    const d = new Date(typeof ts === 'number' ? ts * 1000 : ts);
    return d.getFullYear().toString();
  }

  function getQualityBadge(bitDepth, sampleRate) {
    if (bitDepth >= 24 && sampleRate >= 192) return { label: 'Hi-Res+', cssClass: 'quality-hiresplus' };
    if (bitDepth >= 24 && sampleRate >= 88) return { label: 'Hi-Res', cssClass: 'quality-hires' };
    if (bitDepth >= 16) return { label: 'CD', cssClass: 'quality-cd' };
    return { label: 'MP3', cssClass: 'quality-mp3' };
  }

  function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str || '';
    return div.innerHTML;
  }

  function qobuzTrackUrl(trackId) {
    return `https://play.qobuz.com/track/${trackId}`;
  }

  function qobuzAlbumUrl(albumId) {
    return `https://play.qobuz.com/album/${albumId}`;
  }

  // --------------- Toast System ---------------
  function showToast(message, type = 'info') {
    const icons = {
      success: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>',
      error: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>',
      info: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>',
    };

    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.innerHTML = `<span class="toast-icon">${icons[type] || icons.info}</span><span>${escapeHtml(message)}</span>`;
    toastContainer.appendChild(toast);

    setTimeout(() => {
      toast.classList.add('exiting');
      toast.addEventListener('animationend', () => toast.remove());
    }, 4000);
  }

  // --------------- Search History ---------------
  function getHistory() {
    try {
      return JSON.parse(localStorage.getItem('qobuz_search_history') || '[]');
    } catch { return []; }
  }

  function addToHistory(query) {
    const q = query.trim();
    if (!q) return;
    let history = getHistory().filter(h => h !== q);
    history.unshift(q);
    history = history.slice(0, MAX_HISTORY);
    localStorage.setItem('qobuz_search_history', JSON.stringify(history));
    renderHistory();
  }

  function renderHistory() {
    const history = getHistory();
    if (!history.length) {
      searchHistory.innerHTML = '';
      return;
    }
    searchHistory.innerHTML = history.map(q =>
      `<button class="history-chip" data-query="${escapeHtml(q)}">${escapeHtml(q)}</button>`
    ).join('');
  }

  // --------------- Tab Navigation ---------------
  function switchTab(tab) {
    currentTab = tab;
    $$('.tab-btn').forEach(btn => {
      const isActive = btn.dataset.tab === tab;
      btn.classList.toggle('active', isActive);
      btn.setAttribute('aria-current', isActive ? 'page' : 'false');
    });
    $$('.tab-panel').forEach(panel => {
      const isActive = panel.id === `panel-${tab}`;
      panel.classList.toggle('active', isActive);
      panel.hidden = !isActive;
    });
    if (tab === 'downloads') refreshDownloads();
    if (tab === 'library') refreshLibrary();
  }

  // --------------- API Calls ---------------
  async function apiGet(path) {
    const resp = await fetch(`${API}${path}`);
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${resp.status}`);
    }
    return resp.json();
  }

  async function apiPost(path, body) {
    const resp = await fetch(`${API}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${resp.status}`);
    }
    return resp.json();
  }

  async function apiDelete(path) {
    const resp = await fetch(`${API}${path}`, { method: 'DELETE' });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${resp.status}`);
    }
    return resp.json();
  }

  // --------------- Search ---------------
  async function performSearch(query) {
    const q = query.trim();
    if (!q) {
      resultsGrid.innerHTML = '';
      searchSkeleton.hidden = true;
      searchEmpty.hidden = false;
      return;
    }

    searchEmpty.hidden = true;
    resultsGrid.innerHTML = '';
    searchSkeleton.hidden = false;

    try {
      const data = await apiGet(`/search?q=${encodeURIComponent(q)}&type=${searchType}&limit=30`);
      searchSkeleton.hidden = true;
      addToHistory(q);
      renderResults(data);
    } catch (err) {
      searchSkeleton.hidden = true;
      showToast(`Search failed: ${err.message}`, 'error');
    }
  }

  function renderResults(data) {
    if (searchType === 'tracks') {
      const items = data?.tracks?.items || [];
      if (!items.length) {
        resultsGrid.innerHTML = '<div class="empty-state"><h2>No tracks found</h2><p>Try a different search query</p></div>';
        return;
      }
      resultsGrid.innerHTML = items.map(t => renderTrackCard(t)).join('');
    } else if (searchType === 'albums') {
      const items = data?.albums?.items || [];
      if (!items.length) {
        resultsGrid.innerHTML = '<div class="empty-state"><h2>No albums found</h2><p>Try a different search query</p></div>';
        return;
      }
      resultsGrid.innerHTML = items.map(a => renderAlbumCard(a)).join('');
    } else if (searchType === 'artists') {
      const items = data?.artists?.items || [];
      if (!items.length) {
        resultsGrid.innerHTML = '<div class="empty-state"><h2>No artists found</h2><p>Try a different search query</p></div>';
        return;
      }
      resultsGrid.innerHTML = items.map(a => renderArtistCard(a)).join('');
    }
  }

  function renderTrackCard(track) {
    const artUrl = track.album?.image?.small || track.album?.image?.thumbnail || '';
    const title = escapeHtml(track.title || 'Unknown Track');
    const artist = escapeHtml(track.performer?.name || track.album?.artist?.name || 'Unknown Artist');
    const albumTitle = escapeHtml(track.album?.title || '');
    const duration = formatDuration(track.duration);
    const q = getQualityBadge(track.maximum_bit_depth, track.maximum_sampling_rate);
    const trackId = track.id;

    return `
      <div class="track-card" data-track-id="${trackId}">
        <img class="track-art" src="${artUrl}" alt="" loading="lazy" onerror="this.style.display='none'">
        <div class="track-info">
          <div class="track-title" title="${title}">${title}</div>
          <div class="track-meta">
            <span class="track-artist">${artist}</span>
            <span class="track-dot"></span>
            <span class="track-duration">${duration}</span>
          </div>
        </div>
        <div class="track-actions">
          <span class="quality-badge ${q.cssClass}">${q.label}</span>
          <div class="dl-btn-group">
            <button class="dl-btn" data-url="${qobuzTrackUrl(trackId)}" title="Download Highest Quality" aria-label="Download ${title}">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>
              </svg>
            </button>
            <button class="dl-caret" title="Select Quality" aria-label="Select Quality">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg>
            </button>
            <div class="quality-dropdown" data-url="${qobuzTrackUrl(trackId)}">
              ${QUALITY_OPTIONS.map(qo => `
                <button class="quality-option" data-quality="${qo.id}" data-url="${qobuzTrackUrl(trackId)}">
                  <span class="q-label">${qo.label}</span>
                  <span class="q-detail">${qo.detail}</span>
                </button>
              `).join('')}
            </div>
          </div>
        </div>
      </div>`;
  }

  function renderAlbumCard(album) {
    const artUrl = album.image?.large || album.image?.small || album.image?.thumbnail || '';
    const title = escapeHtml(album.title || 'Unknown Album');
    const artist = escapeHtml(album.artist?.name || 'Unknown Artist');
    const year = formatDate(album.released_at);
    const tracks = album.tracks_count || 0;
    const genre = escapeHtml(album.genre?.name || '');
    const q = getQualityBadge(album.maximum_bit_depth, album.maximum_sampling_rate);
    const albumId = album.id;

    return `
      <div class="album-card" data-album-id="${albumId}">
        <img class="album-art" src="${artUrl}" alt="" loading="lazy" onerror="this.style.display='none'">
        <div class="album-info">
          <div class="album-title" title="${title}">${title}</div>
          <div class="album-artist">${artist}</div>
          <div class="album-meta-row">
            <span class="quality-badge ${q.cssClass}">${q.label}</span>
            ${year ? `<span class="album-meta-tag">${year}</span>` : ''}
            ${tracks ? `<span class="album-meta-tag">${tracks} track${tracks !== 1 ? 's' : ''}</span>` : ''}
            ${genre ? `<span class="album-meta-tag">${genre}</span>` : ''}
          </div>
        </div>
        <div class="track-actions">
          <div class="dl-btn-group">
            <button class="dl-btn" data-url="${qobuzAlbumUrl(albumId)}" title="Download Highest Quality" aria-label="Download album ${title}">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>
              </svg>
            </button>
            <button class="dl-caret" title="Select Quality" aria-label="Select Quality">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg>
            </button>
            <div class="quality-dropdown" data-url="${qobuzAlbumUrl(albumId)}">
              ${QUALITY_OPTIONS.map(qo => `
                <button class="quality-option" data-quality="${qo.id}" data-url="${qobuzAlbumUrl(albumId)}">
                  <span class="q-label">${qo.label}</span>
                  <span class="q-detail">${qo.detail}</span>
                </button>
              `).join('')}
            </div>
          </div>
        </div>
      </div>`;
  }

  function renderArtistCard(artist) {
    const imgUrl = artist.image?.small || artist.image?.medium || artist.picture || '';
    const name = escapeHtml(artist.name || 'Unknown Artist');
    const albums = artist.albums_count || 0;

    return `
      <div class="artist-card">
        ${imgUrl ? `<img class="artist-avatar" src="${imgUrl}" alt="" loading="lazy" onerror="this.style.display='none'">` : '<div class="artist-avatar"></div>'}
        <div>
          <div class="artist-name">${name}</div>
          ${albums ? `<div class="artist-albums-count">${albums} album${albums !== 1 ? 's' : ''}</div>` : ''}
        </div>
      </div>`;
  }

  // --------------- Downloads ---------------
  async function startDownload(url, quality) {
    try {
      const data = await apiPost('/download', { url, quality });
      showToast('Download started', 'success');
      startPolling();
      updateBadge();
      if (currentTab === 'downloads') refreshDownloads();
    } catch (err) {
      showToast(`Download failed: ${err.message}`, 'error');
    }
  }

  async function refreshDownloads() {
    try {
      const data = await apiGet('/downloads');
      const downloads = data.downloads || [];

      if (!downloads.length) {
        downloadsEmpty.hidden = false;
        downloadsList.innerHTML = '';
        stopPolling();
        return;
      }

      downloadsEmpty.hidden = true;
      downloadsList.innerHTML = downloads.map(d => renderDownloadItem(d)).join('');

      // Update badge
      const active = downloads.filter(d => d.status === 'downloading' || d.status === 'pending');
      updateBadge(active.length);

      // Keep polling if active downloads exist
      if (active.length > 0) startPolling();
      else stopPolling();
    } catch (err) {
      console.error('Failed to refresh downloads:', err);
    }
  }

  function renderDownloadItem(dl) {
    const statusIcons = {
      pending: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>',
      downloading: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>',
      completed: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>',
      failed: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>',
    };

    const title = escapeHtml(dl.title || dl.url || 'Download');
    const pct = Math.round(dl.progress || 0);
    const canCancel = dl.status === 'pending' || dl.status === 'downloading';
    const progressClass = dl.status === 'completed' ? 'completed' : dl.status === 'failed' ? 'failed' : '';

    return `
      <div class="download-item" data-dl-id="${dl.id}">
        <div class="download-icon-wrap ${dl.status}">${statusIcons[dl.status] || statusIcons.pending}</div>
        <div class="download-info">
          <div class="download-title">${title}</div>
          <div class="download-url">${escapeHtml(dl.url)}</div>
          ${dl.quality_label ? `<div class="download-url">${escapeHtml(dl.quality_label)}</div>` : ''}
          ${dl.error ? `<div class="download-url" style="color:var(--error)">${escapeHtml(dl.error)}</div>` : ''}
          <div class="download-progress-bar">
            <div class="download-progress-fill ${progressClass}" style="width:${pct}%"></div>
          </div>
        </div>
        <div class="download-status-text">
          <span class="status-label ${dl.status}">${pct}%</span>
        </div>
        ${canCancel ? `
          <button class="download-cancel" data-dl-id="${dl.id}" title="Cancel" aria-label="Cancel download">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
          </button>
        ` : ''}
      </div>`;
  }

  function updateBadge(count) {
    if (count === undefined) {
      // Fetch from API
      apiGet('/downloads').then(data => {
        const active = (data.downloads || []).filter(d => d.status === 'downloading' || d.status === 'pending');
        updateBadge(active.length);
      }).catch(() => {});
      return;
    }
    if (count > 0) {
      downloadBadge.textContent = count;
      downloadBadge.hidden = false;
    } else {
      downloadBadge.hidden = true;
    }
  }

  function startPolling() {
    if (pollTimer) return;
    pollTimer = setInterval(() => {
      if (currentTab === 'downloads') refreshDownloads();
      else updateBadge();
    }, POLL_MS);
  }

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  async function cancelDownload(dlId) {
    try {
      await apiDelete(`/downloads/${dlId}`);
      showToast('Download cancelled', 'info');
      refreshDownloads();
    } catch (err) {
      showToast(`Cancel failed: ${err.message}`, 'error');
    }
  }

  // --------------- Library ---------------
  async function refreshLibrary() {
    try {
      const data = await apiGet('/library');
      const files = data.files || [];

      if (!files.length) {
        libraryEmpty.hidden = false;
        libraryTree.innerHTML = '';
        return;
      }

      libraryEmpty.hidden = true;

      // Organize files into a tree by top-level directory (artist)
      const tree = {};
      files.forEach(f => {
        const parts = f.path.split('/');
        const folder = parts.length > 1 ? parts.slice(0, -1).join('/') : 'Unsorted';
        if (!tree[folder]) tree[folder] = [];
        tree[folder].push(f);
      });

      const foldersSorted = Object.keys(tree).sort();
      libraryTree.innerHTML = foldersSorted.map(folder => {
        const folderFiles = tree[folder];
        return `
          <div class="library-folder">
            <button class="library-folder-header" aria-expanded="false">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6"/></svg>
              ${escapeHtml(folder)} <span style="color:var(--text-muted); font-weight:400; font-size:0.8125rem">(${folderFiles.length})</span>
            </button>
            <div class="library-files">
              ${folderFiles.map(f => renderLibraryFile(f)).join('')}
            </div>
          </div>`;
      }).join('');
    } catch (err) {
      showToast(`Failed to load library: ${err.message}`, 'error');
    }
  }

  function renderLibraryFile(file) {
    const name = escapeHtml(file.name);
    const size = formatSize(file.size);
    const isAudio = /\.(flac|mp3|wav|ogg|m4a|aac|alac|wma)$/i.test(file.name);
    const filePath = encodeURIComponent(file.path).replace(/%2F/g, '/');

    return `
      <div class="library-file" data-path="${filePath}">
        <span class="library-file-icon">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            ${isAudio ? '<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>' : '<path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/>'}
          </svg>
        </span>
        <span class="library-file-name">${name}</span>
        <span class="library-file-size">${size}</span>
        ${isAudio ? `<button class="library-file-play" data-file="${filePath}" title="Play" aria-label="Play ${name}">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><polygon points="5,3 19,12 5,21"/></svg>
        </button>` : ''}
        <a class="library-file-download" href="${API}/files/${filePath}" download title="Download" aria-label="Save ${name}">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
        </a>
      </div>`;
  }

  // --------------- Audio Player ---------------
  function playFile(filePath, name) {
    audioEl.src = `${API}/files/${filePath}`;
    audioEl.play();
    playerTitle.textContent = name || 'Unknown';
    playerArtist.textContent = '';
    playerBar.hidden = false;
    updatePlayButton(true);
  }

  function updatePlayButton(isPlaying) {
    playerPlayBtn.innerHTML = isPlaying
      ? '<svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>'
      : '<svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><polygon points="5,3 19,12 5,21"/></svg>';
  }

  audioEl.addEventListener('timeupdate', () => {
    if (audioEl.duration) {
      const pct = (audioEl.currentTime / audioEl.duration) * 100;
      playerProgress.value = pct;
      playerCurrent.textContent = formatDuration(audioEl.currentTime);
      playerDuration.textContent = formatDuration(audioEl.duration);
    }
  });

  audioEl.addEventListener('play', () => updatePlayButton(true));
  audioEl.addEventListener('pause', () => updatePlayButton(false));
  audioEl.addEventListener('ended', () => updatePlayButton(false));

  playerPlayBtn.addEventListener('click', () => {
    if (audioEl.paused) audioEl.play();
    else audioEl.pause();
  });

  playerProgress.addEventListener('input', () => {
    if (audioEl.duration) {
      audioEl.currentTime = (playerProgress.value / 100) * audioEl.duration;
    }
  });

  playerCloseBtn.addEventListener('click', () => {
    audioEl.pause();
    audioEl.src = '';
    playerBar.hidden = true;
  });

  // --------------- Event Handlers ---------------

  // Tab clicks
  $$('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => switchTab(btn.dataset.tab));
  });

  // Search type toggle
  $$('.type-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      $$('.type-btn').forEach(b => {
        b.classList.remove('active');
        b.setAttribute('aria-checked', 'false');
      });
      btn.classList.add('active');
      btn.setAttribute('aria-checked', 'true');
      searchType = btn.dataset.type;
      // Re-run search if there's a query
      if (searchInput.value.trim()) performSearch(searchInput.value);
    });
  });

  // Search input with debounce
  searchInput.addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => performSearch(searchInput.value), DEBOUNCE_MS);
  });

  searchInput.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      searchInput.value = '';
      searchInput.blur();
      resultsGrid.innerHTML = '';
      searchSkeleton.hidden = true;
      searchEmpty.hidden = false;
    }
    if (e.key === 'Enter') {
      clearTimeout(searchTimer);
      performSearch(searchInput.value);
    }
  });

  // Keyboard shortcut: Ctrl+K / Cmd+K to focus search
  document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
      e.preventDefault();
      searchInput.focus();
      searchInput.select();
    }
  });

  // History chip clicks
  searchHistory.addEventListener('click', (e) => {
    const chip = e.target.closest('.history-chip');
    if (chip) {
      searchInput.value = chip.dataset.query;
      searchInput.focus();
      performSearch(chip.dataset.query);
    }
  });

  // Download button clicks (using event delegation)
  document.addEventListener('click', (e) => {
    // Download button: start highest quality download automatically
    const dlBtn = e.target.closest('.dl-btn');
    if (dlBtn) {
      e.stopPropagation();
      const url = dlBtn.dataset.url;
      startDownload(url, 4); // 4 = Highest available (Hi-Res+ with fallback)
      return;
    }

    // Caret button: show quality dropdown
    const dlCaret = e.target.closest('.dl-caret');
    if (dlCaret) {
      e.stopPropagation();
      const group = dlCaret.closest('.dl-btn-group');
      const dropdown = group?.querySelector('.quality-dropdown');
      if (dropdown) {
        // Close other dropdowns
        $$('.quality-dropdown.open').forEach(d => {
          if (d !== dropdown) d.classList.remove('open');
        });
        dropdown.classList.toggle('open');
        activeDropdown = dropdown.classList.contains('open') ? dropdown : null;
      }
      return;
    }

    // Quality option click: start download
    const qOption = e.target.closest('.quality-option');
    if (qOption) {
      e.stopPropagation();
      const url = qOption.dataset.url;
      const quality = parseInt(qOption.dataset.quality, 10);
      startDownload(url, quality);
      // Close dropdown
      const dropdown = qOption.closest('.quality-dropdown');
      if (dropdown) dropdown.classList.remove('open');
      activeDropdown = null;
      return;
    }

    // Cancel download click
    const cancelBtn = e.target.closest('.download-cancel');
    if (cancelBtn) {
      cancelDownload(cancelBtn.dataset.dlId);
      return;
    }

    // Library folder expand/collapse
    const folderHeader = e.target.closest('.library-folder-header');
    if (folderHeader) {
      folderHeader.classList.toggle('expanded');
      const files = folderHeader.nextElementSibling;
      if (files) files.classList.toggle('expanded');
      const isExpanded = folderHeader.classList.contains('expanded');
      folderHeader.setAttribute('aria-expanded', isExpanded.toString());
      return;
    }

    // Library file play
    const playBtn = e.target.closest('.library-file-play');
    if (playBtn) {
      e.stopPropagation();
      const filePath = playBtn.dataset.file;
      const fileRow = playBtn.closest('.library-file');
      const name = fileRow?.querySelector('.library-file-name')?.textContent || '';
      playFile(filePath, name);
      return;
    }

    // Close dropdowns when clicking outside
    if (activeDropdown) {
      activeDropdown.classList.remove('open');
      activeDropdown = null;
    }
  });

  // Close dropdown on Escape
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && activeDropdown) {
      activeDropdown.classList.remove('open');
      activeDropdown = null;
    }
  });

  // --------------- Init ---------------
  function init() {
    renderHistory();
    updateBadge();

    // Check for active downloads on load
    apiGet('/downloads').then(data => {
      const active = (data.downloads || []).filter(d => d.status === 'downloading' || d.status === 'pending');
      if (active.length > 0) startPolling();
    }).catch(() => {});
  }

  init();
})();
