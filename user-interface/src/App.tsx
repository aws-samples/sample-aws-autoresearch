import { useState, useEffect, useCallback } from 'react';
import { signIn, signOut, getCurrentUser, confirmSignIn } from 'aws-amplify/auth';
import { BrowserRouter, Routes, Route, useNavigate } from 'react-router-dom';
import TopNavigation from '@cloudscape-design/components/top-navigation';
import AppLayout from '@cloudscape-design/components/app-layout';
import { getMe } from './services/api';
import LoginPage from './pages/LoginPage';
import UserPage from './pages/UserPage';
import HomePage from './pages/HomePage';
import CreateProjectPage from './pages/CreateProjectPage';
import ProjectLayout from './components/ProjectLayout';
import StreamView from './pages/StreamView';
import StatusView from './pages/StatusView';
import ExperimentsPage from './pages/ExperimentsPage';
import ExperimentDetail from './pages/ExperimentDetail';
import WikiPage from './pages/WikiPage';
import CodePage from './pages/CodePage';
import DataPage from './pages/DataPage';
import PaperView from './pages/PaperView';
import TeamPage from './pages/TeamPage';

function Shell() {
  const navigate = useNavigate();
  const [username, setUsername] = useState('');

  useEffect(() => {
    getMe().then((me) => setUsername(me.username)).catch(() => setUsername(''));
  }, []);

  const handleSignOut = async () => {
    await signOut();
    window.location.reload();
  };

  return (
    <>
      <TopNavigation
        identity={{ title: 'Auto-Research', href: '/' }}
        utilities={[
          // Username → the User page (My Budget, My Projects, and budget admin if applicable).
          { type: 'button', iconName: 'user-profile', text: username || 'Account', onClick: () => navigate('/user') },
          { type: 'button', text: 'Sign out', onClick: handleSignOut },
        ]}
      />
      <AppLayout
        navigationHide
        toolsHide
        content={
          <Routes>
            <Route path="/" element={<HomePage />} />
            <Route path="/user" element={<UserPage />} />
            <Route path="/projects/new" element={<CreateProjectPage />} />
            <Route path="/projects/:projectId" element={<ProjectLayout />}>
              <Route index element={<StreamView />} />
              <Route path="status" element={<StatusView />} />
              <Route path="experiments" element={<ExperimentsPage />} />
              <Route path="experiments/:experimentId" element={<ExperimentDetail />} />
              <Route path="wiki" element={<WikiPage />} />
              <Route path="code" element={<CodePage />} />
              <Route path="data" element={<DataPage />} />
              <Route path="paper" element={<PaperView />} />
              <Route path="team" element={<TeamPage />} />
            </Route>
          </Routes>
        }
      />
    </>
  );
}

function AuthenticatedApp() {
  return (
    <BrowserRouter>
      <Shell />
    </BrowserRouter>
  );
}

export default function App() {
  const [authenticated, setAuthenticated] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getCurrentUser()
      .then(() => setAuthenticated(true))
      .catch(() => setAuthenticated(false))
      .finally(() => setLoading(false));
  }, []);

  const handleLogin = useCallback(async (username: string, password: string): Promise<string | null> => {
    try {
      const result = await signIn({ username, password });
      if (result.nextStep?.signInStep === 'CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED') {
        return 'NEW_PASSWORD_REQUIRED';
      }
      setAuthenticated(true);
      return null;
    } catch (err: unknown) {
      return err instanceof Error ? err.message : 'Login failed';
    }
  }, []);

  const handleNewPassword = useCallback(async (newPassword: string): Promise<string | null> => {
    try {
      await confirmSignIn({ challengeResponse: newPassword });
      setAuthenticated(true);
      return null;
    } catch (err: unknown) {
      return err instanceof Error ? err.message : 'Failed';
    }
  }, []);

  if (loading) return null;
  if (!authenticated) return <LoginPage onLogin={handleLogin} onNewPassword={handleNewPassword} />;
  return <AuthenticatedApp />;
}
