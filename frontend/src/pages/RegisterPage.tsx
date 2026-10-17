import { Navigate } from 'react-router-dom'

import { useAuth } from '../auth'
import { AuthForm } from './AuthForm'

export function RegisterPage() {
  const { user, register } = useAuth()
  if (user) return <Navigate to="/" replace />
  return (
    <AuthForm
      title="Create your account"
      submitLabel="Create account"
      onSubmit={register}
      footer={{ text: 'Already registered?', linkText: 'Log in', to: '/login' }}
    />
  )
}
