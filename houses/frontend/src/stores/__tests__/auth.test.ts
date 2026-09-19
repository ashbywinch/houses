import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useAuthStore } from '../auth'
import * as api from '../../services/api'

vi.mock('../../services/api', () => ({
  impersonate: vi.fn(),
  login: vi.fn(),
}))

function makeUser(person: string | null) {
  return {
    email: 'x@y.z',
    name: 'X',
    picture: '',
    person,
    person_id: person ? { simon: '1', lorena: '2', ashby: '3', george: '4' }[person.toLowerCase()] ?? null : null,
    is_superuser: true,
  } as never
}

function stubMe(payload: Record<string, unknown>) {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
    ok: true,
    json: async () => payload,
  }))
}

/** The acting identity is owned HERE, in the store — these cases pin
 * the precedence every view relies on when it reads auth.actingAs. */
describe('auth actingAs', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('is null when logged out', () => {
    expect(useAuthStore().actingAs).toBeNull()
  })

  it('is the linked person when not impersonating', () => {
    const auth = useAuthStore()
    auth.user = makeUser('Ashby')
    expect(auth.actingAs).toBe('3')
  })

  it('is the impersonated person while a superuser impersonates', () => {
    const auth = useAuthStore()
    auth.user = makeUser('Ashby')
    auth.superuserMode = true
    auth.impersonating = '1'
    expect(auth.actingAs).toBe('1')
  })

  it('ignores a stale impersonating flag when superuser mode is off', () => {
    const auth = useAuthStore()
    auth.user = makeUser('Ashby')
    auth.impersonating = '1' // cookie-flavoured leftovers without the mode
    expect(auth.actingAs).toBe('3')
  })

  it('follows setImpersonating immediately — no /me round trip', async () => {
    vi.mocked(api.impersonate).mockResolvedValue({ ok: true } as Response)
    const auth = useAuthStore()
    auth.user = makeUser('Ashby')
    auth.superuserMode = true
    await auth.setImpersonating('1')
    expect(auth.actingAs).toBe('1')
  })

  it('reload adopts the cookie impersonation claim but leaves it inert until the mode is on', async () => {
    stubMe({
      authenticated: true,
      email: 'x@y.z',
      name: 'X',
      picture: '',
      person: 'Ashby',
      person_id: '3',
      is_superuser: true,
      impersonating: '1',
    })
    const auth = useAuthStore()
    await auth.checkAuth()
    expect(auth.impersonating).toBe('1')
    expect(auth.isImpersonating).toBe(false) // mode off → inert
    expect(auth.actingAs).toBe('3')
    auth.superuserMode = true
    expect(auth.isImpersonating).toBe(true)
    expect(auth.actingAs).toBe('1')
  })

  it('reload clears a stale claim when the account is no longer a superuser', async () => {
    stubMe({
      authenticated: true,
      email: 'x@y.z',
      name: 'X',
      picture: '',
      person: 'Ashby',
      is_superuser: false,
      impersonating: '1', // stale cookie claim
    })
    const auth = useAuthStore()
    auth.superuserMode = true
    await auth.checkAuth()
    expect(auth.impersonating).toBeNull()
    expect(auth.isImpersonating).toBe(false)
  })

  it('isImpersonating needs BOTH the mode and a selected person', () => {
    const auth = useAuthStore()
    auth.user = makeUser('Ashby')
    expect(auth.isImpersonating).toBe(false)
    auth.impersonating = '1'
    expect(auth.isImpersonating).toBe(false) // mode off
    auth.superuserMode = true
    expect(auth.isImpersonating).toBe(true)
    auth.impersonating = null
    expect(auth.isImpersonating).toBe(false) // no selection
  })
})