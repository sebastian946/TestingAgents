import { Navigate, useSearchParams } from 'react-router-dom'

import { useAuth } from '../auth'
import { AuthForm } from './AuthForm'

export function LoginPage() {
  const { user, login } = useAuth()
  const [params] = useSearchParams()
  const plan = params.get('plan')
  if (user) return <Navigate to={plan ? `/billing?plan=${encodeURIComponent(plan)}` : '/dashboard'} replace />
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
