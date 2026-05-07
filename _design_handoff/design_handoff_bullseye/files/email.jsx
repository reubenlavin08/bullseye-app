// Bullseye — email digest mock (HTML email rendered in a "device")
function EmailDigest() {
  const cameras = [
    { t:'Canon AE-1 Program w/ 50mm f/1.8', p: 380, f: 540, c: 35, s: 78, loc:'Oakland · 4.2mi', when:'12m ago' },
    { t:'Hasselblad 500C/M w/ 80mm CF',     p: 1450, f: 1850, c: 120, s: 71, loc:'Berkeley · 22mi', when:'24m ago' },
  ];
  const electronics = [
    { t:'LG 27UK850 4K USB-C monitor', p: 410, f: 460, c: 30, s: 52, loc:'Alameda · 5.1mi', when:'34m ago' },
  ];

  return (
    <div style={{
      width: 720, background:'#e8e1d3', padding: 28, fontFamily:'var(--sans)',
    }}>
      {/* gmail-ish chrome to give it context */}
      <div style={{display:'flex', alignItems:'center', justifyContent:'space-between', marginBottom: 12, fontSize: 11, color:'var(--muted)', fontFamily:'var(--mono)'}}>
        <span>From: alerts@bullseye.app</span>
        <span>Today · 8:00 AM</span>
      </div>

      {/* email body */}
      <div style={{background:'#fafaf7', border:'1px solid var(--border)', borderRadius: 6, overflow:'hidden'}}>
        {/* header */}
        <div style={{padding:'24px 28px', borderBottom:'1px solid var(--border)'}}>
          <div style={{display:'flex', alignItems:'center', gap: 10, marginBottom: 16}}>
            <Logo.A size={20}/>
          </div>
          <div style={{fontFamily:'var(--serif)', fontSize: 26, color:'var(--fg)', letterSpacing:'-0.01em'}}>
            3 new deals — May 4
          </div>
          <div style={{fontSize: 13, color:'var(--muted)', marginTop: 6}}>
            From your 23 active watches · highest score <span style={{color:'var(--accent)', fontWeight: 500}}>78</span>
          </div>
        </div>

        {/* keyword section */}
        <Section title="Cameras · 2 deals">
          {cameras.map((d, i) => <DealCard key={i} d={d}/>)}
        </Section>
        <Section title="Electronics · 1 deal">
          {electronics.map((d, i) => <DealCard key={i} d={d}/>)}
        </Section>

        {/* footer */}
        <div style={{padding:'18px 28px', background:'var(--bg-sunk)', borderTop:'1px solid var(--border)', fontSize: 11, color:'var(--muted)', lineHeight: 1.6}}>
          You're getting this because you're on the daily 8am digest.<br/>
          <a href="#" style={{color:'var(--muted)'}}>Manage watches</a> ·{' '}
          <a href="#" style={{color:'var(--muted)'}}>Switch to instant (Pro)</a> ·{' '}
          <a href="#" style={{color:'var(--muted)'}}>Unsubscribe</a>
        </div>
      </div>
    </div>
  );
}

function Section({title, children}) {
  return (
    <div style={{padding:'18px 28px 8px', borderBottom:'1px solid var(--border)'}}>
      <div style={{
        fontFamily:'var(--mono)', fontSize: 11, color:'var(--muted)',
        textTransform:'uppercase', letterSpacing:'0.06em', marginBottom: 12
      }}>{title}</div>
      <div style={{display:'flex', flexDirection:'column', gap: 10}}>{children}</div>
    </div>
  );
}

function DealCard({d}) {
  const tone = d.s >= 70 ? 'score-good' : d.s >= 50 ? 'score-ok' : 'score-meh';
  return (
    <a href="#" style={{
      display:'grid', gridTemplateColumns:'88px 1fr auto', gap: 16, alignItems:'stretch',
      padding: 14, background: '#fff',
      border:'1px solid var(--border)', borderRadius: 6, textDecoration:'none', color:'var(--fg)',
    }}>
      <div className="ph" style={{height: 88, fontSize: 9, borderRadius: 4}}>photo</div>
      <div style={{minWidth: 0, display:'flex', flexDirection:'column', justifyContent:'space-between'}}>
        <div>
          <div style={{fontFamily:'var(--serif)', fontSize: 16, color:'var(--fg)', overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap'}}>{d.t}</div>
          <div style={{fontSize: 12, color:'var(--muted)', marginTop: 4}}>{d.loc} · {d.when}</div>
        </div>
        <div style={{display:'flex', gap: 18, marginTop: 10, fontSize: 12, fontFamily:'var(--mono)', color:'var(--muted)'}}>
          <span>asking <span style={{color:'var(--accent)', fontVariantNumeric:'tabular-nums'}}>${d.p}</span></span>
          <span>fair <span style={{color:'var(--fg)', fontVariantNumeric:'tabular-nums'}}>${d.f} ±${d.c}</span></span>
          <span style={{color:'var(--accent-2)'}}>−${d.f - d.p}</span>
        </div>
      </div>
      <div style={{display:'flex', flexDirection:'column', alignItems:'flex-end', justifyContent:'space-between', gap: 8}}>
        <span className={`score ${tone}`}><span className="num">{d.s}</span><span className="pct">/100</span></span>
        <span style={{fontSize: 12, color:'var(--accent)', fontWeight: 500}}>open →</span>
      </div>
    </a>
  );
}

window.EmailDigest = EmailDigest;
