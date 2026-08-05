import { useCallback, useEffect, useRef, useState } from 'react';
import RolePicker from './RolePicker';
import { useRole } from '../lib/roleContext';
import { fetchReviewQueue, setReviewStatus } from '../lib/sourcingApi';

const TABS = [
  { id: 'shortlisted', label: 'Shortlisted' },
  { id: 'reviewing', label: 'Reviewing' },
  { id: 'benched', label: 'Benched' },
];

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

function BenchIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M18 6L6 18M6 6l12 12"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
      />
    </svg>
  );
}

function ShortlistIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M20 6L9 17l-5-5"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function formatScore(score) {
  if (score == null || Number.isNaN(Number(score))) return '—';
  return `${Math.round(Number(score) * 100)}%`;
}

function candidateName(c) {
  const fromParts = `${c.first_name ?? ''} ${c.last_name ?? ''}`.trim();
  return fromParts || c.name || '—';
}

function titleAtCompany(c) {
  const title = c.current_title || c.title;
  const company = c.current_company;
  return [title, company].filter(Boolean).join(' @ ') || '—';
}

function candidateKey(c) {
  return c.candidate_id || c.id || c.linkedin_url;
}

function emptyMessage(tab, hasRole) {
  if (!hasRole) {
    return 'Select a scored role to start reviewing candidates.';
  }
  if (tab === 'reviewing') {
    return 'No candidates to review. Score more candidates from the Scoring tab first.';
  }
  if (tab === 'shortlisted') {
    return 'No shortlisted candidates yet.';
  }
  return 'No benched candidates yet.';
}

/** Primary actions for the active tab — labels adapt to context. */
function actionButtonsForTab(tab) {
  if (tab === 'reviewing') {
    return [
      { status: 'benched', label: 'Bench', icon: 'bench', variant: 'bench' },
      { status: 'shortlisted', label: 'Shortlist', icon: 'shortlist', variant: 'shortlist' },
    ];
  }
  if (tab === 'shortlisted') {
    return [
      { status: 'benched', label: 'Bench', icon: 'bench', variant: 'bench' },
      { status: 'reviewing', label: 'Move to Reviewing', icon: null, variant: 'ghost' },
    ];
  }
  return [
    { status: 'reviewing', label: 'Move to Reviewing', icon: null, variant: 'ghost' },
    { status: 'shortlisted', label: 'Shortlist', icon: 'shortlist', variant: 'shortlist' },
  ];
}

