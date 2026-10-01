import { ref } from 'vue'
import { fetchPropertyProvenance } from '../services/api'
import type { Provenance } from '../types'

export type ProvenanceMap = Record<string, Provenance>

/**
 * The P8 provenance store: NOTHING is fetched until a ProvenanceToggle is
 * opened. The detail/summary wires carry no provenance — this is the only
 * client path that loads it, once per property, memoized.
 */
const byRid = ref<Record<string, ProvenanceMap>>({})
const inflight = new Map<string, Promise<ProvenanceMap>>()

export function useProvenanceStore() {
  return {
    /** The served tree for a dotted path, or undefined before the first
     *  load of this property. Reactive: toggles open on data arrival. */
    provenanceFor(rid: string, path: string): Provenance | undefined {
      return byRid.value[rid]?.[path]
    },
    async loadProvenance(rid: string): Promise<ProvenanceMap> {
      const pending = inflight.get(rid)
      if (pending) return pending
      const p = fetchPropertyProvenance(rid)
        .then((map) => {
          byRid.value[rid] = map
          return map
        })
        .finally(() => inflight.delete(rid))
      inflight.set(rid, p)
      return p
    },
  }
}
