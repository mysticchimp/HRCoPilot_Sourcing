import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  archiveRole,
  fetchRoleCandidates,
  listRoles,
  sendChatMessage,
  startSession,
} from '../lib/sourcingApi';

const CHAT_MIN = 240;
const CHAT_MAX = 600;
const CHAT_DEFAULT = 420;

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
  const [sessionId, setSessionId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [candidates, setCandidates] = useState([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [summary, setSummary] = useState(null);
  const [chatWidth, setChatWidth] = useState(CHAT_DEFAULT);
  const [menuOpen, setMenuOpen] = useState(false);
  const threadRef = useRef(null);
  const inputRef = useRef(null);
  const menuRef = useRef(null);
  const dragRef = useRef({ active: false, startX: 0, startW: CHAT_DEFAULT });

  const activeRole = roles.find((r) => r.slug === activeSlug) || null;
  const activeLabel =
    activeSlug === 'new' ? '— new role —' : activeRole?.role_name || activeSlug;

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

  const applySessionMeta = useCallback((data) => {
    if (data.session_id) setSessionId(data.session_id);
    if (data.role_slug) {
      setActiveSlug(data.role_slug === 'new' || !data.role_slug ? 'new' : data.role_slug);
    }
  }, []);

  const bootstrapSession = useCallback(
    async (slug) => {
      setBusy(true);
      setError(null);
      setSummary(null);
      setSessionId(null);
      try {
        const data = await startSession(slug);
        applySessionMeta(data);
        setMessages(
          data.assistant_message
            ? [{ role: 'assistant', content: data.assistant_message }]
            : [],
        );
        if (data.candidates) setCandidates(data.candidates);
        else if (data.role_slug && data.role_slug !== 'new') await loadCandidates(data.role_slug);
        else setCandidates([]);
        await refreshRoles();
      } catch (err) {
        setError(err.message);
      } finally {
        setBusy(false);
      }
    },
    [applySessionMeta, loadCandidates, refreshRoles],
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

  // Re-focus after send + after the assistant reply renders. The input is
  // disabled while busy, which drops focus; restore it once busy clears.
  useEffect(() => {
    if (!busy && inputRef.current) {
      inputRef.current.focus();
    }
  }, [messages, busy]);

  useEffect(() => {
    const onMove = (e) => {
      if (!dragRef.current.active) return;
      const delta = e.clientX - dragRef.current.startX;
      const next = Math.min(
        CHAT_MAX,
        Math.max(CHAT_MIN, dragRef.current.startW + delta),
      );
      setChatWidth(next);
    };
    const onUp = () => {
      if (!dragRef.current.active) return;
      dragRef.current.active = false;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    return () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
  }, []);

  useEffect(() => {
    if (!menuOpen) return undefined;
    const onDoc = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)) {
        setMenuOpen(false);
      }
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [menuOpen]);

  const handleResizeStart = (e) => {
    e.preventDefault();
    dragRef.current = { active: true, startX: e.clientX, startW: chatWidth };
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
  };

  const selectRole = (slug) => {
    setMenuOpen(false);
    setActiveSlug(slug);
    bootstrapSession(slug);
  };

  const handleNewRole = () => {
    setMenuOpen(false);
    setActiveSlug('new');
    bootstrapSession('new');
  };

  const handleArchive = async (role, e) => {
    e.stopPropagation();
    const ok = window.confirm(
      `Archive ${role.role_name}? You can restore it later from Archived Roles.`,
    );
    if (!ok) return;
    setError(null);
    try {
      await archiveRole(role.slug);
      setMenuOpen(false);
      if (activeSlug === role.slug) {
        setActiveSlug('new');
        await bootstrapSession('new');
      } else {
        await refreshRoles();
      }
    } catch (err) {
      setError(err.message);
    }
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
      const data = await sendChatMessage(slug, text, sessionId);
      applySessionMeta(data);
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
        <div className="sourcing__brand">Sourcing</div>

        <div className="sourcing__role-picker" ref={menuRef}>
          <span className="sourcing__label">Role</span>
          <button
            type="button"
            className="role-menu__trigger"
            disabled={busy}
            aria-haspopup="listbox"
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((o) => !o)}
          >
            {activeLabel}
          </button>
          {menuOpen && (
            <ul className="role-menu" role="listbox">
              <li>
                <button
                  type="button"
                  className="role-menu__item"
                  onClick={() => selectRole('new')}
                >
                  — new role —
                </button>
              </li>
              {roles.map((r) => (
                <li key={r.slug} className="role-menu__row">
                  <button
                    type="button"
                    className={`role-menu__item${
                      r.slug === activeSlug ? ' role-menu__item--active' : ''
                    }`}
                    onClick={() => selectRole(r.slug)}
                  >
                    {r.role_name}
                  </button>
                  <button
                    type="button"
                    className="role-menu__archive"
                    aria-label={`Archive ${r.role_name}`}
                    title="Archive role"
                    onClick={(e) => handleArchive(r, e)}
                  >
                    ×
                  </button>
                </li>
              ))}
            </ul>
          )}
          {activeRole && (
            <span className="sourcing__role-id mono" title={activeRole.id}>
              ID: {activeRole.id}
            </span>
          )}
        </div>

        <button
          type="button"
          className="btn btn--ghost"
          onClick={handleNewRole}
          disabled={busy}
        >
          Start new role
        </button>
        <Link to="/sourcing/archived" className="sourcing__archived-link">
          Archived Roles
        </Link>
        {summary && <p className="sourcing__summary mono">{summary}</p>}
      </header>

      {error && (
        <p className="sourcing__error" role="alert">
          {error}
        </p>
      )}

      <div className="sourcing__panes">
        <section
          className="sourcing__chat"
          aria-label="Sourcing chat"
          style={{ width: chatWidth, flexBasis: chatWidth }}
        >
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
              ref={inputRef}
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

        <div
          className="sourcing__resize"
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize chat panel"
          aria-valuemin={CHAT_MIN}
          aria-valuemax={CHAT_MAX}
          aria-valuenow={chatWidth}
          onMouseDown={handleResizeStart}
        />

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
