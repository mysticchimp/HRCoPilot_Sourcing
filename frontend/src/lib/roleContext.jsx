import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { archiveRole as apiArchiveRole, listRoles } from './sourcingApi';

const RoleContext = createContext(null);

export function RoleProvider({ children }) {
  const [roles, setRoles] = useState([]);
  const [activeSlug, setActiveSlug] = useState(null);
  const [error, setError] = useState(null);

  const refreshRoles = useCallback(async () => {
    try {
      const data = await listRoles();
      setRoles(Array.isArray(data) ? data : []);
      setError(null);
    } catch (err) {
      setError(err.message);
    }
  }, []);

  useEffect(() => {
    refreshRoles();
  }, [refreshRoles]);

  const selectRole = useCallback((slug) => {
    setActiveSlug(slug && slug !== 'new' ? slug : null);
  }, []);

  const archiveRole = useCallback(
    async (role) => {
      await apiArchiveRole(role.slug);
      if (activeSlug === role.slug) {
        setActiveSlug(null);
      }
      await refreshRoles();
    },
    [activeSlug, refreshRoles],
  );

  const activeRole = useMemo(
    () => roles.find((r) => r.slug === activeSlug) || null,
    [roles, activeSlug],
  );

  const value = useMemo(
    () => ({
      roles,
      activeSlug,
      activeRole,
      selectRole,
      refreshRoles,
      archiveRole,
      error,
      setError,
    }),
    [roles, activeSlug, activeRole, selectRole, refreshRoles, archiveRole, error],
  );

  return <RoleContext.Provider value={value}>{children}</RoleContext.Provider>;
}

export function useRole() {
  const ctx = useContext(RoleContext);
  if (!ctx) {
    throw new Error('useRole must be used within RoleProvider');
  }
  return ctx;
}
