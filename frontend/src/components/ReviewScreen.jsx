import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import RolePicker from './RolePicker';
import { useRole } from '../lib/roleContext';
import { fetchReviewQueue, setReviewStatus } from '../lib/sourcingApi';

const TABS = [
  { id: 'shortlisted', label: 'Shortlisted' },
  { id: 'reviewing', label: 'Reviewing' },
  { id: 'benched', label: 'Benched' },
];

/** Preferred radar axis order — keys confirmed from stored component_breakdown. */
const BREAKDOWN_ORDER = [
  'similarity',
  'title',
  'skill',
  'industry',
  'language',
  'location',
  'seniority',
  'experience',
  'experience_relevance',
  'education_relevance',
  'qualification',
  'attrition',
];

const BREAKDOWN_LABELS = {
  similarity: 'Similarity',
  title: 'Title fit',
  skill: 'Skills',
  industry: 'Sector fit',
  language: 'Language',
  location: 'Location',
  attrition: 'Retention',
  seniority: 'Seniority',
  experience: 'Experience',
  qualification: 'Qualification',
  education_relevance: 'Education',
  experience_relevance: 'Exp. relevance',
};

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
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
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
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
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

/** Pipeline total_score is 0–1; same % display as Scoring. */
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

function normalizeBreakdown(raw) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return [];
  const known = BREAKDOWN_ORDER.filter((k) => raw[k] != null);
  const extras = Object.keys(raw).filter((k) => !BREAKDOWN_ORDER.includes(k));
  return [...known, ...extras].map((key) => {
    const n = Number(raw[key]);
    const value = Number.isFinite(n) ? Math.max(0, Math.min(1, n)) : 0;
    return {
      key,
      label: BREAKDOWN_LABELS[key] || key.replace(/_/g, ' '),
      value,
    };
  });
}

function parseSkills(card) {
  if (Array.isArray(card.skills) && card.skills.length) {
    return card.skills.map(String).filter(Boolean);
  }
  const raw = card.top_skills;
  if (!raw) return [];
  if (Array.isArray(raw)) return raw.map(String).filter(Boolean);
  return String(raw)
    .replace(/•/g, ',')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);
}

function polarToCartesian(cx, cy, radius, angleRad) {
  return {
    x: cx + radius * Math.sin(angleRad),
    y: cy - radius * Math.cos(angleRad),
  };
}

function RadarChart({ axes }) {
  const size = 520;
  const cx = size / 2;
  const cy = size / 2;
  const radius = 175;
  const n = axes.length;
  if (n < 3) return null;

  const levels = [0.25, 0.5, 0.75, 1];
  const angleAt = (i) => (i / n) * Math.PI * 2;

  const gridPolygons = levels.map((lvl) =>
    axes
      .map((_, i) => {
        const p = polarToCartesian(cx, cy, radius * lvl, angleAt(i));
        return `${p.x},${p.y}`;
      })
      .join(' '),
  );

  const dataPoints = axes.map((axis, i) =>
    polarToCartesian(cx, cy, radius * axis.value, angleAt(i)),
  );
  const dataPolygon = dataPoints.map((p) => `${p.x},${p.y}`).join(' ');

  return (
    <svg
      className="review-radar"
      viewBox={`0 0 ${size} ${size}`}
      role="img"
      aria-label="Role-fit component breakdown"
    >
      {gridPolygons.map((pts) => (
        <polygon key={pts} className="review-radar__grid" points={pts} />
      ))}
      {axes.map((_, i) => {
        const end = polarToCartesian(cx, cy, radius, angleAt(i));
        return (
          <line
            key={`spoke-${i}`}
            className="review-radar__spoke"
            x1={cx}
            y1={cy}
            x2={end.x}
            y2={end.y}
          />
        );
      })}
      <polygon className="review-radar__area" points={dataPolygon} />
      {dataPoints.map((p, i) => (
        <circle
          key={`dot-${axes[i].key}`}
          className="review-radar__dot"
          cx={p.x}
          cy={p.y}
          r={4}
        />
      ))}
      {axes.map((axis, i) => {
        const labelR = radius + 42;
        const p = polarToCartesian(cx, cy, labelR, angleAt(i));
        return (
          <text
            key={`label-${axis.key}`}
            className="review-radar__label"
            x={p.x}
            y={p.y}
            textAnchor="middle"
            dominantBaseline="middle"
          >
            {axis.label}
          </text>
        );
      })}
    </svg>
  );
}

function JobsIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M4 7h16v12a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7z"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function GapIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M4 12h4M16 12h4M10 12h4"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
      />
      <circle cx="9" cy="12" r="1.5" fill="currentColor" />
      <circle cx="15" cy="12" r="1.5" fill="currentColor" />
    </svg>
  );
}

function formatYearsExp(years) {
  if (years == null || Number.isNaN(Number(years))) return null;
  const n = Number(years);
  if (n <= 0) return null;
  const label = Number.isInteger(n) ? String(n) : n.toFixed(1);
  return `${label} yrs exp.`;
}

function formatGapLabel(months) {
  if (months == null) return 'None';
  if (months < 12) return `${months} mo`;
  const y = Math.floor(months / 12);
  const m = months % 12;
  if (m === 0) return `${y} yr${y === 1 ? '' : 's'}`;
  return `${y}y ${m}mo`;
}

function CareerTimeline({ timeline }) {
  if (!timeline || !Array.isArray(timeline.markers) || timeline.markers.length === 0) {
    return null;
  }
  const gaps = Array.isArray(timeline.gaps) ? timeline.gaps : [];

  return (
    <section className="review-card__section" aria-label="Career timeline">
      <h3 className="review-card__section-title">Career timeline</h3>
      <div className="review-timeline">
        <div className="review-timeline__track" aria-hidden="true">
          <div className="review-timeline__line" />
          {gaps.map((g) => (
            <div
              key={`${g.start_pct}-${g.end_pct}`}
              className="review-timeline__gap"
              style={{
                left: `${g.start_pct}%`,
                width: `${Math.max(1.5, g.end_pct - g.start_pct)}%`,
              }}
              title={g.label}
            >
              <span className="review-timeline__gap-label">{g.label}</span>
            </div>
          ))}
          {timeline.markers.map((m) => (
            <div
              key={`${m.year}-${m.company}-${m.title}`}
              className={`review-timeline__dot${m.present ? ' review-timeline__dot--present' : ''}`}
              style={{ left: `${m.pct}%` }}
              title={[m.title, m.company].filter(Boolean).join(' @ ')}
            />
          ))}
        </div>
        <div className="review-timeline__ends">
          <span>{timeline.start_year}</span>
          <span>{timeline.end_label}</span>
        </div>
      </div>
    </section>
  );
}

