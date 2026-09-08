import { ref, onUnmounted, getCurrentInstance } from 'vue'
import { usePropertiesStore } from '../stores/properties'

const MAX_RETRIES = 10
const BASE_DELAY = 1000
let retries = 0
let timer: number | null = null
let wsFactory = (url: string) => new WebSocket(url)

interface PendingUpdate {
  rid: string
  data: any
  triage: any
}

export function useWebSocket(factory?: (url: string) => WebSocket) {
  if (factory) wsFactory = factory

  const connected = ref(false)
  let ws: WebSocket | null = null

  // The burst buffer belongs to THIS connection: a scenario apply
  // produces one update per refreshed node/property, and a backlog
  // drain spreads them over minutes. Applying each message the instant
  // it lands invalidates the grid per message (the whole-page redraw).
  // Buffer updates and apply them once per tick; coalesce settings
  // refreshes the same way. Per-instance state means a closed socket
  // takes its un-applied buffer with it.
  let pendingUpdates: PendingUpdate[] = []
  let pendingSettings: Record<string, unknown> | null = null
  let flushScheduled = false
  let disposed = false

  function flushBurst() {
    flushScheduled = false
    if (disposed) return
    const store = usePropertiesStore()
    if (pendingUpdates.length) {
      for (const { rid, data, triage } of pendingUpdates) {
        store.updateSummary(rid, data)
        if (triage) {
          // Extract triage using the same pattern as loadAll()
          store.triage[rid] = {
            favourite: triage.favourite?.value ?? false,
            dismissed: triage.dismissed?.value ?? false,
            is_viewed: triage.is_viewed?.value ?? false,
            user_notes: triage.user_notes?.value ?? '',
            triage_status: triage.triage_status?.value ?? '',
          }
        }
        if (!store.rids.includes(rid)) {
          store.rids.push(rid)
        }
      }
      pendingUpdates = []
    }
    if (pendingSettings !== null) {
      const data = pendingSettings
      pendingSettings = null
      // The payload carries what_if_active, so a what-if applied on
      // another device flips this device's banner and chips in the
      // same pass — no extra round-trip.
      store.applySettings(data as never)
    }
  }

  function scheduleFlush() {
    if (flushScheduled) return
    flushScheduled = true
    setTimeout(flushBurst, 0)
  }

  function connect(url: string) {
    ws = wsFactory(url)
    ws.onopen = () => { connected.value = true; retries = 0 }
    ws.onclose = () => {
      connected.value = false
      if (retries < MAX_RETRIES) {
        const delay = BASE_DELAY * Math.pow(2, retries)
        timer = setTimeout(() => connect(url), delay + Math.random() * 1000)
        retries++
      }
    }
    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data)
        if (msg.type === 'property_updated' && msg.rid) {
          pendingUpdates.push({ rid: msg.rid, data: msg.data, triage: msg.data?.triage })
          scheduleFlush()
        }
        // A settings node refreshed: the server pushes the WHOLE
        // settings payload once (persons, thresholds, what-if flag).
        // Applied with the property burst so every surface updates in
        // the same pass.
        if (msg.type === 'settings_updated' && msg.data) {
          pendingSettings = msg.data
          scheduleFlush()
        }
      } catch {
        // ignore parse errors
      }
    }
  }

  function disconnect() {
    disposed = true
    ws?.close()
    ws = null
    connected.value = false
    if (timer !== null) clearTimeout(timer)
  }

  if (getCurrentInstance()) onUnmounted(disconnect)

  return { connected, connect, disconnect }
}
