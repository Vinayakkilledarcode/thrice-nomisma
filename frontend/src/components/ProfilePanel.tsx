import React, { useState, useEffect } from 'react'
import { api } from '../utils/api'
import { UserProfile, VaultNote } from '../utils/types'

interface Props {
  user: UserProfile | null
  onLogout: () => void
  onRefresh?: () => void
}

// Local shape of what we persist once the user signs off. Kept client-side
// (localStorage) for now since utils/api.ts doesn't expose a compliance
// endpoint yet — see the TODO near handleSignAgreement below for the
// backend hook this should eventually call instead.
interface ComplianceRecord {
  signedByName: string
  signedAt: number // epoch seconds
}

const COMPLIANCE_STORAGE_PREFIX = 'thrice_compliance_'

export default function ProfilePanel({ user, onLogout, onRefresh }: Props) {
  const [vaultOpen, setVaultOpen] = useState(false)
  const [notes, setNotes] = useState<VaultNote[]>([])
  const [newTitle, setNewTitle] = useState('')
  const [newContent, setNewContent] = useState('')
  const [vaultError, setVaultError] = useState('')
  const [vaultSuccess, setVaultSuccess] = useState('')
  const [imgError, setImgError] = useState(false)
  const [refreshing, setRefreshing] = useState(false)

  // --- SEBI / RBI trading compliance agreement state ---
  const [compliance, setCompliance] = useState<ComplianceRecord | null>(null)
  const [agreeChecked, setAgreeChecked] = useState(false)
  const [signing, setSigning] = useState(false)

  useEffect(() => {
    if (user && vaultOpen) {
      loadVaultNotes()
    }
  }, [user, vaultOpen])

  // Load any previously-signed agreement for this user on mount / user change
  useEffect(() => {
    if (!user?.email) {
      setCompliance(null)
      return
    }
    try {
      const raw = localStorage.getItem(COMPLIANCE_STORAGE_PREFIX + user.email)
      setCompliance(raw ? (JSON.parse(raw) as ComplianceRecord) : null)
    } catch {
      setCompliance(null)
    }
  }, [user?.email])

  const loadVaultNotes = async () => {
    try {
      const data = await api.getVaultNotes()
      setNotes(data)
    } catch (err) {
      console.error("Failed to load private notes", err)
    }
  }

  const handleSaveNote = async (e: React.FormEvent) => {
    e.preventDefault()
    setVaultError('')
    setVaultSuccess('')
    if (!newTitle || !newContent) {
      setVaultError('Please add a title and a note before saving.')
      return
    }
    try {
      await api.saveVaultNote({ title: newTitle, content: newContent })
      setVaultSuccess('Saved — only you can see this.')
      setNewTitle('')
      setNewContent('')
      loadVaultNotes()
      setTimeout(() => setVaultSuccess(''), 3000)
    } catch (err) {
      setVaultError(err instanceof Error ? err.message : "Something went wrong saving that note — mind trying again?")
    }
  }

  const handleSignAgreement = () => {
    if (!user || !agreeChecked || signing) return
    setSigning(true)
    const record: ComplianceRecord = {
      signedByName: user.name,
      signedAt: Math.floor(Date.now() / 1000)
    }
    // TODO(backend): once utils/api.ts exposes something like
    // api.signComplianceAgreement(record), call it here and only fall back
    // to localStorage on failure, so the signature is recorded server-side
    // (SEBI record-keeping norms for algo trading expect this to be
    // auditable, not just client-side state).
    try {
      localStorage.setItem(COMPLIANCE_STORAGE_PREFIX + user.email, JSON.stringify(record))
      setCompliance(record)
    } finally {
      setSigning(false)
    }
  }

  const getInitials = (name: string) => {
    if (!name) return 'U'
    const parts = name.trim().split(/\s+/)
    if (parts.length >= 2) {
      return (parts[0][0] + parts[1][0]).toUpperCase()
    }
    return name.slice(0, 2).toUpperCase()
  }

  if (!user) return null

  const nodeLabel = user.email.includes('@') 
    ? user.email.split('@')[1].toUpperCase() 
    : 'NOMISMA.LOCAL'

  const formattedDate = user.created_at 
    ? new Date(user.created_at * 1000).toLocaleDateString() 
    : 'N/A'

  return (
    <div className="profile-panel" style={{ display: 'flex', flexDirection: 'column', height: '100%', overflowY: 'auto' }}>
      <div className="profile-header">
        <div className="profile-avatar">
          {user.avatar && !imgError ? (
            <img 
              src={user.avatar} 
              alt={user.name} 
              className="profile-avatar-img" 
              onError={() => setImgError(true)}
            />
          ) : (
            <div className="profile-avatar-placeholder">
              {user.name ? getInitials(user.name) : 'U'}
            </div>
          )}
        </div>
        <div className="profile-info">
          <h3 className="profile-name" title={user.name}>{user.name}</h3>
          <p className="profile-email" title={user.email}>{user.email}</p>
        </div>
      </div>
      
      <div className="profile-actions" style={{ display: 'flex', flexDirection: 'column', gap: '14px', flex: 1 }}>

        {/* ─── Basic Info ─── */}
        <div className="info-card">
          <div className="info-card-title">Your details</div>
          <div className="info-grid">
            <div>
              <div className="info-field-label">Full Name</div>
              <div className="info-field-value">{user.name || 'N/A'}</div>
            </div>
            <div>
              <div className="info-field-label">Email</div>
              <div className="info-field-value">{user.email}</div>
            </div>
            <div>
              <div className="info-field-label">Workspace</div>
              <div className="info-field-value">{nodeLabel}</div>
            </div>
            <div>
              <div className="info-field-label">Membership</div>
              <div className="info-field-value">{user.clearance_level || 'Standard member'}</div>
            </div>
            <div className="info-grid-full">
              <div className="info-field-label">Member Since</div>
              <div className="info-field-value">{formattedDate}</div>
            </div>
          </div>
          {onRefresh && (
            <button
              type="button"
              className="profile-action-btn"
              onClick={() => {
                setRefreshing(true)
                onRefresh()
                setTimeout(() => setRefreshing(false), 600)
              }}
              style={{ marginTop: '14px', padding: '6px', fontSize: '10px' }}
            >
              {refreshing ? 'Syncing…' : 'Refresh my details'}
            </button>
          )}
        </div>

        {/* ─── SEBI / RBI Trading Compliance Agreement ─── */}
        <div className={`compliance-card ${compliance ? 'signed' : ''}`}>
          <div className="info-card-title">Trading risk &amp; regulatory agreement</div>

          {compliance ? (
            <div className="compliance-signed-badge">
              <span>✅</span>
              <span>
                You signed this on{' '}
                {new Date(compliance.signedAt * 1000).toLocaleString()} — thanks, {compliance.signedByName}.
              </span>
            </div>
          ) : (
            <>
              <div className="compliance-text">
                This is a self-directed algorithmic trading tool for Indian equities
                (NSE/BSE). By continuing to use it you acknowledge that: (1) all trading and
                investment decisions, including those executed by automated strategies, remain
                your own responsibility; (2) securities market activity is subject to SEBI
                regulations, including applicable rules for algorithmic and API-based trading,
                and to exchange-specific risk-management circulars; (3) any funds transfer,
                margin, or payment-related activity is subject to RBI guidelines governing
                electronic and banking transactions; and (4) you will independently verify your
                broker's (e.g. Angel One SmartAPI) terms, margin, and risk-disclosure documents
                before placing live orders. Past performance of any strategy shown here is not
                indicative of future results.
              </div>
              <label className="compliance-checkbox-row">
                <input
                  type="checkbox"
                  checked={agreeChecked}
                  onChange={e => setAgreeChecked(e.target.checked)}
                />
                <span>
                  I have read the above and agree to the SEBI/RBI-related trading and regulatory
                  terms for using this terminal.
                </span>
              </label>
              <button
                type="button"
                className="login-submit-btn"
                disabled={!agreeChecked || signing}
                onClick={handleSignAgreement}
                style={{
                  margin: '14px 0 0',
                  padding: '10px',
                  fontSize: '11px',
                  opacity: !agreeChecked || signing ? 0.5 : 1,
                  cursor: !agreeChecked || signing ? 'not-allowed' : 'pointer'
                }}
              >
                {signing ? 'Signing…' : 'I agree, sign me up'}
              </button>
              <p className="compliance-disclaimer">
                Quick note: this is a placeholder summary, not legal advice. Please have it
                reviewed by a SEBI-registered compliance professional or lawyer before treating
                it as your actual regulatory disclosure.
              </p>
            </>
          )}
        </div>

        <button 
          type="button"
          className="profile-action-btn" 
          onClick={() => setVaultOpen(!vaultOpen)}
          style={{ 
            border: vaultOpen ? '1px solid var(--gold)' : '1px solid var(--border-subtle)', 
            color: vaultOpen ? 'var(--gold-bright)' : 'var(--silver)' 
          }}
        >
          {vaultOpen ? 'Close my private notes' : 'Open my private notes'}
        </button>

        {vaultOpen ? (
          <div className="vault-terminal" style={{ 
            background: 'var(--bg-base)', 
            border: '1px solid var(--border)', 
            borderRadius: '6px', 
            padding: '16px', 
            display: 'flex', 
            flexDirection: 'column', 
            gap: '12px' 
          }}>
            <h4 style={{ fontFamily: 'var(--font-ui)', color: 'var(--gold)', fontSize: '13px', fontWeight: 700 }}>
              Private notes
            </h4>
            <p style={{ fontSize: '11px', color: 'var(--silver-dim)', marginTop: '-6px' }}>
              Encrypted and visible only to you — a good spot for API keys, account labels, or anything you'd rather not lose.
            </p>

            <form onSubmit={handleSaveNote} style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
              <input 
                type="text" 
                placeholder="Note title (e.g. Angel One API key)" 
                value={newTitle} 
                onChange={e => setNewTitle(e.target.value)}
                style={{ 
                  background: 'var(--bg-surface)', 
                  border: '1px solid var(--border-subtle)', 
                  borderRadius: '4px', 
                  color: 'var(--gold-bright)', 
                  fontSize: '11px', 
                  padding: '8px', 
                  outline: 'none',
                  fontFamily: 'var(--font-data)'
                }}
              />
              <textarea 
                placeholder="Write your note here…" 
                value={newContent} 
                onChange={e => setNewContent(e.target.value)}
                style={{ 
                  background: 'var(--bg-surface)', 
                  border: '1px solid var(--border-subtle)', 
                  borderRadius: '4px', 
                  color: 'var(--gold-bright)', 
                  fontSize: '11px', 
                  padding: '8px', 
                  outline: 'none',
                  minHeight: '40px',
                  resize: 'vertical',
                  fontFamily: 'var(--font-data)'
                }}
              />
              {vaultError && <p style={{ color: 'var(--negative)', fontSize: '10px' }}>{vaultError}</p>}
              {vaultSuccess && <p style={{ color: 'var(--positive)', fontSize: '10px' }}>{vaultSuccess}</p>}
              <button type="submit" className="login-submit-btn" style={{ margin: 0, padding: '8px', fontSize: '11px' }}>
                Save note
              </button>
            </form>

            <div className="vault-secrets-list" style={{ marginTop: '8px', display: 'flex', flexDirection: 'column', gap: '6px', maxHeight: '150px', overflowY: 'auto' }}>
              <p style={{ fontSize: '10px', color: 'var(--silver-dim)', borderBottom: '1px solid var(--border-subtle)', paddingBottom: '4px' }}>
                Saved notes ({notes.length})
              </p>
              {notes.length === 0 ? (
                <p style={{ fontSize: '10px', color: 'var(--silver-dim)', fontStyle: 'italic' }}>Nothing saved yet — your first note will show up here.</p>
              ) : (
                notes.map((note, idx) => (
                  <div key={idx} style={{ background: 'var(--bg-raised)', padding: '8px', borderRadius: '4px', borderLeft: '2px solid var(--gold)' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <span style={{ fontFamily: 'var(--font-data)', fontSize: '10px', color: 'var(--gold-bright)', fontWeight: 'bold' }}>
                        {note.note_title}
                      </span>
                      <span style={{ fontSize: '9px', color: 'var(--silver-dim)' }}>
                        {new Date(note.updated_at * 1000).toLocaleDateString()}
                      </span>
                    </div>
                    <p style={{ 
                      fontFamily: 'var(--font-data)', 
                      fontSize: '10px', 
                      color: 'var(--silver-bright)', 
                      wordBreak: 'break-all', 
                      marginTop: '4px', 
                      background: 'var(--bg-base)', 
                      padding: '4px', 
                      borderRadius: '2px' 
                    }}>
                      {note.note_content}
                    </p>
                  </div>
                ))
              )}
            </div>
          </div>
        ) : null}

        <button 
          type="button" 
          className="profile-action-btn" 
          onClick={onLogout}
          style={{ 
            marginTop: 'auto',
            background: 'rgba(255, 74, 90, 0.05)', 
            border: '1px solid var(--negative)', 
            color: 'var(--negative)',
            textAlign: 'center',
            fontWeight: 'bold'
          }}
        >
          Sign out
        </button>
      </div>
    </div>
  )
}