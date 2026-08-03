const API_BASE =
  (typeof import.meta !== 'undefined' && import.meta.env?.VITE_SOURCING_API_URL) ||
  '/api';

async function request(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    },
    ...options,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || JSON.stringify(body);
    } catch {
      /* ignore */
    }
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  return res.json();
}

export function listRoles() {
  return request('/roles');
}

export function startSession(slug = 'new') {
  return request(`/roles/${encodeURIComponent(slug)}/session`, { method: 'POST' });
}

export function sendChatMessage(roleSlug, message) {
  return request(`/chat/${encodeURIComponent(roleSlug)}/message`, {
    method: 'POST',
    body: JSON.stringify({ message }),
  });
}

export function fetchRoleCandidates(slug) {
  return request(`/roles/${encodeURIComponent(slug)}/candidates`);
}
