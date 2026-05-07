// Bullseye — Landing v2: reseller positioning, Free vs Pro only,
// Hub-app shown as the floating product artifact.
function LandingV2() {
  return (
    <div className="bs-root" style={{width: 1180}}>
      {/* Top nav */}
      <div style={{display:'flex', alignItems:'center', justifyContent:'space-between', padding:'20px 40px', borderBottom:'1px solid var(--border)'}}>
        <Logo.B size={26}/>
        <div style={{display:'flex', alignItems:'center', gap: 28, fontSize: 13}}>
          <a className="muted" href="#">How it works</a>
          <a className="muted" href="#">Pricing</a>
          <a className="muted" href="#">For resellers</a>
          <a className="muted" href="#">Sign in</a>
          <button className="btn btn-primary btn-sm">Download free</button>
        </div>
      </div>

      {/* Hero — variant A (live feed) keeps "show the work" trust */}
      <div style={{padding:'80px 40px 70px', display:'grid', gridTemplateColumns:'1.05fr 1fr', gap: 56, alignItems:'center'}}>
        <div>
          <div className="kicker" style={{marginBottom: 16}}>● new listing · oakland · 12s ago</div>
          <h1 style={{fontSize: 60, lineHeight: 1.04, letterSpacing:'-0.025em'}}>
            Resellers, stop<br/>refreshing Marketplace.
          </h1>
          <p style={{fontSize: 17, color:'var(--muted)', marginTop: 24, maxWidth: 480, lineHeight: 1.6}}>
            Bullseye watches Facebook Marketplace 24/7 and pings you the second a
            listing is priced below market — scored against real eBay sale comps,
            with the math shown. No black-box rating.
          </p>
          <div style={{display:'flex', gap: 12, marginTop: 32}}>
            <button className="btn btn-primary btn-lg" style={{paddingLeft: 22, paddingRight: 22}}>Try Pro free for 7 days</button>
            <button className="btn btn-lg">Download for Windows</button>
          </div>
          <div style={{marginTop: 16, fontSize: 12, color:'var(--muted)'}}>
            $9.99/mo Pro · free tier forever · 14-day money-back · no card required
          </div>
        </div>
        <HeroDemo/>
      </div>

      {/* Reseller stats band */}
      <div style={{padding:'28px 40px', background:'var(--accent-soft)', borderTop:'1px solid #f1d9c5', borderBottom:'1px solid #f1d9c5'}}>
        <div style={{display:'grid', gridTemplateColumns:'repeat(4, 1fr)', gap: 24}}>
          <Stat2 v="< 5 min" k="from listing to your inbox"/>
          <Stat2 v="23" k="comps per appraisal · trimmed median"/>
          <Stat2 v="$9.99" k="per month · half what alternatives cost"/>
          <Stat2 v="0" k="black-box scores. ever."/>
        </div>
      </div>

      {/* How it works */}
      <div style={{padding:'72px 40px'}}>
        <div style={{display:'flex', alignItems:'baseline', justifyContent:'space-between', marginBottom: 36}}>
          <h2 style={{fontSize: 32}}>From listing to alert in three steps.</h2>
          <span className="kicker">how it works</span>
        </div>
        <div style={{display:'grid', gridTemplateColumns:'1fr 1fr 1fr', gap: 28}}>
          <Step n="01" title="Tell it what you flip" body="Cameras, hi-fi, kitchen, electronics — paste your keywords. Set a max distance and ceiling price."/>
          <Step n="02" title="It watches every 5 minutes" body="New listings get appraised against eBay sold comps from the last 90 days, with a real confidence interval."/>
          <Step n="03" title="You get pinged instantly" body="Score above your threshold? Email + desktop toast within seconds. Open the listing, decide, message."/>
        </div>
      </div>

      {/* Free vs Pro only */}
      <div style={{padding:'40px 40px 80px', borderTop:'1px solid var(--border)', background:'var(--bg-sunk)'}}>
        <div style={{display:'flex', alignItems:'baseline', justifyContent:'space-between', marginBottom: 28}}>
          <h2 style={{fontSize: 32}}>Free or Pro.</h2>
          <span className="kicker">pricing</span>
        </div>
        <div style={{display:'grid', gridTemplateColumns:'1fr 1fr', gap: 18, maxWidth: 920}}>
          <Plan
            tag="FREE"
            price="$0"
            sub="forever"
            cta="Download"
            features={[
              ['3 active watches'],
              ['30 minute polling'],
              ['Daily digest email'],
              ['Score with 1-line summary'],
              ['', false],
              ['', false],
            ]}
          />
          <Plan
            tag="PRO"
            price="$9.99"
            sub="per month · 7-day free trial"
            cta="Try free"
            highlighted
            features={[
              ['Unlimited watches'],
              ['5 minute polling'],
              ['Instant email + desktop toast'],
              ['Full breakdown · math + 23 comps'],
              ['Live observability dashboard'],
              ['LLM listing verification'],
            ]}
          />
        </div>
        <p className="muted" style={{fontSize: 12, marginTop: 14, maxWidth: 920}}>
          Compare to alternatives: most reseller scouts cost $19–$29/mo and lock the comp data behind a black-box score.
        </p>
      </div>

      {/* Built for */}
      <div style={{padding:'72px 40px 90px'}}>
        <h2 style={{fontSize: 32, marginBottom: 32}}>Built for people who already know what to buy.</h2>
        <div style={{display:'grid', gridTemplateColumns:'1fr 1fr 1fr', gap: 24}}>
          <Persona tag="Resellers" body="You flip on eBay. Every listing missed is margin lost. Bullseye is your overnight scout."/>
          <Persona tag="Vintage hunters" body="Cameras, hi-fi, denim, lighting. You know fair value cold — Bullseye finds the under-priced ones."/>
          <Persona tag="Electronics flippers" body="PS5s, MacBooks, monitors. Speed wins. Five-minute polling beats refreshing every coffee break."/>
        </div>
      </div>

      {/* Footer */}
      <div style={{padding:'28px 40px', borderTop:'1px solid var(--border)', background:'var(--bg-sunk)', display:'flex', justifyContent:'space-between', alignItems:'center', fontSize: 12, color:'var(--muted)'}}>
        <Logo.B size={18}/>
        <span>© 2026 Bullseye · <a href="#" className="muted">Privacy</a> · <a href="#" className="muted">Terms</a> · <a href="#" className="muted">Contact</a></span>
      </div>
    </div>
  );
}

