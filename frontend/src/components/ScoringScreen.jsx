import { useCallback, useEffect, useState } from 'react';
import RolePicker from './RolePicker';
import { useRole } from '../lib/roleContext';
import {
  fetchRoleScores,
  narrateRole,
  saveRoleJd,
  scoreRole,
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

/** Pretty-print JSON for display when the role stores a scoring brief. */
function formatJdForDisplay(text, hasParsedJd) {
  if (!hasParsedJd || !text) return text || '';
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text;
  }
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

function JdModal({
  jdText,
  editing,
  draft,
  saving,
  error,
  hasParsedJd,
  onDraftChange,
  onEdit,
  onSave,
  onCancel,
}) {
  const displayBody = formatJdForDisplay(jdText, hasParsedJd);
  return (
    <div className="scoring-modal" role="presentation">
      <button
        type="button"
        className="scoring-modal__backdrop"
        aria-label="Close"
        onClick={onCancel}
      />
      <div
        className="scoring-modal__panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="jd-modal-title"
      >
        <h2 id="jd-modal-title" className="scoring-modal__title">
          Job description
        </h2>
        {editing ? (
          <textarea
            className={`scoring__jd-input scoring-modal__textarea${
              hasParsedJd || (draft || '').trim().startsWith('{')
                ? ' scoring__jd-input--mono'
                : ''
            }`}
            value={draft}
            onChange={(e) => onDraftChange(e.target.value)}
            disabled={saving}
            aria-label="Edit job description"
            autoFocus
            spellCheck={!((draft || '').trim().startsWith('{'))}
          />
        ) : (
          <pre
            className={`scoring-modal__body${
              hasParsedJd ? ' scoring-modal__body--mono' : ''
            }`}
          >
            {displayBody || '—'}
          </pre>
        )}
        {error && (
          <p className="scoring-modal__error" role="alert">
            {error}
          </p>
        )}
        <div className="scoring-modal__actions">
          {editing ? (
            <>
              <button
                type="button"
                className="btn btn--primary"
                onClick={onSave}
                disabled={saving || !draft.trim()}
              >
                {saving ? 'Saving…' : 'Save'}
              </button>
              <button
                type="button"
                className="btn btn--ghost"
                onClick={onCancel}
                disabled={saving}
              >
                Cancel
              </button>
            </>
          ) : (
            <>
              <button type="button" className="btn btn--primary" onClick={onEdit}>
                Edit
              </button>
              <button type="button" className="btn btn--ghost" onClick={onCancel}>
                Cancel
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export default function ScoringScreen() {
  const { activeSlug, activeRole, refreshRoles, setError: setRoleError } = useRole();

  const [jdText, setJdText] = useState('');
  const [hasJd, setHasJd] = useState(false);
  const [hasParsedJd, setHasParsedJd] = useState(false);
  const [candidates, setCandidates] = useState([]);
  const [skippedIncomplete, setSkippedIncomplete] = useState(0);
  const [incompleteCandidates, setIncompleteCandidates] = useState([]);
  const [scoreSummary, setScoreSummary] = useState(null);
  const [scoringMode, setScoringMode] = useState(null);
  const [loading, setLoading] = useState(false);
  const [scoring, setScoring] = useState(false);
  const [scoringWaitLong, setScoringWaitLong] = useState(false);
  const [narrating, setNarrating] = useState(false);
  const [narrativeSummary, setNarrativeSummary] = useState(null);
  const [savingJd, setSavingJd] = useState(false);
  const [error, setError] = useState(null);
  const [jdModalOpen, setJdModalOpen] = useState(false);
  const [jdEditing, setJdEditing] = useState(false);
  const [jdDraft, setJdDraft] = useState('');
  const [jdModalError, setJdModalError] = useState(null);
  const [jdStaleScores, setJdStaleScores] = useState(false);

  const applyJdMeta = (data, fallbackText) => {
    const nextText = data.jd_text != null ? data.jd_text : fallbackText;
    const parsed =
      data.has_parsed_jd != null
        ? Boolean(data.has_parsed_jd)
        : data.jd_mode === 'parsed';
    setHasJd(Boolean(data.has_jd));
    setHasParsedJd(parsed);
    if (nextText != null) {
      setJdText(formatJdForDisplay(nextText, parsed));
    }
  };

  const closeJdModal = () => {
    setJdModalOpen(false);
    setJdEditing(false);
    setJdDraft('');
    setJdModalError(null);
  };

  const openJdModal = () => {
    setJdDraft(formatJdForDisplay(jdText, hasParsedJd));
    setJdEditing(false);
    setJdModalError(null);
    setJdModalOpen(true);
  };

  const loadScores = useCallback(
    async (slug) => {
      if (!slug) {
        setCandidates([]);
        setIncompleteCandidates([]);
        setSkippedIncomplete(0);
        setScoreSummary(null);
        setScoringMode(null);
        setNarrativeSummary(null);
        setJdText('');
        setHasJd(false);
        setHasParsedJd(false);
        setJdStaleScores(false);
        setJdModalOpen(false);
        setJdEditing(false);
        setJdModalError(null);
        return;
      }
      setLoading(true);
      setError(null);
      setRoleError(null);
      setJdStaleScores(false);
      setJdModalOpen(false);
      setJdEditing(false);
      setJdModalError(null);
      try {
        const data = await fetchRoleScores(slug);
        setCandidates(data.candidates || []);
        setIncompleteCandidates(data.incomplete_candidates || []);
        setSkippedIncomplete(data.skipped_incomplete || 0);
        setScoreSummary(null);
        setScoringMode(data.scoring_mode || null);
        setNarrativeSummary(null);
        applyJdMeta(data, '');
      } catch (err) {
        setError(err.message);
        setCandidates([]);
        setIncompleteCandidates([]);
        setSkippedIncomplete(0);
        setScoringMode(null);
      } finally {
        setLoading(false);
      }
    },
    [setRoleError],
  );

  useEffect(() => {
    loadScores(activeSlug);
  }, [activeSlug, loadScores]);

  useEffect(() => {
    if (!scoring) {
      setScoringWaitLong(false);
      return undefined;
    }
    const timer = setTimeout(() => setScoringWaitLong(true), 60_000);
    return () => clearTimeout(timer);
  }, [scoring]);

  const scoringStatusText = scoringWaitLong
    ? 'Still scoring — large batches can take a few minutes'
    : 'Scoring candidates — this can take a minute';

  const handleSaveJd = async (e) => {
    e.preventDefault();
    if (!activeSlug || !jdText.trim() || savingJd) return;
    setSavingJd(true);
    setError(null);
    try {
      const data = await saveRoleJd(activeSlug, jdText.trim());
      applyJdMeta(data, jdText.trim());
      await refreshRoles();
    } catch (err) {
      setError(err.message);
    } finally {
      setSavingJd(false);
    }
  };

  const handleSaveJdFromModal = async () => {
    if (!activeSlug || !jdDraft.trim() || savingJd) return;
    setSavingJd(true);
    setJdModalError(null);
    setError(null);
    try {
      const data = await saveRoleJd(activeSlug, jdDraft.trim());
      applyJdMeta(data, jdDraft.trim());
      if (candidates.length > 0 || Boolean(scoreSummary)) {
        setJdStaleScores(true);
      }
      await refreshRoles();
      closeJdModal();
    } catch (err) {
      setJdModalError(err.message);
    } finally {
      setSavingJd(false);
    }
  };

  const handleScore = async () => {
    if (!activeSlug || scoring) return;
    setScoring(true);
    setError(null);
    setNarrativeSummary(null);
    try {
      const data = await scoreRole(activeSlug);
      setCandidates(data.candidates || []);
      setIncompleteCandidates(data.incomplete_candidates || []);
      setSkippedIncomplete(data.skipped_incomplete || 0);
      setScoreSummary(data.summary || null);
      setScoringMode(data.scoring_mode || null);
      applyJdMeta(data, jdText);
      setJdStaleScores(false);
      await refreshRoles();
    } catch (err) {
      setError(err.message);
    } finally {
      setScoring(false);
    }
  };

  const handleNarrate = async () => {
    if (!activeSlug || narrating || scoring) return;
    setNarrating(true);
    setError(null);
    setNarrativeSummary(null);
    try {
      const data = await narrateRole(activeSlug);
      setNarrativeSummary(data.summary || null);
      const refreshed = await fetchRoleScores(activeSlug);
      setCandidates(refreshed.candidates || []);
      setIncompleteCandidates(refreshed.incomplete_candidates || []);
      setSkippedIncomplete(refreshed.skipped_incomplete || 0);
      setScoringMode(refreshed.scoring_mode || null);
      applyJdMeta(refreshed, jdText);
    } catch (err) {
      setError(err.message);
    } finally {
      setNarrating(false);
    }
  };

  const hasScores = candidates.length > 0;
  const showResults =
    hasScores || (Boolean(scoreSummary) && skippedIncomplete > 0);
  const showJdStep = Boolean(activeSlug) && !hasScores && !hasJd && !scoreSummary;
  const showScoreCta = Boolean(activeSlug) && !hasScores && hasJd && !scoreSummary;
  const busy = loading || scoring || narrating || savingJd;
  const jdModeLabel = hasParsedJd ? 'Brief (JSON)' : 'Text (AI-parsed)';

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
            disabled={scoring || narrating}
          >
            {scoring ? 'Re-scoring…' : 'Re-score'}
          </button>
        )}
        {showResults && activeSlug && hasScores && (
          <button
            type="button"
            className="btn btn--ghost"
            onClick={handleNarrate}
            disabled={scoring || narrating}
            title="Generate LLM summary + assessment (cached by JD hash)"
          >
            {narrating ? 'Generating narratives…' : 'Generate Narratives'}
          </button>
        )}
        {activeRole && hasJd && (
          <div className="scoring__jd-badges">
            <button
              type="button"
              className="scoring__jd-badge mono"
              onClick={openJdModal}
              title="View or edit saved JD"
            >
              JD saved
            </button>
            <span
              className={`scoring__jd-mode mono${
                hasParsedJd
                  ? ' scoring__jd-mode--parsed'
                  : ' scoring__jd-mode--text'
              }`}
              title={
                hasParsedJd
                  ? 'Structured scoring brief — used as-is (no AI parse)'
                  : 'Plain JD text — Claude parses on score'
              }
            >
              {jdModeLabel}
            </span>
          </div>
        )}
      </header>

      {error && (
        <p className="scoring__error" role="alert">
          {error}
        </p>
      )}

      {narrativeSummary && showResults && (
        <p className="scoring__status mono" role="status">
          {narrativeSummary}
        </p>
      )}

      {jdStaleScores && showResults && (
        <p className="scoring__stale-banner mono" role="status">
          JD updated — scores shown are from the previous JD. Re-score to update.
        </p>
      )}

      {!activeSlug && (
        <div className="scoring__empty">
          <h1 className="scoring__empty-title">
            Score a <span className="it">role</span>
          </h1>
          <p className="scoring__empty-body">
            Select a sourced role to paste a job description (or a JSON scoring
            brief) and rank its candidates.
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
            Paste the JD for {activeRole?.role_name || activeSlug}, or paste a
            structured JSON scoring brief (role, company, responsibilities,
            skills). Plain text is AI-parsed; JSON briefs are used as-is.
          </p>
          <form className="scoring__jd-form" onSubmit={handleSaveJd}>
            <textarea
              className={`scoring__jd-input${
                (jdText || '').trim().startsWith('{')
                  ? ' scoring__jd-input--mono'
                  : ''
              }`}
              value={jdText}
              onChange={(e) => setJdText(e.target.value)}
              rows={14}
              placeholder="Paste the full job description or a JSON scoring brief…"
              disabled={savingJd}
              aria-label="Job description"
              spellCheck={!((jdText || '').trim().startsWith('{'))}
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
            {hasParsedJd
              ? 'Structured scoring brief is saved. Run scoring against all sourced candidates for this role (no AI parse).'
              : 'JD is saved. Run scoring against all sourced candidates for this role.'}
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
            <p className="scoring__status mono" role="status">
              {scoringStatusText}
            </p>
          )}
        </section>
      )}

      {activeSlug && !loading && scoring && hasScores && (
        <p className="scoring__status mono" role="status">
          {scoringStatusText}
        </p>
      )}

      {activeSlug && !loading && showResults && (
        <section className="scoring__results" aria-label="Scored candidates">
          <div className="scoring__results-head">
            <h2 className="scoring__section-title">Ranked candidates</h2>
            <span className="mono scoring__results-count">{candidates.length}</span>
            {scoringMode && (
              <span
                className={`scoring__jd-mode mono${
                  scoringMode === 'parsed'
                    ? ' scoring__jd-mode--parsed'
                    : ' scoring__jd-mode--text'
                }`}
                title="Mode used for the latest score run"
              >
                {scoringMode === 'parsed'
                  ? 'Scored via brief'
                  : scoringMode === 'mixed'
                    ? 'Mixed modes'
                    : 'Scored via AI parse'}
              </span>
            )}
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
                  const enrichFailed =
                    c.enrich_status === 'enrich_failed' ||
                    c.enrich_retry_exhausted;
                  const maxN = c.max_enrich_retry_attempts || 3;
                  return (
                    <li key={c.id || c.candidate_id || c.linkedin_url}>
                      <span>{name}</span>
                      <span className="scoring__incomplete-status mono">
                        {enrichFailed
                          ? `failed after ${maxN} attempts — manual re-pull`
                          : 'insufficient data — not scored'}
                      </span>
                    </li>
                  );
                })}
              </ul>
            </div>
          )}
        </section>
      )}

      {jdModalOpen && (
        <JdModal
          jdText={jdText}
          editing={jdEditing}
          draft={jdDraft}
          saving={savingJd}
          error={jdModalError}
          hasParsedJd={hasParsedJd}
          onDraftChange={(v) => {
            setJdDraft(v);
            if (jdModalError) setJdModalError(null);
          }}
          onEdit={() => {
            setJdDraft(formatJdForDisplay(jdText, hasParsedJd));
            setJdEditing(true);
            setJdModalError(null);
          }}
          onSave={handleSaveJdFromModal}
          onCancel={closeJdModal}
        />
      )}
    </div>
  );
}
