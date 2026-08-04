import { useCallback, useEffect, useState } from 'react';
import RolePicker from './RolePicker';
import { useRole } from '../lib/roleContext';
import { fetchRoleScores, saveRoleJd, scoreRole } from '../lib/sourcingApi';

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

/** Pipeline total_score is 0–1; show as a clear percentage. */
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

function ScoreCard({ card }) {
  const [open, setOpen] = useState(false);
  const name = candidateName(card);
  const signals = Array.isArray(card.matched_signals) ? card.matched_signals : [];
  const reasoning = card.reasoning || '';

  return (
    <article className="score-card">
      <div className="score-card__top">
        <div className="score-card__identity">
          <h3 className="score-card__name">{name}</h3>
          <p className="score-card__title">{titleAtCompany(card)}</p>
        </div>
        <div className="score-card__score mono" title={`raw: ${card.total_score}`}>
          {formatScore(card.total_score)}
        </div>
        {card.linkedin_url && (
          <a
            href={card.linkedin_url}
            target="_blank"
            rel="noopener noreferrer"
            className="score-card__link"
            aria-label={`Open LinkedIn for ${name}`}
          >
            <ExternalLinkIcon />
          </a>
        )}
      </div>

      {signals.length > 0 && (
        <ul className="score-card__signals">
          {signals.map((s) => (
            <li key={s} className="score-card__signal">
              {s}
            </li>
          ))}
        </ul>
      )}

      {reasoning && (
        <div className="score-card__reasoning">
          <button
            type="button"
            className="score-card__reasoning-toggle"
            aria-expanded={open}
            onClick={() => setOpen((v) => !v)}
          >
            {open ? 'Hide reasoning' : 'Show reasoning'}
          </button>
          {open && <p className="score-card__reasoning-body">{reasoning}</p>}
        </div>
      )}
    </article>
  );
}

