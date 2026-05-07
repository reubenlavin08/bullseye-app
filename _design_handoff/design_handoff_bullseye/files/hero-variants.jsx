// Bullseye — Hero wireframe variants (4 directions)
// Low-fi sketches; pick one and we promote it to hi-fi.

function HeroVariants() {
  return (
    <div className="bs-root" style={{width: 1180, padding: 28, background:'var(--bg)'}}>
      <div className="kicker">hero · wireframes</div>
      <h2 style={{fontSize: 22, marginTop: 6}}>Four directions for the landing hero</h2>
      <p className="muted" style={{margin:'4px 0 24px', fontSize: 13, maxWidth: 620}}>
        Same headline + sub the whole way: <em>"Find Marketplace deals before everyone else."</em>{' '}
        Position is "make money for resellers · cheaper than the alternatives". What changes is the layout
        and what we put on the right.
      </p>

      <div style={{display:'grid', gridTemplateColumns:'1fr 1fr', gap: 18}}>
        <Wire tag="A · LIVE FEED (current)" note="Right column shows the real product feed; trust comes from showing the work.">
          <WireFrame>
            <Headline/>
            <RightFeed/>
          </WireFrame>
        </Wire>

        <Wire tag="B · BIG SCORE" note="One huge score number anchors the page. Strong, more game-y.">
          <WireFrame>
            <Headline/>
            <RightBigScore/>
          </WireFrame>
        </Wire>

        <Wire tag="C · CENTER + RECEIPT" note="Wispr-Flow-style: centered text, single floating receipt artifact below. Calmest.">
          <WireFrameCentered>
            <CenteredHeadline/>
            <FloatingReceipt/>
          </WireFrameCentered>
        </Wire>

        <Wire tag="D · BEFORE/AFTER" note='Reseller framing: "what you saw vs. what you missed". Most concrete value-prop.'>
          <WireFrame>
            <Headline reseller/>
            <RightBeforeAfter/>
          </WireFrame>
        </Wire>
      </div>
    </div>
  );
}

function Wire({tag, note, children}) {
  return (
    <div>
      <div style={{display:'flex', alignItems:'baseline', gap: 12, marginBottom: 8}}>
        <span className="kicker">{tag}</span>
      </div>
      <div className="card" style={{padding: 0, overflow:'hidden', background:'var(--bg-elev)'}}>
        {children}
      </div>
      <p className="muted" style={{fontSize: 12, marginTop: 8, lineHeight: 1.5}}>{note}</p>
    </div>
  );
}

// Wireframe primitives ----------------------------------------------------
const wireBox = {background:'var(--bg-sunk)', border:'1px dashed var(--border-strong)', borderRadius: 4, color:'var(--muted)', display:'flex', alignItems:'center', justifyContent:'center', fontFamily:'var(--mono)', fontSize: 10, letterSpacing:'0.04em'};
const navBar = {height: 36, borderBottom:'1px solid var(--border)', display:'flex', alignItems:'center', justifyContent:'space-between', padding:'0 18px', fontSize: 11, color:'var(--muted)', fontFamily:'var(--mono)'};

function WireFrame({children}) {
  return (
    <div>
      <div style={navBar}>
        <span>○ bullseye</span>
        <span>how · pricing · faq · download</span>
      </div>
      <div style={{display:'grid', gridTemplateColumns:'1fr 1fr', gap: 18, padding: 22, minHeight: 280}}>
        {children}
      </div>
    </div>
  );
}

function WireFrameCentered({children}) {
  return (
    <div>
      <div style={{...navBar, justifyContent:'center', gap: 32}}>
        <span>○ bullseye</span>
        <span>how · pricing · faq · download</span>
      </div>
      <div style={{padding: 22, minHeight: 280, display:'flex', flexDirection:'column', alignItems:'center', textAlign:'center'}}>
        {children}
      </div>
    </div>
  );
}

