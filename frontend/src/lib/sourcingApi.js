const API_BASE =
  (typeof import.meta !== 'undefined' && import.meta.env?.VITE_SOURCING_API_URL) ||
  '/api';

async function request(path, options = {}) {
  const { headers, ...rest } = options;
  const res = await fetch(`${API_BASE}${path}`, {
    ...rest,
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...(headers || {}),
    },
  });
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
