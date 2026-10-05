import { describe, it, expect } from 'vitest'
import router from '../router'

/**
 * The route table must keep the index eager and lazy-load everything
 * else, so Vite emits the non-index views as their own chunks.
 *
 * Inspect the table BEFORE any navigation: vue-router replaces a lazy
 * route's loader with the resolved component once that route has been
 * visited, which would hide the dynamic import being asserted here.
 */
describe('router — code splitting', () => {
  it('keeps the index eager and lazy-loads every other view', async () => {
    const componentFor = (path: string) =>
      router.getRoutes().find(r => r.path === path)!.components!.default

    // PropertyList is the entry screen — eager.
    expect(typeof componentFor('/')).toBe('object')

    for (const path of ['/login', '/property/:rid', '/settings']) {
      // A dynamic import: a function, not a resolved SFC object.
      expect(typeof componentFor(path)).toBe('function')
    }

    // And the loader really is a module import (the chunk exists).
    const loadSettings = componentFor('/settings') as () => Promise<{ default: unknown }>
    const mod = await loadSettings()
    expect(mod.default).toBeTruthy()
  })
})