function Stat2({v, k}) {
  return (
    <div>
      <div style={{fontFamily:'var(--serif)', fontSize: 28, color:'var(--accent)', letterSpacing:'-0.01em'}}>{v}</div>
      <div className="muted" style={{fontSize: 12, marginTop: 4}}>{k}</div>
    </div>
  );
}

function Plan({tag, price, sub, features, highlighted, cta}) {
  return (
    <div className="card" style={{
      padding: 28, position:'relative',
      borderColor: highlighted ? '#f1d9c5' : 'var(--border)',
      background: highlighted ? 'var(--bg-elev)' : 'var(--bg-elev)',
      boxShadow: highlighted ? 'var(--shadow-2)' : 'var(--shadow-1)',
    }}>
      {highlighted && (
        <span style={{
          position:'absolute', top: 14, right: 14,
          fontSize: 10, fontFamily:'var(--mono)', textTransform:'uppercase', letterSpacing:'0.08em',
          padding:'3px 8px', borderRadius: 99, background:'var(--accent)', color:'#fff',
        }}>recommended</span>
      )}
      <div className="kicker" style={{color: highlighted ? 'var(--accent)' : 'var(--muted)'}}>{tag}</div>
      <div style={{display:'flex', alignItems:'baseline', gap: 8, marginTop: 10}}>
        <span style={{fontFamily:'var(--serif)', fontSize: 44, letterSpacing:'-0.02em'}}>{price}</span>
        <span className="muted" style={{fontSize: 13}}>{sub}</span>
      </div>
      <button className={`btn ${highlighted ? 'btn-primary' : ''} btn-lg`} style={{width:'100%', marginTop: 18}}>{cta}</button>
      <ul style={{listStyle:'none', padding: 0, margin:'22px 0 0', display:'flex', flexDirection:'column', gap: 10}}>
        {features.map(([f, ok = true], i) => (
          <li key={i} style={{display:'flex', gap: 10, alignItems:'flex-start', fontSize: 13, color: f ? 'var(--fg)' : 'var(--muted-2)'}}>
            <span style={{color: ok && f ? 'var(--accent-2)' : 'var(--muted-2)', marginTop: 1}}>{ok && f ? '✓' : '—'}</span>
            <span>{f || '—'}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Step({n, title, body}) {
  return (
    <div>
      <div style={{fontFamily:'var(--mono)', fontSize: 11, color:'var(--accent)', letterSpacing:'0.08em'}}>{n}</div>
      <h3 style={{fontSize: 22, marginTop: 8}}>{title}</h3>
      <p className="muted" style={{fontSize: 14, lineHeight: 1.6, marginTop: 8}}>{body}</p>
    </div>
  );
}

function Persona({tag, body}) {
  return (
    <div className="card" style={{padding: 22}}>
      <div className="kicker" style={{marginBottom: 8, color:'var(--accent)'}}>{tag}</div>
      <p style={{fontSize: 14, lineHeight: 1.55, color:'var(--fg)', margin: 0}}>{body}</p>
    </div>
  );
}

function HeroDemo() {
  const rows = [
    { t:'Canon AE-1 Program 50/1.8', p: 380, f: 540, s: 78, when:'just now', tone:'good' },
    { t:'Vitamix 5200 (refurb)',      p: 195, f: 260, s: 64, when:'2m',       tone:'ok'   },
    { t:'LG 27" UltraFine 4K',        p: 410, f: 460, s: 52, when:'7m',       tone:'ok'   },
  ];
  return (
    <div className="card" style={{padding: 0, overflow:'hidden', boxShadow:'var(--shadow-2)'}}>
      <div style={{padding:'12px 16px', borderBottom:'1px solid var(--border)', display:'flex', alignItems:'center', justifyContent:'space-between'}}>
        <div style={{display:'flex', alignItems:'center', gap: 8}}>
          <span className="dot dot-live"/>
          <span className="mono" style={{fontSize: 11, color:'var(--muted)'}}>appraisal feed · live</span>
        </div>
        <span className="mono" style={{fontSize: 11, color:'var(--muted)'}}>23 polls · 4 alerts · 2h</span>
      </div>
      {rows.map((r, i) => (
        <div key={i} style={{
          display:'grid', gridTemplateColumns:'56px 1fr auto', gap: 14, alignItems:'center',
          padding:'14px 16px', borderBottom: i < rows.length-1 ? '1px solid var(--border)' : 'none',
          background: i === 0 ? 'var(--accent-soft)' : 'var(--bg-elev)'
        }}>
          <div className="ph" style={{height: 56, borderRadius: 6, fontSize: 9}}>photo</div>
          <div style={{minWidth: 0}}>
            <div style={{fontFamily:'var(--serif)', fontSize: 15, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap'}}>{r.t}</div>
            <div style={{fontSize: 12, color:'var(--muted)', marginTop: 4, fontFamily:'var(--mono)'}}>
              <span style={{color:'var(--accent)'}}>${r.p}</span> · fair ${r.f} · {r.when}
            </div>
          </div>
          <span className={`score ${r.tone === 'good' ? 'score-good' : 'score-ok'}`}>
            <span className="num">{r.s}</span><span className="pct">/100</span>
          </span>
        </div>
      ))}
      <div style={{padding:'10px 16px', background:'var(--bg-sunk)', fontSize: 11, color:'var(--muted)', display:'flex', justifyContent:'space-between'}}>
        <span className="mono">next poll in 2:14</span>
        <span className="mono">↗ 23 active watches</span>
      </div>
    </div>
  );
}

window.LandingV2 = LandingV2;
