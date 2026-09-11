/* SamaConcept · GeoProjects — logique du dashboard.
 *
 * Carte vectorielle MapLibre GL alimentée par OpenFreeMap (données
 * OpenStreetMap, sans clé API, nette à tous les niveaux de zoom).
 *
 * Filtres multi-critères (ET entre critères, OU au sein d'un filtre),
 * debounce 300 ms sur la recherche (cahier des charges §9.2).
 * L'infobulle et la fiche projet affichent un lien cliquable vers le
 * dossier projet (config ``app.projects_base_url``).
 */
'use strict';

const STATUTS = ['devis', 'en_cours', 'finalise', 'livre'];
const STATUT_LABELS = { devis: 'Devis', en_cours: 'En cours', finalise: 'Finalisé', livre: 'Livré' };
const STATUT_COLORS = { devis: '#f5c518', en_cours: '#2196f3', finalise: '#4caf50', livre: '#26a69a' };

// Tuiles vectorielles OpenStreetMap gratuites (OpenFreeMap), sans clé API.
const OPENFREEMAP_STYLE = 'https://tiles.openfreemap.org/styles/liberty';

// Vue initiale centrée sur le Maroc (Tanger, Rabat, Casablanca, Fès).
const MOROCCO_CENTER = [-6.4, 33.7];
const MOROCCO_ZOOM = 6.5;
const PROJECT_ZOOM = 6;

const state = {
  filters: { q: '', statuts: new Set(), promoteur: '', etape: '', hasGps: false },
  projects: [],
  selectedId: null,
};

const $ = (sel) => document.querySelector(sel);

const els = {
  search: $('#inputSearch'),
  promoteur: $('#inputPromoteur'),
  etape: $('#inputEtape'),
  etapeDatalist: $('#etapeDatalist'),
  gpsToggle: $('#toggleGps'),
  reset: $('#btnReset'),
  sync: $('#btnSync'),
  syncLabel: $('#btnSyncLabel'),
  extract: $('#btnExtract'),
  extractIcon: $('#btnExtractIcon'),
  extractLabel: $('#btnExtractLabel'),
  list: $('#projectList'),
  count: $('#countLabel'),
  statsBar: $('#statsBar'),
  map: $('#map'),
  tooltip: $('#tooltip'),
};

let map = null;
let mapReady = false;
let detailModal = null;
let tooltipHideTimer = null;
let dashboardRefreshTimer = null;

/* ------------------------------- utilitaires ------------------------------- */

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

function villeFrom(adresse) {
  const parts = (adresse || '').split(',').map((s) => s.trim()).filter(Boolean);
  return parts.length > 1 ? parts[parts.length - 1] : (parts[0] || '—');
}

function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

