import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import RolePicker from './RolePicker';
import { useRole } from '../lib/roleContext';
import {
  fetchRoleCandidates,
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
  const {
    activeSlug,
    selectRole,
    refreshRoles,
    archiveRole,
    error: roleError,
    setError: setRoleError,
  } = useRole();

  const [sessionId, setSessionId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [candidates, setCandidates] = useState([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [summary, setSummary] = useState(null);
  const [chatWidth, setChatWidth] = useState(CHAT_DEFAULT);
  const threadRef = useRef(null);
  const inputRef = useRef(null);
  const dragRef = useRef({ active: false, startX: 0, startW: CHAT_DEFAULT });
  const bootstrappedFor = useRef(undefined);

  const chatSlug = activeSlug || 'new';
  const displayError = error || roleError;

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

  const applySessionMeta = useCallback(
    (data) => {
      if (data.session_id) setSessionId(data.session_id);
      if (data.role_slug) {
        const next =
          data.role_slug === 'new' || !data.role_slug ? null : data.role_slug;
        selectRole(next);
      }
    },
    [selectRole],
  );

  const bootstrapSession = useCallback(
    async (slug) => {
      const target = slug || 'new';
      setBusy(true);
      setError(null);
      setRoleError(null);
      setSummary(null);
      setSessionId(null);
      try {
        const data = await startSession(target);
        applySessionMeta(data);
        setMessages(
          data.assistant_message
            ? [{ role: 'assistant', content: data.assistant_message }]
            : [],
        );
        if (data.candidates) setCandidates(data.candidates);
        else if (data.role_slug && data.role_slug !== 'new')
          await loadCandidates(data.role_slug);
        else setCandidates([]);
        await refreshRoles();
      } catch (err) {
        setError(err.message);
      } finally {
        setBusy(false);
      }
    },
    [applySessionMeta, loadCandidates, refreshRoles, setRoleError],
  );

  // Bootstrap chat when the shared role selection changes (including first mount).
  useEffect(() => {
    const key = activeSlug || 'new';
    if (bootstrappedFor.current === key) return;
    bootstrappedFor.current = key;
    bootstrapSession(key);
  }, [activeSlug, bootstrapSession]);

  useEffect(() => {
    if (threadRef.current) {
      threadRef.current.scrollTop = threadRef.current.scrollHeight;
    }
  }, [messages, busy]);

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

  const handleResizeStart = (e) => {
    e.preventDefault();
    dragRef.current = { active: true, startX: e.clientX, startW: chatWidth };
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
  };

  const handleRoleSelect = (slug) => {
    bootstrappedFor.current = undefined;
    selectRole(slug);
  };

  const handleNewRole = () => {
    bootstrappedFor.current = undefined;
    selectRole(null);
  };

  const handleArchive = async (role) => {
    setError(null);
    setRoleError(null);
    try {
      bootstrappedFor.current = undefined;
      await archiveRole(role);
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
      const data = await sendChatMessage(chatSlug, text, sessionId);
      if (data.role_slug && data.role_slug !== 'new') {
        bootstrappedFor.current = data.role_slug;
      }
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

        <RolePicker
          allowNew
          busy={busy}
          onSelect={handleRoleSelect}
          onArchive={handleArchive}
        />

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

      {displayError && (
        <p className="sourcing__error" role="alert">
          {displayError}
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
