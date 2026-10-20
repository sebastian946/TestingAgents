import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'

import { AuthProvider, RequireAuth } from './auth'
import { Layout } from './components/Layout'
import './index.css'
import { AccountPage } from './pages/AccountPage'
import { BillingPage } from './pages/BillingPage'
import { ForgotPasswordPage } from './pages/ForgotPasswordPage'
import { JobDetailPage } from './pages/JobDetailPage'
import { JobsPage } from './pages/JobsPage'
import { LoginPage } from './pages/LoginPage'
import { NewJobPage } from './pages/NewJobPage'
import { RegisterPage } from './pages/RegisterPage'
import { ResetPasswordPage } from './pages/ResetPasswordPage'
import { VerifyEmailPage } from './pages/VerifyEmailPage'

// In production Caddy serves the static landing (public/landing.html) at "/", so the SPA
// never sees that path; in development "/" just forwards to the dashboard.
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/register" element={<RegisterPage />} />
          <Route path="/verify" element={<VerifyEmailPage />} />
          <Route path="/forgot-password" element={<ForgotPasswordPage />} />
          <Route path="/reset-password" element={<ResetPasswordPage />} />
          <Route
            element={
              <RequireAuth>
                <Layout />
              </RequireAuth>
            }
          >
            <Route path="/dashboard" element={<JobsPage />} />
            <Route path="/new" element={<NewJobPage />} />
            <Route path="/job/:id" element={<JobDetailPage />} />
            <Route path="/billing" element={<BillingPage />} />
            <Route path="/account" element={<AccountPage />} />
          </Route>
          <Route path="*" element={<Navigate to="/dashboard" replace />} />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
)
