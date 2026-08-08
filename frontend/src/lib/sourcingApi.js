const API_BASE =
  (typeof import.meta !== 'undefined' && import.meta.env?.VITE_SOURCING_API_URL) ||
  '/api';

const TOKEN_KEY = 'sourcing_access_token';

export function getAccessToken() {
  try {
    return sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setAccessToken(token) {
  try {
    if (token) sessionStorage.setItem(TOKEN_KEY, token);
    else sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    /* private mode / blocked storage */
  }
}

export function clearAccessToken() {
  setAccessToken(null);
}

async function request(path, options = {}) {
  const { headers, ...rest } = options;
  const token = getAccessToken();
  let res;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...rest,
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(headers || {}),
      },
    });
  } catch {
    throw new Error(
      'Cannot reach the API (network/CORS). If this is a deployed UI, ensure the API allows this origin.',
    );
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || JSON.stringify(body);
    } catch {
      /* ignore */
    }
    const err = new Error(
      typeof detail === 'string' ? detail : JSON.stringify(detail),
    );
    err.status = res.status;
    throw err;
  }
  if (res.status === 204) return null;
  const text = await res.text();
  if (!text) return null;
  return JSON.parse(text);
}

export function login(email, password) {
  return request('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  });
}

export function logout() {
  return request('/auth/logout', { method: 'POST' });
}

export function fetchMe() {
  return request('/auth/me');
}

export function listRoles() {
  return request('/roles');
}

export function listArchivedRoles() {
  return request('/roles/archived');
}

export function archiveRole(slug) {
  return request(`/roles/${encodeURIComponent(slug)}/archive`, { method: 'POST' });
}

export function unarchiveRole(slug) {
  return request(`/roles/${encodeURIComponent(slug)}/unarchive`, {
    method: 'POST',
  });
}

export function startSession(slug = 'new') {
  return request(`/roles/${encodeURIComponent(slug)}/session`, { method: 'POST' });
}

export function sendChatMessage(roleSlug, message, sessionId = null) {
  const body = { message };
  if (sessionId) body.session_id = sessionId;
  return request(`/chat/${encodeURIComponent(roleSlug)}/message`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export function fetchRoleCandidates(slug) {
  return request(`/roles/${encodeURIComponent(slug)}/candidates`);
}

export function saveRoleJd(slug, jd_text) {
  return request(`/roles/${encodeURIComponent(slug)}/jd`, {
    method: 'POST',
    body: JSON.stringify({ jd_text }),
  });
}

export function scoreRole(slug) {
  return request(`/roles/${encodeURIComponent(slug)}/score`, { method: 'POST' });
}

export function narrateRole(slug) {
  return request(`/roles/${encodeURIComponent(slug)}/narrate`, { method: 'POST' });
}

export function fetchRoleScores(slug) {
  return request(`/roles/${encodeURIComponent(slug)}/scores`);
}

export function fetchReviewQueue(slug, status = 'reviewing') {
  const q = new URLSearchParams({ status });
  return request(`/roles/${encodeURIComponent(slug)}/review-queue?${q}`);
}

export function setReviewStatus(slug, candidateId, status) {
  return request(
    `/roles/${encodeURIComponent(slug)}/candidates/${encodeURIComponent(candidateId)}/review-status`,
    {
      method: 'POST',
      body: JSON.stringify({ status }),
    },
  );
}

export function retryIncompleteProfiles(slug) {
  return request(`/roles/${encodeURIComponent(slug)}/retry-incomplete`, {
    method: 'POST',
  });
}

export function retryOneCandidate(slug, candidateId) {
  return request(
    `/roles/${encodeURIComponent(slug)}/candidates/${encodeURIComponent(candidateId)}/retry`,
    { method: 'POST' },
  );
}

export function ignoreCandidate(slug, candidateId) {
  return request(
    `/roles/${encodeURIComponent(slug)}/candidates/${encodeURIComponent(candidateId)}/ignore`,
    { method: 'POST' },
  );
}

export function unignoreCandidate(slug, candidateId) {
  return request(
    `/roles/${encodeURIComponent(slug)}/candidates/${encodeURIComponent(candidateId)}/unignore`,
    { method: 'POST' },
  );
}
