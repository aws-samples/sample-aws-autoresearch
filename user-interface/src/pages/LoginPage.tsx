import { useState } from 'react';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import FormField from '@cloudscape-design/components/form-field';
import Input from '@cloudscape-design/components/input';
import Button from '@cloudscape-design/components/button';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Alert from '@cloudscape-design/components/alert';

interface Props {
  onLogin: (username: string, password: string) => Promise<string | null>;
  onNewPassword: (newPassword: string) => Promise<string | null>;
}

export default function LoginPage({ onLogin, onNewPassword }: Props) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [error, setError] = useState('');
  const [needsNewPassword, setNeedsNewPassword] = useState(false);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async () => {
    setLoading(true);
    setError('');
    const result = await onLogin(username, password);
    if (result === 'NEW_PASSWORD_REQUIRED') setNeedsNewPassword(true);
    else if (result) setError(result);
    setLoading(false);
  };

  const handleNewPasswordSubmit = async () => {
    setLoading(true);
    setError('');
    const result = await onNewPassword(newPassword);
    if (result) setError(result);
    setLoading(false);
  };

  return (
    <div style={{ maxWidth: 400, margin: '100px auto' }}>
      <Container header={<Header variant="h1">Auto-Research</Header>}>
        <SpaceBetween size="m">
          {error && <Alert type="error">{error}</Alert>}
          {!needsNewPassword ? (
            <>
              <FormField label="Username">
                <Input value={username} onChange={({ detail }) => setUsername(detail.value)} />
              </FormField>
              <FormField label="Password">
                <Input type="password" value={password} onChange={({ detail }) => setPassword(detail.value)} />
              </FormField>
              <Button variant="primary" loading={loading} onClick={handleSubmit}>Sign in</Button>
            </>
          ) : (
            <>
              <FormField label="Set new password">
                <Input type="password" value={newPassword} onChange={({ detail }) => setNewPassword(detail.value)} />
              </FormField>
              <Button variant="primary" loading={loading} onClick={handleNewPasswordSubmit}>Set password</Button>
            </>
          )}
        </SpaceBetween>
      </Container>
    </div>
  );
}