function Headline({reseller}) {
  return (
    <div style={{display:'flex', flexDirection:'column', justifyContent:'center', gap: 10}}>
      <div style={{...wireBox, height: 14, width: '50%', background:'var(--accent-soft)', border:'1px dashed #f1d9c5'}}>
        <span>● kicker · live in oakland</span>
      </div>
      <div style={{height: 36, background:'var(--fg)', opacity: 0.85, borderRadius: 3}}/>
      <div style={{height: 36, background:'var(--fg)', opacity: 0.85, borderRadius: 3, width: '85%'}}/>
      <div style={{height: 12, background:'var(--muted-2)', opacity: 0.5, borderRadius: 2, marginTop: 8}}/>
      <div style={{height: 12, background:'var(--muted-2)', opacity: 0.5, borderRadius: 2, width:'80%'}}/>
      <div style={{height: 12, background:'var(--muted-2)', opacity: 0.5, borderRadius: 2, width:'60%'}}/>
      {reseller && <div style={{height: 12, background:'var(--accent-2)', opacity: 0.7, borderRadius: 2, width:'70%', marginTop: 4}}/>}
      <div style={{display:'flex', gap: 8, marginTop: 14}}>
        <div style={{height: 28, width: 130, background:'var(--accent)', borderRadius: 4}}/>
        <div style={{height: 28, width: 130, border:'1px solid var(--border-strong)', borderRadius: 4}}/>
      </div>
      <div style={{...wireBox, height: 12, width:'70%', marginTop: 6, border:'none', background:'transparent', justifyContent:'flex-start'}}>no card · 14-day money back · free tier</div>
    </div>
  );
}

function CenteredHeadline() {
  return (
    <>
      <div style={{...wireBox, height: 14, width: 220, background:'var(--accent-soft)', border:'1px dashed #f1d9c5', marginBottom: 14}}>
        <span>● live in oakland</span>
      </div>
      <div style={{height: 40, background:'var(--fg)', opacity:.85, borderRadius: 3, width:'70%'}}/>
      <div style={{height: 40, background:'var(--fg)', opacity:.85, borderRadius: 3, width:'55%', marginTop: 8}}/>
      <div style={{height: 12, background:'var(--muted-2)', opacity:.5, borderRadius: 2, width:'60%', marginTop: 18}}/>
      <div style={{height: 12, background:'var(--muted-2)', opacity:.5, borderRadius: 2, width:'45%', marginTop: 6}}/>
      <div style={{display:'flex', gap: 8, marginTop: 18}}>
        <div style={{height: 30, width: 140, background:'var(--accent)', borderRadius: 4}}/>
        <div style={{height: 30, width: 140, border:'1px solid var(--border-strong)', borderRadius: 4}}/>
      </div>
    </>
  );
}

function RightFeed() {
  return (
    <div style={{...wireBox, flexDirection:'column', alignItems:'stretch', justifyContent:'flex-start', padding: 0, background:'var(--bg-elev)', border:'1px solid var(--border)'}}>
      <div style={{padding:'8px 12px', borderBottom:'1px solid var(--border)', display:'flex', justifyContent:'space-between'}}>
        <span>● appraisal feed · live</span>
        <span>23 polls · 2h</span>
      </div>
      {[78, 64, 52].map((s, i) => (
        <div key={i} style={{display:'flex', alignItems:'center', gap: 10, padding: 10, borderBottom: i < 2 ? '1px solid var(--border)' : 'none', background: i === 0 ? 'var(--accent-soft)' : 'transparent'}}>
          <div style={{width: 36, height: 36, background:'var(--bg-sunk)', borderRadius: 3}}/>
          <div style={{flex: 1, display:'flex', flexDirection:'column', gap: 4}}>
            <div style={{height: 8, background:'var(--fg)', opacity:.5, borderRadius: 2, width:'70%'}}/>
            <div style={{height: 7, background:'var(--muted-2)', opacity:.5, borderRadius: 2, width:'50%'}}/>
          </div>
          <span style={{fontFamily:'var(--serif)', fontSize: 14, color: s >= 70 ? 'var(--accent-2)' : 'var(--warn)', fontVariantNumeric:'tabular-nums'}}>{s}</span>
        </div>
      ))}
    </div>
  );
}

