import ReactDOM from 'react-dom/client';
import '@cloudscape-design/global-styles/index.css';
import { Amplify } from 'aws-amplify';
import App from './App';

Amplify.configure({
  Auth: {
    Cognito: {
      userPoolId: import.meta.env.VITE_COGNITO_USER_POOL_ID || '',
      userPoolClientId: import.meta.env.VITE_COGNITO_CLIENT_ID || '',
      identityPoolId: import.meta.env.VITE_COGNITO_IDENTITY_POOL_ID || '',
    },
  },
});

const root = ReactDOM.createRoot(document.getElementById('root') as HTMLElement);
root.render(<App />);
