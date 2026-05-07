// Bullseye — logo sketch sheet (4 directions)
// All concepts share the burnt-orange accent on warm off-white.

const Logo = {};

// Concept A — concentric target rings + wordmark
Logo.A = function ConceptA({ size = 28 }) {
  return (
    <span style={{display:'inline-flex', alignItems:'center', gap: 10}}>
      <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
        <circle cx="16" cy="16" r="15" fill="none" stroke="#1a1614" strokeWidth="1.25"/>
        <circle cx="16" cy="16" r="10" fill="none" stroke="#1a1614" strokeWidth="1.25"/>
        <circle cx="16" cy="16" r="5"  fill="none" stroke="#1a1614" strokeWidth="1.25"/>
        <circle cx="16" cy="16" r="1.6" fill="#c2410c"/>
      </svg>
      <span style={{fontFamily:'Georgia, serif', fontSize: size*0.78, letterSpacing:'-0.01em'}}>bullseye</span>
    </span>
  );
};

// Concept B — target rings with arrow embedded
Logo.B = function ConceptB({ size = 28 }) {
  return (
    <span style={{display:'inline-flex', alignItems:'center', gap: 10}}>
      <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
        <circle cx="14" cy="18" r="13" fill="none" stroke="#1a1614" strokeWidth="1.25"/>
        <circle cx="14" cy="18" r="8"  fill="none" stroke="#1a1614" strokeWidth="1.25"/>
        <circle cx="14" cy="18" r="3.5" fill="#c2410c"/>
        {/* arrow shaft */}
        <line x1="22" y1="10" x2="14" y2="18" stroke="#1a1614" strokeWidth="1.5" strokeLinecap="round"/>
        {/* fletching */}
        <path d="M22 10 L26 6 M22 10 L26 10 M22 10 L22 6" stroke="#1a1614" strokeWidth="1.25" strokeLinecap="round" fill="none"/>
      </svg>
      <span style={{fontFamily:'Georgia, serif', fontSize: size*0.78, letterSpacing:'-0.01em'}}>bullseye</span>
    </span>
  );
};

// Concept C — bullseye dot replaces the 'i' dot in the wordmark
Logo.C = function ConceptC({ size = 28 }) {
  // Render wordmark with a target glyph in place of the 'i' dot.
  // Using SVG for precise placement.
  const fs = size * 0.85;
  return (
    <svg width={fs * 4.7} height={fs * 1.2} viewBox="0 0 240 36" aria-label="bullseye" style={{display:'block'}}>
      <text x="0" y="29" fontFamily="Georgia, serif" fontSize="30" fill="#1a1614" letterSpacing="-0.5">bullseye</text>
      {/* mask out the 'i' dot above 'i' (the 4th char in 'bullseye' — index of i is in 'bullsey'? actually 'bullseye' has no 'i'). 
         Place a target glyph above the second 'l' instead, acting as the focal glyph. */}
      {/* Actually use 'y' descender area — put a small target replacing the dot of a stylized b -- simplest: tiny target after wordmark */}
      <g transform="translate(150, 8)">
        <circle cx="0" cy="0" r="8" fill="none" stroke="#1a1614" strokeWidth="1.25"/>
        <circle cx="0" cy="0" r="4.5" fill="none" stroke="#1a1614" strokeWidth="1.25"/>
        <circle cx="0" cy="0" r="1.6" fill="#c2410c"/>
      </g>
    </svg>
  );
};

// Concept D — monogram B inside circle (favicon-friendly)
Logo.D = function ConceptD({ size = 28 }) {
  return (
    <span style={{display:'inline-flex', alignItems:'center', gap: 10}}>
      <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
        <circle cx="16" cy="16" r="15" fill="#1a1614"/>
        <text x="16" y="22" textAnchor="middle" fontFamily="Georgia, serif" fontSize="18" fill="#fafaf7">b</text>
        <circle cx="23" cy="9" r="2" fill="#c2410c"/>
      </svg>
      <span style={{fontFamily:'Georgia, serif', fontSize: size*0.78, letterSpacing:'-0.01em'}}>bullseye</span>
    </span>
  );
};