function RightBigScore() {
  return (
    <div style={{...wireBox, flexDirection:'column', justifyContent:'center', background:'var(--bg-elev)', border:'1px solid var(--border)', position:'relative'}}>
      <div style={{fontFamily:'var(--serif)', fontSize: 110, color:'var(--accent-2)', lineHeight: 1, fontVariantNumeric:'tabular-nums'}}>78</div>
      <div style={{height: 8, width: 100, background:'var(--muted-2)', opacity:.4, borderRadius: 2, marginTop: 14}}/>
      <div style={{height: 8, width: 70, background:'var(--muted-2)', opacity:.4, borderRadius: 2, marginTop: 6}}/>
      <div style={{position:'absolute', bottom: 10, left: 12, right: 12, display:'flex', justifyContent:'space-between', fontSize: 9, color:'var(--muted)'}}>
        <span>asking $380</span><span>fair $540</span><span>−$160</span>
      </div>
    </div>
  );
}

function FloatingReceipt() {
  return (
    <div style={{
      marginTop: 22, width: 360, padding: 14,
      background:'var(--bg-elev)', border:'1px solid var(--border)', borderRadius: 6,
      boxShadow:'var(--shadow-2)', transform:'rotate(-1deg)', textAlign:'left',
      fontFamily:'var(--mono)', fontSize: 10, color:'var(--muted)',
    }}>
      <div style={{display:'flex', justifyContent:'space-between', marginBottom: 8}}>
        <span style={{fontFamily:'var(--serif)', fontSize: 13, color:'var(--fg)'}}>Canon AE-1 Program</span>
        <span style={{fontFamily:'var(--serif)', fontSize: 18, color:'var(--accent-2)'}}>78</span>
      </div>
      {['asking $380','fair $540 ±$35','median $565','under median −$160'].map((l,i) => (
        <div key={i} style={{display:'flex', justifyContent:'space-between', borderBottom:'1px dashed var(--border)', padding:'3px 0'}}>
          <span>{l.split(' ')[0]}</span><span style={{color:'var(--fg)'}}>{l.split(' ').slice(1).join(' ')}</span>
        </div>
      ))}
    </div>
  );
}

function RightBeforeAfter() {
  return (
    <div style={{display:'flex', flexDirection:'column', gap: 10}}>
      <div style={{...wireBox, padding: 10, height: 92, flexDirection:'column', alignItems:'flex-start', justifyContent:'flex-start', gap: 6}}>
        <div style={{fontSize: 9, color:'var(--muted)', textTransform:'uppercase', letterSpacing:'0.05em'}}>without bullseye · today</div>
        <div style={{height: 9, background:'var(--muted-2)', opacity:.4, borderRadius: 2, width:'80%'}}/>
        <div style={{height: 9, background:'var(--muted-2)', opacity:.4, borderRadius: 2, width:'60%'}}/>
        <div style={{display:'flex', justifyContent:'space-between', width:'100%', fontSize: 10, fontFamily:'var(--mono)'}}>
          <span style={{color:'var(--muted)'}}>refresh refresh refresh…</span>
          <span style={{color:'var(--accent)'}}>missed by 8 min</span>
        </div>
      </div>
      <div style={{...wireBox, padding: 10, height: 130, flexDirection:'column', alignItems:'flex-start', justifyContent:'flex-start', gap: 6, background:'var(--accent-soft)', border:'1px solid #f1d9c5'}}>
        <div style={{fontSize: 9, color:'var(--accent)', textTransform:'uppercase', letterSpacing:'0.05em'}}>with bullseye</div>
        <div style={{display:'flex', alignItems:'center', gap: 8, width:'100%'}}>
          <span style={{fontFamily:'var(--serif)', fontSize: 24, color:'var(--accent-2)'}}>78</span>
          <div style={{flex: 1}}>
            <div style={{height: 8, background:'var(--fg)', opacity:.5, borderRadius: 2, width:'80%'}}/>
            <div style={{height: 8, background:'var(--muted-2)', opacity:.4, borderRadius: 2, width:'60%', marginTop: 4}}/>
          </div>
        </div>
        <div style={{fontSize: 10, fontFamily:'var(--mono)', color:'var(--accent-2)'}}>+$160 margin · alerted in 4 sec</div>
        <div style={{height: 22, width: 100, background:'var(--accent)', borderRadius: 3, marginTop: 4}}/>
      </div>
    </div>
  );
}

window.HeroVariants = HeroVariants;
