import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { listArchivedRoles, unarchiveRole } from '../lib/sourcingApi';

function formatArchivedAt(iso) {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

export default function ArchivedRolesView() {
  const [roles, setRoles] = useState([]);
  const [error, setError] = useState(null);
  const [busySlug, setBusySlug] = useState(null);

  const refresh = useCallback(async () => {
    try {
      const data = await listArchivedRoles();
      setRoles(Array.isArray(data) ? data : []);
      setError(null);
    } catch (err) {
      setError(err.message);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const handleRestore = async (role) => {
    setBusySlug(role.slug);
    setError(null);
    try {
      await unarchiveRole(role.slug);
      await refresh();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusySlug(null);
    }
  };

  return (
    <div className="archived">
      <header className="archived__toolbar">
        <Link to="/sourcing" className="archived__back">
          ← Back to Sourcing
        </Link>
        <h1 className="archived__title">Archived Roles</h1>
      </header>
      {error && (
        <p className="sourcing__error" role="alert">
          {error}
        </p>
      )}
      <div className="archived__list-wrap">
        {roles.length === 0 ? (
          <p className="archived__empty">No archived roles.</p>
        ) : (
          <table className="sourcing-table">
            <thead>
              <tr>
                <th>Role</th>
                <th>Archived</th>
                <th>ID</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {roles.map((r) => (
                <tr key={r.slug}>
                  <td>{r.role_name}</td>
                  <td className="mono">{formatArchivedAt(r.archived_at)}</td>
                  <td className="mono archived__id">{r.id}</td>
                  <td>
                    <button
                      type="button"
                      className="btn btn--ghost"
                      disabled={busySlug === r.slug}
                      onClick={() => handleRestore(r)}
                    >
                      Restore
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