// Sketch-sheet artboard
function LogoSheet() {
  const cell = {
    padding: '32px 28px',
    borderRight: '1px solid var(--border)',
    borderBottom: '1px solid var(--border)',
    display: 'flex', flexDirection: 'column', gap: 18,
    background: 'var(--bg-elev)',
  };
  const tag = {fontFamily:'var(--mono)', fontSize:11, color:'var(--muted)', letterSpacing:'0.04em'};
  const note = {fontSize: 12, color:'var(--muted)', lineHeight: 1.5};
  return (
    <div className="bs-root" style={{width: 920, background:'var(--bg)'}}>
      <div style={{padding:'24px 28px', borderBottom:'1px solid var(--border)'}}>
        <div className="kicker">logo · sketch sheet</div>
        <h2 style={{fontSize: 22, marginTop: 6}}>Four directions for the bullseye mark</h2>
        <p className="muted" style={{margin:'4px 0 0', fontSize: 13}}>
          All in burnt-orange #c2410c on warm off-white. Wordmark in Georgia.
        </p>
      </div>

      <div style={{display:'grid', gridTemplateColumns:'1fr 1fr', borderTop:'1px solid var(--border)'}}>
        {/* A */}
        <div style={cell}>
          <div style={tag}>A · CONCENTRIC RINGS</div>
          <div style={{padding:'18px 0'}}><Logo.A size={36}/></div>
          <div style={note}>Pure target — calm, neutral, scales to favicon. Reads as a measurement device, not a game.</div>
          <div style={{display:'flex', gap: 18, alignItems:'center', paddingTop: 8}}>
            <Logo.A size={22}/>
            <Logo.A size={16}/>
            {/* favicon swatch */}
            <span style={{
              width:32, height:32, borderRadius:6, background:'var(--fg)',
              display:'flex', alignItems:'center', justifyContent:'center'
            }}>
              <svg width="20" height="20" viewBox="0 0 32 32">
                <circle cx="16" cy="16" r="13" fill="none" stroke="#fafaf7" strokeWidth="1.5"/>
                <circle cx="16" cy="16" r="7" fill="none" stroke="#fafaf7" strokeWidth="1.5"/>
                <circle cx="16" cy="16" r="2" fill="#c2410c"/>
              </svg>
            </span>
          </div>
        </div>

        {/* B */}
        <div style={{...cell, borderRight: 'none'}}>
          <div style={tag}>B · TARGET + ARROW</div>
          <div style={{padding:'18px 0'}}><Logo.B size={36}/></div>
          <div style={note}>More literal — says "we hit the deal". Slight risk of looking sporty/gamified at small sizes.</div>
          <div style={{display:'flex', gap: 18, alignItems:'center', paddingTop: 8}}>
            <Logo.B size={22}/>
            <Logo.B size={16}/>
            <span style={{
              width:32, height:32, borderRadius:6, background:'var(--fg)',
              display:'flex', alignItems:'center', justifyContent:'center'
            }}>
              <svg width="20" height="20" viewBox="0 0 32 32">
                <circle cx="14" cy="18" r="11" fill="none" stroke="#fafaf7" strokeWidth="1.5"/>
                <circle cx="14" cy="18" r="3" fill="#c2410c"/>
                <line x1="22" y1="10" x2="14" y2="18" stroke="#fafaf7" strokeWidth="1.6" strokeLinecap="round"/>
              </svg>
            </span>
          </div>
        </div>

        {/* C */}
        <div style={{...cell, borderBottom: 'none'}}>
          <div style={tag}>C · INLINE MARK</div>
          <div style={{padding:'18px 0'}}><Logo.C size={32}/></div>
          <div style={note}>Wordmark + tiny target glyph trailing the type. Best when the target needs breathing room from the word.</div>
        </div>

        {/* D */}
        <div style={{...cell, borderRight:'none', borderBottom:'none'}}>
          <div style={tag}>D · MONOGRAM</div>
          <div style={{padding:'18px 0'}}><Logo.D size={36}/></div>
          <div style={note}>App-icon first. Dark fill for tray and dock. Orange dot is the "found it" pulse.</div>
          <div style={{display:'flex', gap: 18, alignItems:'center', paddingTop: 8}}>
            <span style={{
              width:32, height:32, borderRadius:7, background:'var(--fg)',
              display:'flex', alignItems:'center', justifyContent:'center'
            }}>
              <svg width="22" height="22" viewBox="0 0 32 32">
                <text x="16" y="22" textAnchor="middle" fontFamily="Georgia, serif" fontSize="18" fill="#fafaf7">b</text>
                <circle cx="23" cy="9" r="2" fill="#c2410c"/>
              </svg>
            </span>
            <span className="muted" style={{fontSize:12}}>system tray · favicon · dock</span>
          </div>
        </div>
      </div>

      <div style={{padding:'16px 28px', borderTop:'1px solid var(--border)', background:'var(--bg-sunk)'}}>
        <div className="muted" style={{fontSize:12}}>
          <strong style={{color:'var(--fg)', fontWeight:500}}>Recommendation:</strong>{' '}
          A for landing &amp; product chrome, D for app icon &amp; tray. B reserved for marketing moments.
        </div>
      </div>
    </div>
  );
}

window.Logo = Logo;
window.LogoSheet = LogoSheet;
