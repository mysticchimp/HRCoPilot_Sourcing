import { Navigate, useNavigate } from 'react-router-dom';
import { useAuth } from '../lib/auth';

const ATS_URL = import.meta.env.VITE_ATS_URL || 'http://localhost:5174';

export default function ChooserPage() {
  const { user, loading, logout } = useAuth();
  const navigate = useNavigate();

  if (loading) {
    return (
      <div className="app-shell app-shell--loading">
        <p className="mono">Loading…</p>
      </div>
    );
  }

  if (!user) {
    return <Navigate to="/login" replace />;
  }

  const handleLogout = async () => {
    await logout();
    navigate('/login', { replace: true });
  };

  const openAts = () => {
    window.location.assign(ATS_URL);
  };

  return (
    <div className="app-shell chooser">
      <header className="topbar">
        <div className="topbar__brand">
          <img
            className="topbar__logo"
            src="/contra6-logo.png"
            alt="Contra6 Engineering"
          />
          <span className="topbar__product">
            HR<span className="topbar__product-accent">CoPilot</span>
          </span>
        </div>
        <div className="topbar__user">
          <span className="topbar__email">{user.email}</span>
          <span className="topbar__role mono">{user.role}</span>
          <button type="button" className="btn btn--ghost" onClick={handleLogout}>
            Log out
          </button>
        </div>
      </header>

      <main className="chooser__main">
        <button
          type="button"
          className="chooser__panel"
          onClick={() => navigate('/sourcing')}
        >
          <span className="chooser__panel-label">Intelligence</span>
          <span className="chooser__panel-hint">
            Sourcing, scoring, and review
          </span>
          <span className="chooser__panel-body">
            Find and shortlist candidates from LinkedIn, score them against
            your job description, and move the best fits into review. Use this
            when you need market intelligence and ranked talent pools.
          </span>
        </button>
        <button type="button" className="chooser__panel" onClick={openAts}>
          <span className="chooser__panel-label">ATS</span>
          <span className="chooser__panel-hint">
            Recruiting pipeline
          </span>
          <span className="chooser__panel-body">
            Run the hiring workflow from requisition and AI shortlist through
            swipe decisions, candidate tracking, and hire. Use this when you
            are managing an active pipeline and progressing people toward an
            offer.
          </span>
        </button>
      </main>
    </div>
  );
}