function ReviewCard({ card, rank, actions, busy, onAction }) {
  const name = candidateName(card);
  const signals = Array.isArray(card.matched_signals) ? card.matched_signals : [];
  const skills = useMemo(() => parseSkills(card), [card]);
  const axes = useMemo(
    () => normalizeBreakdown(card.component_breakdown),
    [card.component_breakdown],
  );
  const career = card.career || {};
  const reasoning = (card.reasoning || '').trim();
  const headline = (card.headline || '').trim();
  const summary = [headline, reasoning].filter(Boolean).join(' — ');
  const yearsLabel = formatYearsExp(career.years_experience);
  const metaBits = [titleAtCompany(card), card.location, yearsLabel].filter(
    (x) => x && x !== '—',
  );
  const hasCareerStats =
    career.position_count > 0 ||
    career.job_changes != null ||
    career.longest_gap_months !== undefined;

  return (
    <article className="review-card">
      <header className="review-card__header">
        <div className="review-card__header-main">
          <div className="review-card__badges">
            <span className="review-card__badge">Rank #{rank}</span>
            <span
              className="review-card__badge review-card__badge--fit"
              title={`raw total_score: ${card.total_score}`}
            >
              {formatScore(card.total_score)} Match
            </span>
          </div>
          <h2 className="review-card__name">{name}</h2>
          <p className="review-card__meta">
            {metaBits.join(' · ')}
            {card.linkedin_url && (
              <>
                {' · '}
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
              </>
            )}
          </p>
        </div>
        <div className="review-card__header-actions">
          {actions.map((a) => (
            <button
              key={a.status}
              type="button"
              className={`btn review__action review__action--${a.variant}`}
              onClick={() => onAction(a.status)}
              disabled={busy}
            >
              {a.icon === 'bench' && <BenchIcon />}
              {a.icon === 'shortlist' && <ShortlistIcon />}
              {a.label}
            </button>
          ))}
        </div>
      </header>

      {reasoning && (
        <section className="review-card__section" aria-label="Assessment">
          <h3 className="review-card__section-title">Assessment</h3>
          <p className="review-card__reasoning-body">{reasoning}</p>
        </section>
      )}

      <div className="review-card__body">
        {axes.length >= 3 && (
          <section className="review-card__radar-col" aria-label="Role-fit breakdown">
            <h3 className="review-card__section-title">Role-fit breakdown</h3>
            <div className="review-card__radar-wrap">
              <RadarChart axes={axes} />
            </div>
          </section>
        )}

        <div className="review-card__details">
          {summary && (
            <section className="review-card__section review-card__section--flush" aria-label="Summary">
              <h3 className="review-card__section-title">Summary</h3>
              <p className="review-card__summary">{summary}</p>
            </section>
          )}

          <CareerTimeline timeline={career.timeline} />

          {hasCareerStats && career.position_count > 0 && (
            <div className="review-card__stats" aria-label="Career stats">
              <div className="review-stat">
                <span className="review-stat__icon">
                  <JobsIcon />
                </span>
                <div>
                  <div className="review-stat__value">{career.job_changes ?? 0}</div>
                  <div className="review-stat__label">Job Changes</div>
                </div>
              </div>
              <div className="review-stat">
                <span className="review-stat__icon">
                  <GapIcon />
                </span>
                <div>
                  <div className="review-stat__value">
                    {formatGapLabel(career.longest_gap_months)}
                  </div>
                  <div className="review-stat__label">Career Gap</div>
                </div>
              </div>
            </div>
          )}

          {(signals.length > 0 || skills.length > 0) && (
            <div className="review-card__tags-row">
              {signals.length > 0 && (
                <section className="review-card__section review-card__section--flush" aria-label="Strengths">
                  <h3 className="review-card__section-title">Strengths</h3>
                  <ul className="review-card__signals">
                    {signals.map((s) => (
                      <li key={s} className="review-card__signal">
                        {s}
                      </li>
                    ))}
                  </ul>
                </section>
              )}

              {skills.length > 0 && (
                <section className="review-card__section review-card__section--flush" aria-label="Top skills">
                  <h3 className="review-card__section-title">Top skills</h3>
                  <ul className="review-card__signals">
                    {skills.map((s) => (
                      <li key={s} className="review-card__signal review-card__signal--skill">
                        {s}
                      </li>
                    ))}
                  </ul>
                </section>
              )}
            </div>
          )}
        </div>
      </div>
    </article>
  );
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

  const stateRef = useRef({});
  stateRef.current = { candidates, index, tab, busy, activeSlug, counts };

  const loadQueue = useCallback(async (slug, status) => {
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
      setCounts(data.counts || { reviewing: 0, shortlisted: 0, benched: 0 });
      setIndex(0);
    } catch (err) {
      setError(err.message);
      setCandidates([]);
    } finally {
      setLoading(false);
    }
  }, []);

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
    const current = list[i];
    if (!current) return;
    const cid = current.candidate_id || current.id;
    if (!cid) return;
    if (currentTab === nextStatus) return;

    const prevList = list;
    const prevCounts = { ...currentCounts };
    const prevIndex = i;
    const nextList = list.filter((c) => candidateKey(c) !== candidateKey(current));
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
  const rank = card ? index + 1 : 0;

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

          {loading && <p className="review__status mono">Loading queue…</p>}

          {!loading && !card && (
            <div className="review__empty">
              <p className="review__empty-body">{emptyMessage(tab, true)}</p>
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

              <ReviewCard
                card={card}
                rank={rank}
                actions={actions}
                busy={busy}
                onAction={applyStatus}
              />

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
