import { useCallback, useEffect, useRef, useState } from 'react';
import {
  fetchRoleCandidates,
  listRoles,
  sendChatMessage,
  startSession,
} from '../lib/sourcingApi';

function ExternalLinkIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M14 4h6v6M20 4l-9 9M10 5H5a1 1 0 0 0-1 1v13a1 1 0 0 0 1 1h13a1 1 0 0 0 1-1v-5"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export default function SourcingScreen() {
  const [roles, setRoles] = useState([]);
  const [activeSlug, setActiveSlug] = useState('new');
  const [messages, setMessages] = useState([]);
  const [candidates, setCandidates] = useState([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [summary, setSummary] = useState(null);
  const threadRef = useRef(null);

  const refreshRoles = useCallback(async () => {
    try {
      const data = await listRoles();
      setRoles(Array.isArray(data) ? data : []);
    } catch (err) {
      setError(err.message);
    }
  }, []);

  const loadCandidates = useCallback(async (slug) => {
    if (!slug || slug === 'new') {
      setCandidates([]);
      return;
    }
    try {
      const data = await fetchRoleCandidates(slug);
      setCandidates(data.candidates || []);
    } catch (err) {
      setError(err.message);
    }
  }, []);

  const bootstrapSession = useCallback(
    async (slug) => {
      setBusy(true);
      setError(null);
      setSummary(null);
      try {
        const data = await startSession(slug);
        const nextSlug = data.role_slug || slug;
        setActiveSlug(nextSlug === 'new' || !nextSlug ? 'new' : nextSlug);
        setMessages(
          data.assistant_message
            ? [{ role: 'assistant', content: data.assistant_message }]
            : [],
        );
        if (data.candidates) setCandidates(data.candidates);
        else if (nextSlug && nextSlug !== 'new') await loadCandidates(nextSlug);
        else setCandidates([]);
        await refreshRoles();
      } catch (err) {
        setError(err.message);
      } finally {
        setBusy(false);
      }
    },
    [loadCandidates, refreshRoles],
  );

  useEffect(() => {
    refreshRoles();
    bootstrapSession('new');
  }, [bootstrapSession, refreshRoles]);

  useEffect(() => {
    if (threadRef.current) {
      threadRef.current.scrollTop = threadRef.current.scrollHeight;
    }
  }, [messages, busy]);

  const handleRoleChange = (e) => {
    const slug = e.target.value;
    setActiveSlug(slug);
    bootstrapSession(slug);
  };

  const handleNewRole = () => {
    setActiveSlug('new');
    bootstrapSession('new');
  };

  const handleSend = async (e) => {
    e.preventDefault();
    const text = input.trim();
    if (!text || busy) return;

    setInput('');
    setMessages((prev) => [...prev, { role: 'user', content: text }]);
    setBusy(true);
    setError(null);

    try {
      const slug = activeSlug || 'new';
      const data = await sendChatMessage(slug, text);
      if (data.role_slug && data.role_slug !== activeSlug) {
        setActiveSlug(data.role_slug);
      }
      if (data.assistant_message) {
        setMessages((prev) => [
          ...prev,
          { role: 'assistant', content: data.assistant_message },
        ]);
      }
      if (data.summary) setSummary(data.summary);

      if (data.action === 'PULL_BATCH' || data.action === 'SHOW_TABLE') {
        if (data.candidates) {
          if (data.action === 'PULL_BATCH') {
            setCandidates((prev) => {
              const ids = new Set((data.candidates || []).map((c) => c.id));
              const rest = prev.filter((c) => !ids.has(c.id));
              return [...(data.candidates || []), ...rest];
            });
          } else {
            setCandidates(data.candidates);
          }
        } else if (data.role_slug) {
          await loadCandidates(data.role_slug);
        }
        await refreshRoles();
      }
    } catch (err) {
      setError(err.message);
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', content: `Error: ${err.message}` },
      ]);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="sourcing">
      <header className="sourcing__toolbar">
        <div className="sourcing__brand">Contra6 Sourcing</div>
        <label className="sourcing__role-picker">
          <span className="sourcing__label">Role</span>
          <select
            value={activeSlug}
            onChange={handleRoleChange}
            disabled={busy}
            aria-label="Select role"
          >
            <option value="new">— new role —</option>
            {roles.map((r) => (
              <option key={r.slug} value={r.slug}>
                {r.role_name}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          className="btn btn--ghost"
          onClick={handleNewRole}
          disabled={busy}
        >
          Start new role
        </button>
        {summary && <p className="sourcing__summary mono">{summary}</p>}
      </header>

      {error && (
        <p className="sourcing__error" role="alert">
          {error}
        </p>
      )}

      <div className="sourcing__panes">
        <section className="sourcing__chat" aria-label="Sourcing chat">
          <div className="sourcing__thread" ref={threadRef}>
            {messages.map((m, i) => (
              <div
                key={`${m.role}-${i}`}
                className={`sourcing__bubble sourcing__bubble--${m.role}`}
              >
                <pre className="sourcing__bubble-text">{m.content}</pre>
              </div>
            ))}
            {busy && (
              <div className="sourcing__bubble sourcing__bubble--assistant sourcing__bubble--pending">
                <span className="mono">…</span>
              </div>
            )}
          </div>
          <form className="sourcing__composer" onSubmit={handleSend}>
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Type a reply…"
              disabled={busy}
              aria-label="Chat message"
            />
            <button type="submit" className="btn btn--primary" disabled={busy || !input.trim()}>
              Send
            </button>
          </form>
        </section>

        <section className="sourcing__results" aria-label="Pulled candidates">
          <div className="sourcing__results-head">
            <h2 className="sourcing__results-title">Results</h2>
            <span className="mono sourcing__results-count">{candidates.length}</span>
          </div>
          <div className="sourcing__table-wrap">
            <table className="sourcing-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Title @ Company</th>
                  <th>Location</th>
                  <th>Headline</th>
                  <th>LinkedIn</th>
                </tr>
              </thead>
              <tbody>
                {candidates.length === 0 && (
                  <tr>
                    <td colSpan={5} className="sourcing-table__empty">
                      No profiles pulled yet for this role.
                    </td>
                  </tr>
                )}
                {candidates.map((c) => {
                  const name =
                    `${c.first_name ?? ''} ${c.last_name ?? ''}`.trim() || '—';
                  const titleCo = [c.current_title, c.current_company]
                    .filter(Boolean)
                    .join(' @ ');
                  return (
                    <tr key={c.id || c.linkedin_url}>
                      <td>{name}</td>
                      <td>{titleCo || '—'}</td>
                      <td>{c.location || '—'}</td>
                      <td className="sourcing-table__headline">{c.headline || '—'}</td>
                      <td>
                        {c.linkedin_url ? (
                          <a
                            href={c.linkedin_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="sourcing-table__link"
                            aria-label={`Open LinkedIn for ${name}`}
                          >
                            <ExternalLinkIcon />
                          </a>
                        ) : (
                          '—'
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </div>
  );
}