async function fetchJSON(url, opts) {
  const res = await fetch(url, opts);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

function fmtSize(bytes) {
  if (bytes == null) return '—';
  if (bytes < 1024) return `${bytes} o`;
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} Ko`;
  return `${(bytes / 1048576).toFixed(1)} Mo`;
}

/* ---------------------------------- carte ---------------------------------- */

function projectsToGeoJSON(projects) {
  return {
    type: 'FeatureCollection',
    features: projects
      .filter((p) => p.latitude !== 0 || p.longitude !== 0)
      .map((p) => ({
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [p.longitude, p.latitude] },
        properties: {
          id: p.id,
          nom_projet: p.nom_projet,
          promoteur: p.promoteur,
          etape_actuelle: p.etape_actuelle,
          statut: p.statut,
          ville: villeFrom(p.adresse),
          folder_url: p.folder_url || '',
        },
      })),
  };
}

function initMap() {
  map = new maplibregl.Map({
    container: els.map,
    style: OPENFREEMAP_STYLE,
    center: MOROCCO_CENTER,
    zoom: MOROCCO_ZOOM,
  });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');

  map.on('load', () => {
    map.addSource('projects', { type: 'geojson', data: projectsToGeoJSON([]) });

    // Libellés des villes (sous les marqueurs)
    map.addLayer({
      id: 'project-labels',
      type: 'symbol',
      source: 'projects',
      layout: {
        'text-field': ['get', 'ville'],
        'text-size': 12,
        'text-anchor': 'top',
        'text-offset': [0, 1.3],
        'text-font': ['Noto Sans Regular'],
      },
      paint: {
        'text-color': '#14213d',
        'text-halo-color': '#ffffff',
        'text-halo-width': 1.6,
      },
    });

    // Marqueurs colorés par statut, avec liseré sombre
    map.addLayer({
      id: 'project-markers',
      type: 'circle',
      source: 'projects',
      paint: {
        'circle-radius': 9,
        'circle-color': [
          'match', ['get', 'statut'],
          'devis', STATUT_COLORS.devis,
          'en_cours', STATUT_COLORS.en_cours,
          'finalise', STATUT_COLORS.finalise,
          'livre', STATUT_COLORS.livre,
          '#9e9e9e',
        ],
        'circle-stroke-color': '#0b1020',
        'circle-stroke-width': 2,
      },
    });

    map.on('click', 'project-markers', (e) => {
      const p = state.projects.find((x) => x.id === e.features[0].properties.id);
      if (p) { selectProject(p); openDetail(p.id); }
    });

    map.on('mousemove', 'project-markers', (e) => {
      map.getCanvas().style.cursor = 'pointer';
      clearTimeout(tooltipHideTimer);
      showTooltip(e.features[0].properties, e.originalEvent);
    });
    map.on('mouseleave', 'project-markers', () => {
      map.getCanvas().style.cursor = '';
      scheduleHideTooltip();
    });

    mapReady = true;
    updateMap();
  });
}

function updateMap() {
  if (!map || !mapReady) return;
  const src = map.getSource('projects');
  if (src) src.setData(projectsToGeoJSON(state.projects));
}

/* --------------------------------- tooltip --------------------------------- */

function showTooltip(props, ev) {
  const folder = props.folder_url
    ? `<a class="tt-folder" href="${esc(props.folder_url)}" target="_blank" rel="noopener">📁 ${esc(props.folder_url)}</a>`
    : '';
  els.tooltip.innerHTML =
    `<div class="tt-title">${esc(props.nom_projet)}</div>` +
    `<div class="tt-line">${esc(props.promoteur || 'Promoteur inconnu')}</div>` +
    `<div class="tt-line tt-etape">${esc(props.etape_actuelle || '—')}</div>` +
    folder;
  els.tooltip.style.display = 'block';
  els.tooltip.style.left = `${ev.clientX + 14}px`;
  els.tooltip.style.top = `${ev.clientY + 14}px`;
}

function hideTooltip() {
  els.tooltip.style.display = 'none';
}

// L'infobulle contient un lien cliquable : on retarde la fermeture pour
// laisser le temps de cliquer (pointer-events activé sur l'infobulle).
function scheduleHideTooltip() {
  clearTimeout(tooltipHideTimer);
  tooltipHideTimer = setTimeout(hideTooltip, 350);
}

/* ---------------------------- chargement des données ----------------------- */

function buildQuery() {
  const p = new URLSearchParams();
  const f = state.filters;
  if (f.q) p.set('q', f.q);
  f.statuts.forEach((s) => p.append('statut', s));
  if (f.promoteur) p.set('promoteur', f.promoteur);
  if (f.etape) p.set('etape', f.etape);
  if (f.hasGps) p.set('has_gps', 'true');
  return p.toString();
}

async function applyFilters() {
  try {
    state.projects = await fetchJSON('/api/projects?' + buildQuery());
  } catch (err) {
    console.error('Erreur de chargement des projets :', err);
    state.projects = [];
  }
  renderCards();
  updateMap();
  updateCount();
  updateEtapeDatalist();
}

async function refreshStats() {
  try {
    const s = await fetchJSON('/api/stats');
    els.statsBar.innerHTML = [
      `<span class="stat-pill"><i class="dot dot-all"></i>${s.total} projet${s.total > 1 ? 's' : ''}</span>`,
      ...STATUTS.map((st) => {
        const n = (s.by_statut && s.by_statut[st]) || 0;
        if (!n) return '';
        return `<span class="stat-pill"><i class="dot dot-${st}"></i>${STATUT_LABELS[st]} · ${n}</span>`;
      }).filter(Boolean),
    ].join('');
  } catch (err) { console.error('Erreur de chargement des stats :', err); }
}

async function loadPromoters() {
  try {
    const list = await fetchJSON('/api/promoters');
    els.promoteur.innerHTML = '<option value="">Tous les promoteurs</option>' +
      list.map((p) => `<option value="${esc(p)}">${esc(p)}</option>`).join('');
  } catch (err) { console.error('Erreur de chargement des promoteurs :', err); }
}

function refreshDashboard() {
  if (document.hidden) return;
  Promise.all([applyFilters(), refreshStats(), loadPromoters()])
    .catch((err) => console.error('Erreur de rafraîchissement :', err));
}

/* ------------------------------- rendu listes ------------------------------ */

function cardHTML(p) {
  return `
  <div class="project-card ${state.selectedId === p.id ? 'active' : ''}" data-id="${esc(p.id)}">
    <div class="d-flex justify-content-between align-items-start gap-2">
      <div class="card-title">${esc(p.nom_projet || p.id)}</div>
      <span class="badge badge-${p.statut}">${STATUT_LABELS[p.statut] || esc(p.statut)}</span>
    </div>
    <div class="card-meta"><strong>${esc(p.promoteur || '—')}</strong> · ${esc(villeFrom(p.adresse))}</div>
    ${p.etape_actuelle ? `<div class="card-etape">${esc(p.etape_actuelle)}</div>` : ''}
    ${(p.latitude === 0 && p.longitude === 0) ? '<div class="card-nogps">⚠ Non géolocalisé</div>' : ''}
  </div>`;
}

function renderCards() {
  els.list.innerHTML = state.projects.length
    ? state.projects.map(cardHTML).join('')
    : '<div class="empty-state">Aucun projet ne correspond aux filtres.</div>';
  els.list.querySelectorAll('.project-card').forEach((el) => {
    el.addEventListener('click', () => {
      const p = state.projects.find((x) => x.id === el.dataset.id);
      if (p) { selectProject(p); openDetail(p.id); }
    });
  });
}

function updateCount() {
  const n = state.projects.length;
  els.count.textContent = `${n} projet${n > 1 ? 's' : ''} trouvé${n > 1 ? 's' : ''}`;
}

function updateEtapeDatalist() {
  const seen = new Set();
  els.etapeDatalist.innerHTML = '';
  state.projects.forEach((p) => {
    const e = (p.etape_actuelle || '').trim();
    if (e && !seen.has(e)) {
      seen.add(e);
      els.etapeDatalist.insertAdjacentHTML('beforeend', `<option value="${esc(e)}">`);
    }
  });
}

/* ------------------------------ interactions ------------------------------- */

function selectProject(p) {
  state.selectedId = p.id;
  document.querySelectorAll('.project-card').forEach((el) =>
    el.classList.toggle('active', el.dataset.id === p.id));
  if (p.latitude !== 0 || p.longitude !== 0) {
    // Centrage / zoom fluide sur le projet (carte vectorielle, nette à tous les zooms)
    map.flyTo({ center: [p.longitude, p.latitude], zoom: PROJECT_ZOOM, duration: 900 });
  }
}

async function openDetail(id) {
  try {
    const data = await fetchJSON('/api/projects/' + encodeURIComponent(id));
    renderDetail(data);
    detailModal.show();
  } catch (err) { console.error('Erreur de chargement du détail :', err); }
}

function renderDetail(data) {
  const p = data.project;
  const gps = (p.latitude !== 0 || p.longitude !== 0)
    ? `${Number(p.latitude).toFixed(4)}, ${Number(p.longitude).toFixed(4)}`
    : 'Non renseigné';
  const dossier = p.folder_url
    ? `<a href="${esc(p.folder_url)}" target="_blank" rel="noopener">📁 ${esc(p.folder_url)}</a>`
    : esc(p.folder_name);
  // Toutes les valeurs sont déjà échappées / construites en HTML sûr.
  const infoRows = [
    ['Référence', esc(p.ref_administrative)],
    ['Promoteur', esc(p.promoteur)],
    ['Adresse', esc(p.adresse)],
    ['Dossier projet', dossier],
    ['Coordonnées GPS', esc(gps)],
    ['Statut', esc(STATUT_LABELS[p.statut] || p.statut)],
    ['Étape actuelle', esc(p.etape_actuelle)],
    ['Dernière mise à jour', esc(p.derniere_mise_a_jour ? new Date(p.derniere_mise_a_jour).toLocaleString('fr-FR') : '—')],
  ].filter(([, v]) => v && String(v).trim() !== '');

  const files = data.files || [];
  $('#detailTitle').textContent = p.nom_projet || p.id;
  $('#detailBody').innerHTML = `
    <table class="table table-sm table-dark detail-table align-middle">
      ${infoRows.map(([k, v]) => `<tr><th>${esc(k)}</th><td>${v}</td></tr>`).join('')}
    </table>
    <h6 class="mt-4 mb-2">Fichiers du dossier (${files.length})</h6>
    <ul class="list-group list-group-flush file-list">
      ${files.length ? files.map((f) => `
        <li class="list-group-item d-flex justify-content-between align-items-center">
          <span class="file-name">${f.est_metadata ? '📄 ' : ''}${esc(f.chemin)}</span>
          <span class="file-size text-muted">${fmtSize(f.taille_octets)}</span>
        </li>`).join('') : '<li class="list-group-item">Dossier vide</li>'}
    </ul>`;
}

/* ------------------------- robot AI (extraction) --------------------------- */

let extractTimer = null;

async function pollExtract() {
  try {
    const st = await fetchJSON('/api/extract/status');
    renderExtractStatus(st);
    if (st.running) {
      clearTimeout(extractTimer);
      extractTimer = setTimeout(pollExtract, 2000);
    } else if (st.finished_at) {
      await Promise.all([applyFilters(), refreshStats()]);
    }
  } catch (err) { console.error('Erreur de statut extraction :', err); }
}

function renderExtractStatus(st) {
  // Bouton masqué si l'agent AI est désactivé (pas de clé API configurée).
  els.extract.classList.toggle('d-none', !st.enabled);
  if (!st.enabled) return;
  if (st.running) {
    els.extract.disabled = true;
    els.extract.classList.add('ai-running');
    els.extractIcon.textContent = '⟳';
    const total = st.total || '…';
    const cur = st.current ? ` · ${st.current}` : '';
    els.extractLabel.textContent = `Traitement ${st.done}/${total}${cur}`;
  } else {
    els.extract.disabled = false;
    els.extract.classList.remove('ai-running');
    const skipped = st.skipped ? ` · ${st.skipped} ignoré${st.skipped > 1 ? 's' : ''}` : '';
    const errs = st.errors && st.errors.length ? ` · ${st.errors.length} erreur${st.errors.length > 1 ? 's' : ''}` : '';
    els.extractIcon.textContent = errs ? '⚠️' : (st.finished_at ? '✅' : '🤖');
    els.extractLabel.textContent = st.finished_at
      ? `Terminé · ${st.done}/${st.total}${skipped}${errs}`
      : 'Extraire (AI)';
  }
}

function resetFilters() {
  state.filters = { q: '', statuts: new Set(), promoteur: '', etape: '', hasGps: false };
  els.search.value = '';
  els.promoteur.value = '';
  els.etape.value = '';
  els.gpsToggle.checked = false;
  document.querySelectorAll('#statutFilters input[type=checkbox]').forEach((cb) => { cb.checked = false; });
  applyFilters();
}

/* ------------------------------- événements -------------------------------- */

function bindEvents() {
  // Debounce 300 ms sur la recherche textuelle (cahier des charges §9.2)
  els.search.addEventListener('input', debounce(() => {
    state.filters.q = els.search.value.trim();
    applyFilters();
  }, 300));

  document.querySelectorAll('#statutFilters input[type=checkbox]').forEach((cb) => {
    cb.addEventListener('change', () => {
      if (cb.checked) state.filters.statuts.add(cb.value);
      else state.filters.statuts.delete(cb.value);
      applyFilters();
    });
  });

  els.promoteur.addEventListener('change', () => {
    state.filters.promoteur = els.promoteur.value;
    applyFilters();
  });

  els.etape.addEventListener('input', debounce(() => {
    state.filters.etape = els.etape.value.trim();
    applyFilters();
  }, 300));

  els.gpsToggle.addEventListener('change', () => {
    state.filters.hasGps = els.gpsToggle.checked;
    applyFilters();
  });

  els.reset.addEventListener('click', resetFilters);

  // Onglets mobile : bascule carte / projets
  document.querySelectorAll('.tab-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      const tab = btn.dataset.tab;
      document.body.classList.toggle('tab-map', tab === 'map');
      document.body.classList.toggle('tab-projects', tab === 'projects');
      document.querySelectorAll('.tab-btn').forEach((b) => b.classList.toggle('active', b === btn));
      if (tab === 'map' && map) map.resize(); // la carte redevient visible
    });
  });

  // L'infobulle reste ouverte quand on survole son lien
  els.tooltip.addEventListener('mouseenter', () => clearTimeout(tooltipHideTimer));
  els.tooltip.addEventListener('mouseleave', scheduleHideTooltip);

  els.sync.addEventListener('click', async () => {
    els.sync.disabled = true;
    els.syncLabel.textContent = 'Réindexation…';
    try {
      await fetchJSON('/api/sync', { method: 'POST' });
      await Promise.all([applyFilters(), refreshStats(), loadPromoters()]);
    } catch (err) { console.error('Erreur de réindexation :', err); }
    finally {
      els.sync.disabled = false;
      els.syncLabel.textContent = '⟳ Réindexer';
    }
  });

  // Robot AI (gourmand) : lancé uniquement au clic, progression en direct.
  els.extract.addEventListener('click', async () => {
    if (!confirm('Lancer le robot AI ? Extraction LLM de tous les dossiers à traiter — très gourmand en ressources (plusieurs minutes par dossier).')) return;
    try {
      await fetchJSON('/api/extract', { method: 'POST' });
      await pollExtract();
    } catch (err) {
      console.error('Erreur de lancement de l\'extraction :', err);
      alert(err.message);
    }
  });

  pollExtract();  // reprend l'affichage si un job tourne déjà (rechargement de page)
  dashboardRefreshTimer = setInterval(refreshDashboard, 5000);
}

/* ------------------------------- démarrage --------------------------------- */

function init() {
  bindEvents();
  detailModal = new bootstrap.Modal($('#detailModal'));
  initMap();
  loadPromoters();
  refreshStats();
  applyFilters();
}

document.addEventListener('DOMContentLoaded', init);