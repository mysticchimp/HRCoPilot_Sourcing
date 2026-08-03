import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import AppShell from './components/AppShell';
import ArchivedRolesView from './components/ArchivedRolesView';
import LoginPage from './components/LoginPage';
import ScoringScreen from './components/ScoringScreen';
import SourcingScreen from './components/SourcingScreen';
import { AuthProvider } from './lib/auth';
import { RoleProvider } from './lib/roleContext';

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route
            element={
              <RoleProvider>
                <AppShell />
              </RoleProvider>
            }
          >
            <Route index element={<Navigate to="/sourcing" replace />} />
            <Route path="/sourcing" element={<SourcingScreen />} />
            <Route path="/sourcing/archived" element={<ArchivedRolesView />} />
            <Route path="/scoring" element={<ScoringScreen />} />
          </Route>
          <Route path="*" element={<Navigate to="/sourcing" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}
