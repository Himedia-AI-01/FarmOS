import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { Toaster, ToastBar, toast } from 'react-hot-toast';
import { MdClose } from 'react-icons/md';
import { AuthProvider, useAuth } from '@/context/AuthContext';
import { FarmAgentProvider } from '@/context/FarmAgentContext';
import AppLayout from '@/components/layout/AppLayout';
import DashboardPage from '@/pages/DashboardPage';
import DiagnosisPage from '@/modules/diagnosis/DiagnosisPage';
import IoTDashboardPage from '@/modules/iot/IoTDashboardPage';
import ReviewsPage from '@/modules/reviews/ReviewsPage';
import WeatherPage from '@/modules/weather/WeatherPage';
import JournalPage from '@/modules/journal/JournalPage';
import MarketPricePage from '@/modules/market/MarketPricePage';
import ProfilePage from '@/modules/profile/ProfilePage';
import LoginPage from '@/modules/auth/LoginPage';
import OnboardingPage from '@/modules/auth/OnboardingPage';
import FindIdPage from '@/modules/auth/FindIdPage';
import FindPasswordPage from '@/modules/auth/FindPasswordPage';
import DiagnosisChatPage from '@/modules/diagnosis/chat/DiagnosisChatPage';
import SubsidyPage from '@/modules/subsidy/SubsidyPage';

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, isLoading, needsOnboarding } = useAuth();
  if (isLoading) return <LoadingScreen />;
  if (!isAuthenticated) return <Navigate to="/login" replace />;
  if (needsOnboarding) return <Navigate to="/onboarding" replace />;
  return <>{children}</>;
}

function PublicRoute({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, isLoading, needsOnboarding } = useAuth();
  if (isLoading) return <LoadingScreen />;
  if (isAuthenticated && !needsOnboarding) return <Navigate to="/" replace />;
  return <>{children}</>;
}

function LoadingScreen() {
  return (
    <div
      role="status"
      aria-live="polite"
      className="flex min-h-screen items-center justify-center bg-[color:var(--color-surface)]"
    >
      <div className="flex flex-col items-center gap-3">
        <span aria-hidden className="flex h-12 w-12 items-center justify-center rounded-2xl bg-[color:var(--color-primary)] text-white shadow-[var(--shadow-sm)]">
          <svg viewBox="0 0 32 32" fill="none" className="h-7 w-7 motion-safe:animate-pulse" aria-hidden>
            <path d="M5 27c0-11 7-19 22-22-1 13-7 22-19 22-1 0-3 0-3 0z" fill="currentColor" opacity="0.95" />
            <path d="M7 25C13 18 19 13 26 9" stroke="white" strokeWidth="1.6" strokeLinecap="round" opacity="0.6" />
          </svg>
        </span>
        <p className="text-[13px] font-medium text-[color:var(--color-ink-mute)]">인증 확인 중...</p>
      </div>
    </div>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          {/* Public auth routes */}
          <Route path="/login" element={<PublicRoute><LoginPage /></PublicRoute>} />
          <Route path="/signup" element={<PublicRoute><OnboardingPage /></PublicRoute>} />
          <Route path="/onboarding" element={<PublicRoute><OnboardingPage /></PublicRoute>} />
          <Route path="/find-id" element={<PublicRoute><FindIdPage /></PublicRoute>} />
          <Route path="/find-password" element={<PublicRoute><FindPasswordPage /></PublicRoute>} />

          {/* Protected app routes — FarmAgentProvider wraps the whole shell so
              dashboard buttons, layout chrome, and the rail console share state. */}
          <Route
            element={
              <ProtectedRoute>
                <FarmAgentProvider>
                  <AppLayout />
                </FarmAgentProvider>
              </ProtectedRoute>
            }
          >
            <Route index element={<DashboardPage />} />
            <Route path="diagnosis" element={<DiagnosisPage />} />
            <Route path="diagnosis/chat" element={<DiagnosisChatPage />} />
            <Route path="iot" element={<IoTDashboardPage />} />
            <Route path="reviews" element={<ReviewsPage />} />
            <Route path="weather" element={<WeatherPage />} />
            <Route path="journal" element={<JournalPage />} />
            <Route path="market" element={<MarketPricePage />} />
            <Route path="subsidy" element={<SubsidyPage />} />
            <Route path="profile" element={<ProfilePage />} />
          </Route>

          {/* Catch-all */}
          <Route path="*" element={<Navigate to="/login" replace />} />
        </Routes>
        <Toaster
          position="top-right"
          containerStyle={{ top: 16, right: 16 }}
          toastOptions={{
            duration: 4000,
            ariaProps: { role: 'status', 'aria-live': 'polite' },
            style: {
              fontSize: '14px',
              borderRadius: '14px',
              border: '1px solid var(--color-line)',
              background: 'var(--color-card)',
              color: 'var(--color-ink)',
              fontFamily: "'Onest', 'Pretendard', -apple-system, sans-serif",
              fontWeight: 500,
              padding: '12px 16px',
              boxShadow: 'var(--shadow-md)',
              maxWidth: '380px',
            },
            success: {
              iconTheme: { primary: 'var(--color-primary)', secondary: '#fff' },
            },
            error: {
              ariaProps: { role: 'alert', 'aria-live': 'assertive' },
              iconTheme: { primary: 'var(--color-danger)', secondary: '#fff' },
              style: {
                border: '1px solid var(--color-danger-light)',
              },
            },
          }}
        >
          {(t) => (
            <ToastBar toast={t}>
              {({ icon, message }) => (
                <>
                  {icon}
                  {message}
                  {t.type !== 'loading' && (
                    <button
                      onClick={() => toast.dismiss(t.id)}
                      className="ml-2 inline-flex h-7 w-7 cursor-pointer items-center justify-center rounded-full text-[color:var(--color-ink-faint)] transition hover:bg-[color:var(--color-surface)] hover:text-[color:var(--color-ink)]"
                      aria-label="닫기"
                    >
                      <MdClose aria-hidden className="text-[18px]" />
                    </button>
                  )}
                </>
              )}
            </ToastBar>
          )}
        </Toaster>
      </AuthProvider>
    </BrowserRouter>
  );
}
