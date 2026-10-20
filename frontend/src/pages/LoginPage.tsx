import { Navigate } from 'react-router-dom'

import { useAuth } from '../auth'
import { AuthForm } from './AuthForm'

export function LoginPage() {
  const { user, login } = useAuth()
  if (user) return <Navigate to="/dashboard" replace />
  return (
    <AuthForm
      title="Log in"
      subtitle="Welcome back. Your jobs are waiting."
      submitLabel="Log in"
      onSubmit={login}
      footer={{ text: 'No account yet?', linkText: 'Create one', to: '/register' }}
    />
  )
}
