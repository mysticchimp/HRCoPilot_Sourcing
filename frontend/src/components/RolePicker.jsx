import { useEffect, useRef, useState } from 'react';
import { useRole } from '../lib/roleContext';

/**
 * Shared role switcher used by Sourcing and Scoring tabs.
 * Selection state lives in RoleProvider — do not duplicate it per screen.
 */
export default function RolePicker({
  allowNew = false,
  busy = false,
  onSelect,
  onArchive,
}) {
  const { roles, activeSlug, activeRole, selectRole, archiveRole } = useRole();
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef(null);

  const isPlaceholder = !activeSlug;
  const activeLabel = isPlaceholder
    ? 'Select Role'
    : activeRole?.role_name || activeSlug;

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

  const handleSelect = (slug) => {
    setMenuOpen(false);
    const next = slug === 'new' ? null : slug;
    selectRole(next);
    if (onSelect) onSelect(next);
  };

  const handleArchive = async (role, e) => {
    e.stopPropagation();
    const ok = window.confirm(
      `Archive ${role.role_name}? You can restore it later from Archived Roles.`,
    );
    if (!ok) return;
    setMenuOpen(false);
    try {
      if (onArchive) await onArchive(role);
      else await archiveRole(role);
    } catch {
      /* parent / context surfaces error */
    }
  };

  return (
    <div className="sourcing__role-picker" ref={menuRef}>
      <span className="sourcing__label">Role</span>
      <button
        type="button"
        className={`role-menu__trigger${
          isPlaceholder ? ' role-menu__trigger--placeholder' : ''
        }`}
        disabled={busy}
        aria-haspopup="listbox"
        aria-expanded={menuOpen}
        onClick={() => setMenuOpen((o) => !o)}
      >
        {activeLabel}
      </button>
      {menuOpen && (
        <ul className="role-menu" role="listbox">
          {allowNew && (
            <li>
              <button
                type="button"
                className="role-menu__item role-menu__item--placeholder"
                onClick={() => handleSelect('new')}
              >
                Select Role
              </button>
            </li>
          )}
          {!allowNew && (
            <li>
              <button
                type="button"
                className="role-menu__item role-menu__item--placeholder"
                onClick={() => handleSelect(null)}
              >
                Select Role
              </button>
            </li>
          )}
          {roles.map((r) => (
            <li key={r.slug} className="role-menu__row">
              <button
                type="button"
                className={`role-menu__item${
                  r.slug === activeSlug ? ' role-menu__item--active' : ''
                }`}
                onClick={() => handleSelect(r.slug)}
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
  );
}
