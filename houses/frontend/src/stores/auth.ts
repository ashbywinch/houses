import { defineStore } from 'pinia'
import { ref } from 'vue'

export interface AuthUser {
  email: string
  name: string
  picture: string
  person: string | null
  is_superuser: boolean
}

export const useAuthStore = defineStore('auth', () => {
  const user = ref<AuthUser | null>(null)
  const loading = ref(true)

  // Superuser mode — when active, a header bar shows with an impersonation dropdown
  const superuserMode = ref(false)
  const impersonating = ref<string | null>(null) // Person name being impersonated

  let _pendingCheck: Promise<void> | null = null

  async function checkAuth() {
    if (_pendingCheck) return _pendingCheck
    _pendingCheck = _doCheck()
    try {
      return await _pendingCheck
    } finally {
      _pendingCheck = null
    }
  }

  async function _doCheck() {
    try {
      const r = await fetch('/api/auth/me')
      if (!r.ok) {
        console.error('Auth check failed:', r.status)
        loading.value = false
        return  // keep current user state on transient errors
      }
      const data = await r.json()
      if (data.authenticated) {
        user.value = data
        // Exit superuser mode if user is no longer a superuser
        if (!data.is_superuser) {
          superuserMode.value = false
          impersonating.value = null
        }
      } else {
        user.value = null
      }
    } catch (e) {
      console.error('Auth check exception:', e)
      // keep current user state on transient errors
    } finally {
      loading.value = false
    }
  }

  function toggleSuperuser() {
    superuserMode.value = !superuserMode.value
    if (!superuserMode.value) {
      impersonating.value = null
    }
  }

  function setImpersonating(person: string) {
    impersonating.value = person
  }

  async function login(): Promise<{ ok: boolean; error?: string }> {
    try {
      const r = await fetch('/api/auth/login')
      if (!r.ok) {
        console.error('Auth login failed:', r.status)
        return { ok: false, error: 'Sign in is unavailable. Please try again later.' }
      }
      const data = await r.json()
      if (data.status === 'error') {
        console.error('Auth login error:', data.detail)
        return { ok: false, error: 'Sign in is unavailable. Please try again later.' }
      }
      if (data.auth_url) {
        window.location.href = data.auth_url
        return { ok: true }
      }
      return { ok: true }
    } catch (e) {
      console.error('Auth login exception:', e)
      return { ok: false, error: 'Could not connect to the server.' }
    }
  }

  async function logout() {
    try {
      await fetch('/api/auth/logout', { method: 'POST' })
    } catch {
      console.error('Logout request failed — clearing local state anyway')
    } finally {
      user.value = null
      superuserMode.value = false
      impersonating.value = null
    }
  }

  return {
    user,
    loading,
    superuserMode,
    impersonating,
    toggleSuperuser,
    setImpersonating,
    checkAuth,
    login,
    logout,
  }
})
