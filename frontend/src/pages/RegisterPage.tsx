import { Navigate, useSearchParams } from 'react-router-dom'

import { useAuth } from '../auth'
import { AuthForm } from './AuthForm'

export function RegisterPage() {
  const { user, register } = useAuth()
  const [params] = useSearchParams()
  const plan = params.get('plan')
  if (user) return <Navigate to={plan ? `/billing?plan=${encodeURIComponent(plan)}` : '/dashboard'} replace />
  return (
    <AuthForm
      title="Create your account"
      subtitle="Free plan, no card. Your first test plan is minutes away."
      submitLabel="Create account"
      onSubmit={register}
      footer={{ text: 'Already registered?', linkText: 'Log in', to: '/login' }}
    />
  )
}
