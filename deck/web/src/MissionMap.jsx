import { useState } from 'react'
import { ago, KINDS } from './api.js'
import { teamColor } from './orchestration.jsx'

const GROUPS = [['needs', 'NEEDS YOU'], ['working', 'WORKING'], ['resting', 'RESTING'], ['starting', 'STARTING'], ['exited', 'STOPPED']]
const RANK = { needs: 0, working: 1, resting: 2, starting: 3, exited: 4 }

// Every console on every workspace - by what it's doing, or by team; click one to fly to it.
export default function MissionMap({ nodes, status, workspaces, onFly }) {
  const [by, setBy] = useState(() => { try { return localStorage.getItem('deck_map_by') || 'status' } catch { return 'status' } })
  const pick = v => { setBy(v); try { localStorage.setItem('deck_map_by', v) } catch {} }
  const consoles = nodes.filter(n => n.type === 'console')
  const wsName = id => workspaces.find(w => w.id === id)?.name || ''
  const state = n => status[n.id]?.state || 'starting'
  const of = s => consoles.filter(n => state(n) === s)
  const working = of('working').length, needs = of('needs').length
  const teams = [...new Set(consoles.map(n => n.team || ''))].sort((a, b) => (!a) - (!b) || a.localeCompare(b))
  const groups = by === 'team'
    ? teams.map(t => ({ key: `t:${t}`, cls: 'team', label: (t || 'NO TEAM').toUpperCase(), color: t ? teamColor(t) : 'var(--faint)',
      list: consoles.filter(n => (n.team || '') === t).sort((a, b) => RANK[state(a)] - RANK[state(b)]) }))
    : GROUPS.map(([s, label]) => ({ key: s, cls: s, label, list: of(s) }))
  return (
    <aside id="map" aria-label="Mission map">
      <div className="map-h"><b>MISSION MAP</b><small>{consoles.length} console{consoles.length === 1 ? '' : 's'}{needs ? ` · ${needs} need you` : working ? ` · ${working} working` : ''}</small></div>
      <div className="seg map-seg" role="radiogroup" aria-label="Group by">
        {[['status', 'By status'], ['team', 'By team']].map(([k, l]) => <button key={k} role="radio" aria-checked={by === k} className={by === k ? 'on' : ''} onClick={() => pick(k)}>{l}</button>)}
      </div>
      <div id="mapBody">
        {!consoles.length && <div className="map-empty">No consoles yet.</div>}
        {groups.map(g => {
          if (!g.list.length) return null
          return (
            <div key={g.key} className={`grp ${g.cls}`}>
              <div className="grp-h"><i style={g.color ? { background: g.color } : undefined} />{g.label} · {g.list.length}</div>
              {g.list.map(n => {
                const st = status[n.id] || {}, s = state(n)
                return (
                  <div key={n.id} className={`mrow${s === 'needs' ? ' needs' : ''}`} role="button" tabIndex={0} onClick={() => onFly(n.id)} onKeyDown={e => e.key === 'Enter' && onFly(n.id)}>
                    <span className={`k ${n.kind}`} />
                    <span className="t">{n.title}{by === 'team' && n.role ? <em className="role"> · {n.role}</em> : null}</span>
                    <span className="a">{by === 'team' ? s : st.since ? ago(st.since) : ''}</span>
                    <span className="d" style={{ color: s === 'needs' ? undefined : 'var(--dim)' }}>
                      {s === 'needs' ? (st.detail || 'waiting for your answer')
                        : `${KINDS[n.kind]?.label} · ${wsName(n.ws)}${by === 'status' && n.team ? ` · ${n.team}` : ''}${(n.loops || []).some(l => l.on) ? ' · ⟳ loop' : ''}`}
                    </span>
                  </div>
                )
              })}
            </div>
          )
        })}
      </div>
    </aside>
  )
}