export default function ReviewScreen() {
  const { activeSlug } = useRole();

  const [tab, setTab] = useState('reviewing');
  const [candidates, setCandidates] = useState([]);
  const [counts, setCounts] = useState({
    reviewing: 0,
    shortlisted: 0,
    benched: 0,
  });
  const [index, setIndex] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  // Keep latest values for keyboard handlers without re-binding constantly.
  const stateRef = useRef({});
  stateRef.current = { candidates, index, tab, busy, activeSlug, counts };

  const loadQueue = useCallback(
    async (slug, status) => {
      if (!slug) {
        setCandidates([]);
        setCounts({ reviewing: 0, shortlisted: 0, benched: 0 });
        setIndex(0);
        return;
      }
      setLoading(true);
      setError(null);
      try {
        const data = await fetchReviewQueue(slug, status);
        setCandidates(data.candidates || []);
        setCounts(
          data.counts || { reviewing: 0, shortlisted: 0, benched: 0 },
        );
        setIndex(0);
      } catch (err) {
        setError(err.message);
        setCandidates([]);
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  useEffect(() => {
    setTab('reviewing');
    setIndex(0);
    loadQueue(activeSlug, 'reviewing');
  }, [activeSlug, loadQueue]);

  const selectTab = (id) => {
    if (id === tab) return;
    setTab(id);
    setIndex(0);
    loadQueue(activeSlug, id);
  };

  const goPrev = useCallback(() => {
    setIndex((i) => Math.max(0, i - 1));
  }, []);

  const goNext = useCallback(() => {
    setIndex((i) => {
      const max = Math.max(0, stateRef.current.candidates.length - 1);
      return Math.min(max, i + 1);
    });
  }, []);

  const applyStatus = useCallback(async (nextStatus) => {
    const {
      candidates: list,
      index: i,
      tab: currentTab,
      busy: isBusy,
      activeSlug: slug,
      counts: currentCounts,
    } = stateRef.current;
    if (!slug || isBusy || !list.length) return;
    const card = list[i];
    if (!card) return;
    const cid = card.candidate_id || card.id;
    if (!cid) return;
    // Already in this status (e.g. S on Shortlisted tab) — no-op.
    if (currentTab === nextStatus) return;

    const prevList = list;
    const prevCounts = { ...currentCounts };
    const prevIndex = i;
    const nextList = list.filter((c) => candidateKey(c) !== candidateKey(card));
    const nextIndex =
      nextList.length === 0 ? 0 : Math.min(i, nextList.length - 1);

    setBusy(true);
    setError(null);
    setCandidates(nextList);
    setIndex(nextIndex);
    setCounts({
      ...currentCounts,
      [currentTab]: Math.max(0, (currentCounts[currentTab] || 0) - 1),
      [nextStatus]: (currentCounts[nextStatus] || 0) + 1,
    });

    try {
      const data = await setReviewStatus(slug, cid, nextStatus);
      if (data.counts) setCounts(data.counts);
    } catch (err) {
      setError(err.message);
      setCandidates(prevList);
      setIndex(prevIndex);
      setCounts(prevCounts);
    } finally {
      setBusy(false);
    }
  }, []);

  // Keyboard: ← → navigate; S shortlist; B bench
  useEffect(() => {
    const onKey = (e) => {
      if (e.target && ['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName)) {
        return;
      }
      if (e.key === 'ArrowLeft') {
        e.preventDefault();
        goPrev();
      } else if (e.key === 'ArrowRight') {
        e.preventDefault();
        goNext();
      } else if (e.key === 's' || e.key === 'S') {
        e.preventDefault();
        applyStatus('shortlisted');
      } else if (e.key === 'b' || e.key === 'B') {
        e.preventDefault();
        applyStatus('benched');
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [goPrev, goNext, applyStatus]);

  const total = candidates.length;
  const card = total > 0 ? candidates[Math.min(index, total - 1)] : null;
  const actions = actionButtonsForTab(tab);
  const name = card ? candidateName(card) : '';
  const signals = card && Array.isArray(card.matched_signals) ? card.matched_signals : [];
  const reasoning = card?.reasoning || '';

  return (
    <div className="review">
      <header className="review__toolbar">
        <div className="review__brand">Review</div>
        <RolePicker busy={loading || busy} />
      </header>

      {error && (
        <p className="review__error" role="alert">
          {error}
        </p>
      )}

      {!activeSlug && (
        <div className="review__empty">
          <h1 className="review__empty-title">
            Review a <span className="it">role</span>
          </h1>
          <p className="review__empty-body">{emptyMessage(tab, false)}</p>
        </div>
      )}

      {activeSlug && (
        <>
          <nav className="review__tabs" aria-label="Review status">
            {TABS.map((t) => (
              <button
                key={t.id}
                type="button"
                className={`review__tab${tab === t.id ? ' review__tab--active' : ''}`}
                onClick={() => selectTab(t.id)}
                disabled={loading}
              >
                {t.label}
                <span className="review__tab-count mono">{counts[t.id] ?? 0}</span>
              </button>
            ))}
          </nav>

          {loading && (
            <p className="review__status mono">Loading queue…</p>
          )}

          {!loading && !card && (
            <div className="review__empty">
              <p className="review__empty-body">
                {emptyMessage(tab, true)}
              </p>
            </div>
          )}

          {!loading && card && (
            <div className="review__stage">
              <div className="review__progress">
                <button
                  type="button"
                  className="btn btn--ghost review__nav-btn"
                  onClick={goPrev}
                  disabled={index <= 0 || busy}
                  aria-label="Previous candidate"
                >
                  ←
                </button>
                <span className="mono review__progress-label">
                  {index + 1} of {total}
                </span>
                <button
                  type="button"
                  className="btn btn--ghost review__nav-btn"
                  onClick={goNext}
                  disabled={index >= total - 1 || busy}
                  aria-label="Next candidate"
                >
                  →
                </button>
              </div>

              <article className="review-card">
                <div className="review-card__top">
                  <div className="review-card__identity">
                    <h2 className="review-card__name">{name}</h2>
                    <p className="review-card__title">{titleAtCompany(card)}</p>
                    {card.location && (
                      <p className="review-card__location">{card.location}</p>
                    )}
                  </div>
                  <div className="review-card__score-block">
                    <div
                      className="review-card__score mono"
                      title={`raw: ${card.total_score}`}
                    >
                      {formatScore(card.total_score)} Match
                    </div>
                    {card.linkedin_url && (
                      <a
                        href={card.linkedin_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="review-card__link"
                        aria-label={`Open LinkedIn for ${name}`}
                      >
                        <ExternalLinkIcon />
                        LinkedIn
                      </a>
                    )}
                  </div>
                </div>

                {signals.length > 0 && (
                  <ul className="review-card__signals">
                    {signals.map((s) => (
                      <li key={s} className="review-card__signal">
                        {s}
                      </li>
                    ))}
                  </ul>
                )}

                {reasoning && (
                  <div className="review-card__reasoning">
                    <h3 className="review-card__reasoning-label mono">Summary</h3>
                    <p className="review-card__reasoning-body">{reasoning}</p>
                  </div>
                )}
              </article>

              <div className="review__actions">
                {actions.map((a) => (
                  <button
                    key={a.status}
                    type="button"
                    className={`btn review__action review__action--${a.variant}`}
                    onClick={() => applyStatus(a.status)}
                    disabled={busy}
                  >
                    {a.icon === 'bench' && <BenchIcon />}
                    {a.icon === 'shortlist' && <ShortlistIcon />}
                    {a.label}
                  </button>
                ))}
              </div>

              <p className="review__hints mono">
                ← → navigate · S shortlist · B bench
              </p>
            </div>
          )}
        </>
      )}
    </div>
  );
}
