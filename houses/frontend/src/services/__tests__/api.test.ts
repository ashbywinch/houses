import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useAuthStore } from '../../stores/auth'
import { fetchAllSummaries } from '../api'

/** The impersonation wire contract: the X-Impersonate-Person header is
 * sent ONLY while the superuser mode is on AND a person is selected —
 * a cookie-borne claim with the mode off must never reach the server.
 * The decision lives in the store (isImpersonating); this pins what the
 * services layer emits from it. */
describe('authHeaders impersonation gate', () => {
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    setActivePinia(createPinia())
    fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      url: '/api/properties/all',
      json: async () => ({}),
      text: async () => '',
    })
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('sends the header only when mode and selection are both on', async () => {
    const auth = useAuthStore()
    auth.user = {
      email: 'x@y.z', name: 'X', picture: '', person: 'Ashby', person_id: '3', is_superuser: true,
    } as never

    await fetchAllSummaries()
    expect(fetchMock.mock.calls[0][1].headers['X-Impersonate-Person']).toBeUndefined()

    auth.superuserMode = true
    auth.impersonating = '1'
    await fetchAllSummaries()
    expect(fetchMock.mock.calls[1][1].headers['X-Impersonate-Person']).toBe('1')

    // mode off again — the selection alone must not drive the header
    auth.superuserMode = false
    await fetchAllSummaries()
    expect(fetchMock.mock.calls[2][1].headers['X-Impersonate-Person']).toBeUndefined()
  })

  it('a reload-adopted claim is inert until the mode is on', async () => {
    const auth = useAuthStore()
    auth.user = {
      email: 'x@y.z', name: 'X', picture: '', person: 'Ashby', person_id: '3', is_superuser: true,
    } as never
    auth.impersonating = '1' // /me-returned claim after reload
    expect(auth.isImpersonating).toBe(false)

    await fetchAllSummaries()
    expect(fetchMock.mock.calls[0][1].headers['X-Impersonate-Person']).toBeUndefined()

    auth.superuserMode = true
    await fetchAllSummaries()
    expect(fetchMock.mock.calls[1][1].headers['X-Impersonate-Person']).toBe('1')
  })
})