export default function ScoringScreen() {
  const { activeSlug, activeRole, refreshRoles, setError: setRoleError } = useRole();

  const [jdText, setJdText] = useState('');
  const [hasJd, setHasJd] = useState(false);
  const [candidates, setCandidates] = useState([]);
  const [skippedIncomplete, setSkippedIncomplete] = useState(0);
  const [incompleteCandidates, setIncompleteCandidates] = useState([]);
  const [scoreSummary, setScoreSummary] = useState(null);
  const [loading, setLoading] = useState(false);
  const [scoring, setScoring] = useState(false);
  const [savingJd, setSavingJd] = useState(false);
  const [error, setError] = useState(null);

  const loadScores = useCallback(async (slug) => {
    if (!slug) {
      setCandidates([]);
      setIncompleteCandidates([]);
      setSkippedIncomplete(0);
      setScoreSummary(null);
      setJdText('');
      setHasJd(false);
      return;
    }
    setLoading(true);
    setError(null);
    setRoleError(null);
    try {
      const data = await fetchRoleScores(slug);
      setCandidates(data.candidates || []);
      setIncompleteCandidates(data.incomplete_candidates || []);
      setSkippedIncomplete(data.skipped_incomplete || 0);
      setScoreSummary(null);
      setJdText(data.jd_text || '');
      setHasJd(Boolean(data.has_jd));
    } catch (err) {
      setError(err.message);
      setCandidates([]);
      setIncompleteCandidates([]);
      setSkippedIncomplete(0);
    } finally {
      setLoading(false);
    }
  }, [setRoleError]);

  useEffect(() => {
    loadScores(activeSlug);
  }, [activeSlug, loadScores]);

  const handleSaveJd = async (e) => {
    e.preventDefault();
    if (!activeSlug || !jdText.trim() || savingJd) return;
    setSavingJd(true);
    setError(null);
    try {
      const data = await saveRoleJd(activeSlug, jdText.trim());
      setHasJd(Boolean(data.has_jd));
      setJdText(data.jd_text || jdText.trim());
      await refreshRoles();
    } catch (err) {
      setError(err.message);
    } finally {
      setSavingJd(false);
    }
  };

  const handleScore = async () => {
    if (!activeSlug || scoring) return;
    setScoring(true);
    setError(null);
    try {
      const data = await scoreRole(activeSlug);
      setCandidates(data.candidates || []);
      setIncompleteCandidates(data.incomplete_candidates || []);
      setSkippedIncomplete(data.skipped_incomplete || 0);
      setScoreSummary(data.summary || null);
      setHasJd(Boolean(data.has_jd));
      if (data.jd_text != null) setJdText(data.jd_text);
      await refreshRoles();
    } catch (err) {
      setError(err.message);
    } finally {
      setScoring(false);
    }
  };

  const hasScores = candidates.length > 0;
  const showResults =
    hasScores || (Boolean(scoreSummary) && skippedIncomplete > 0);
  const showJdStep = Boolean(activeSlug) && !hasScores && !hasJd && !scoreSummary;
  const showScoreCta = Boolean(activeSlug) && !hasScores && hasJd && !scoreSummary;
  const busy = loading || scoring || savingJd;

  return (
    <div className="scoring">
      <header className="scoring__toolbar">
        <div className="scoring__brand">Scoring</div>
        <RolePicker busy={busy} />
        {showResults && activeSlug && (
          <button
            type="button"
            className="btn btn--ghost"
            onClick={handleScore}
            disabled={scoring}
          >
            {scoring ? 'Re-scoring…' : 'Re-score'}
          </button>
        )}
        {activeRole && hasJd && (
          <span className="scoring__jd-badge mono">JD saved</span>
        )}
      </header>

      {error && (
        <p className="scoring__error" role="alert">
          {error}
        </p>
      )}

      {!activeSlug && (
        <div className="scoring__empty">
          <h1 className="scoring__empty-title">
            Score a <span className="it">role</span>
          </h1>
          <p className="scoring__empty-body">
            Select a sourced role to paste a job description and rank its candidates.
          </p>
        </div>
      )}

      {activeSlug && loading && (
        <p className="scoring__status mono">Loading scores…</p>
      )}

      {activeSlug && !loading && showJdStep && (
        <section className="scoring__jd" aria-label="Job description">
          <h2 className="scoring__section-title">Job description</h2>
          <p className="scoring__section-body">
            Paste the JD for {activeRole?.role_name || activeSlug}. Scoring uses this
            text against sourced profiles.
          </p>
          <form className="scoring__jd-form" onSubmit={handleSaveJd}>
            <textarea
              className="scoring__jd-input"
              value={jdText}
              onChange={(e) => setJdText(e.target.value)}
              rows={14}
              placeholder="Paste the full job description here…"
              disabled={savingJd}
              aria-label="Job description"
            />
            <button
              type="submit"
              className="btn btn--primary"
              disabled={savingJd || !jdText.trim()}
            >
              {savingJd ? 'Saving…' : 'Save JD'}
            </button>
          </form>
        </section>
      )}

      {activeSlug && !loading && showScoreCta && (
        <section className="scoring__ready" aria-label="Ready to score">
          <h2 className="scoring__section-title">Ready to score</h2>
          <p className="scoring__section-body">
            JD is saved. Run scoring against all sourced candidates for this role.
          </p>
          <button
            type="button"
            className="btn btn--primary"
            onClick={handleScore}
            disabled={scoring}
          >
            {scoring ? 'Scoring…' : 'Score candidates'}
          </button>
          {scoring && (
            <p className="scoring__status mono">
              Scoring candidates — this can take a minute
            </p>
          )}
        </section>
      )}

      {activeSlug && !loading && scoring && hasScores && (
        <p className="scoring__status mono">
          Scoring candidates — this can take a minute
        </p>
      )}

      {activeSlug && !loading && showResults && (
        <section className="scoring__results" aria-label="Scored candidates">
          <div className="scoring__results-head">
            <h2 className="scoring__section-title">Ranked candidates</h2>
            <span className="mono scoring__results-count">{candidates.length}</span>
          </div>
          {skippedIncomplete > 0 && (
            <p className="scoring__skip-banner mono" role="status">
              {skippedIncomplete} candidate
              {skippedIncomplete === 1 ? '' : 's'} skipped — incomplete profile
              data
              {scoreSummary ? ` · ${scoreSummary}` : ''}
            </p>
          )}
          {candidates.length > 0 ? (
            <div className="scoring__cards">
              {candidates.map((c) => (
                <ScoreCard key={c.id || c.candidate_id || c.linkedin_url} card={c} />
              ))}
            </div>
          ) : (
            <p className="scoring__section-body">
              No complete profiles to rank. Retry Full enrich from Sourcing for
              thin profiles.
            </p>
          )}
          {incompleteCandidates.length > 0 && (
            <div className="scoring__incomplete" aria-label="Incomplete profiles">
              <h3 className="scoring__incomplete-title">Not scored</h3>
              <ul className="scoring__incomplete-list">
                {incompleteCandidates.map((c) => {
                  const name = candidateName(c);
                  return (
                    <li key={c.id || c.candidate_id || c.linkedin_url}>
                      <span>{name}</span>
                      <span className="scoring__incomplete-status mono">
                        insufficient data — not scored
                      </span>
                    </li>
                  );
                })}
              </ul>
            </div>
          )}
        </section>
      )}
    </div>
  );
}
