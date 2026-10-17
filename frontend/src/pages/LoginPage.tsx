import { Navigate } from 'react-router-dom'

import { useAuth } from '../auth'
import { AuthForm } from './AuthForm'

export function LoginPage() {
  const { user, login } = useAuth()
  if (user) return <Navigate to="/" replace />
  return (
    <AuthForm
      title="Log in"
      submitLabel="Log in"
      onSubmit={login}
      footer={{ text: 'No account yet?', linkText: 'Create one', to: '/register' }}
    />
  )
}
