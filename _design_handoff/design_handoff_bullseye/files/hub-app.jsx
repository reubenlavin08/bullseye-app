// Bullseye — Wispr-Hub-style desktop app shell
// Left sidebar (nav) + main area with welcome + content blocks.

const HUB_NAV = [
  {id:'home',     label:'Home',         icon:'⌂'},
  {id:'feed',     label:'Appraisal feed', icon:'≡', count: 5},
  {id:'watches',  label:'Watches',      icon:'◎', count: 23},
  {id:'comps',    label:'Comp library', icon:'☰'},
  {id:'rules',    label:'Alert rules',  icon:'⚑'},
  {id:'insights', label:'Insights',     icon:'◈'},
];
const HUB_FOOT = [
  {id:'settings', label:'Settings', icon:'⚙'},
  {id:'help',     label:'Help',     icon:'?'},
];

function HubApp() {
  const [active, setActive] = React.useState('home');
  return (
    <div className="bs-root" style={{width: 1180, height: 800, display:'grid', gridTemplateColumns:'232px 1fr', background:'var(--bg)'}}>
      <HubSidebar active={active} setActive={setActive}/>
      <div style={{overflowY:'auto'}}>
        <HubTopbar/>
        {active === 'home' && <HubHome/>}
        {active === 'feed' && <HubFeed/>}
      </div>
    </div>
  );
}

function HubSidebar({active, setActive}) {
  return (
    <aside style={{
      borderRight:'1px solid var(--border)', background:'var(--bg)',
      display:'flex', flexDirection:'column', padding:'18px 12px',
    }}>
      <div style={{padding:'4px 8px 18px'}}>
        <Logo.B size={22}/>
      </div>
      <div style={{display:'flex', flexDirection:'column', gap: 1}}>
        {HUB_NAV.map(n => <HubNavItem key={n.id} item={n} active={active === n.id} onClick={() => setActive(n.id)}/>)}
      </div>
      <div style={{flex: 1}}/>
      <div style={{display:'flex', flexDirection:'column', gap: 1, paddingTop: 12, borderTop:'1px solid var(--border)'}}>
        {HUB_FOOT.map(n => <HubNavItem key={n.id} item={n} active={false} onClick={() => {}}/>)}
      </div>
      <div style={{
        marginTop: 12, padding: 12, borderRadius: 8, background:'var(--accent-soft)', border:'1px solid #f1d9c5',
      }}>
        <div style={{fontFamily:'var(--serif)', fontSize: 14, color:'var(--accent)'}}>Pro trial</div>
        <div className="muted" style={{fontSize: 11, marginTop: 2}}>5 days left · $9.99/mo</div>
        <button className="btn btn-primary btn-sm" style={{width:'100%', marginTop: 10}}>Upgrade</button>
      </div>
    </aside>
  );
}

function HubNavItem({item, active, onClick}) {
  return (
    <button onClick={onClick} style={{
      display:'flex', alignItems:'center', gap: 10, padding:'8px 10px',
      border:'none', background: active ? 'var(--bg-elev)' : 'transparent',
      borderRadius: 6, cursor:'pointer', textAlign:'left',
      color: active ? 'var(--fg)' : 'var(--muted)',
      fontSize: 13, fontFamily:'var(--sans)', fontWeight: active ? 500 : 400,
      boxShadow: active ? 'var(--shadow-1)' : 'none',
    }}>
      <span style={{width: 16, textAlign:'center', fontSize: 13, color: active ? 'var(--accent)' : 'var(--muted-2)'}}>{item.icon}</span>
      <span style={{flex: 1}}>{item.label}</span>
      {item.count != null && (
        <span style={{
          fontFamily:'var(--mono)', fontSize: 10, color:'var(--muted)',
          background:'var(--bg-sunk)', borderRadius: 3, padding:'1px 5px',
        }}>{item.count}</span>
      )}
    </button>
  );
}

function HubTopbar() {
  return (
    <div style={{
      height: 52, borderBottom:'1px solid var(--border)',
      display:'flex', alignItems:'center', gap: 14, padding:'0 28px', background:'var(--bg)',
    }}>
      <input className="input" placeholder="Test a Marketplace URL or freetext (e.g. canon ae-1 $380)" style={{flex: 1, maxWidth: 560}}/>
      <span style={{flex: 1}}/>
      <span className="chip"><span className="dot dot-live"/> next poll · 2:14</span>
      <button className="btn btn-sm">+ New watch</button>
      <span style={{
        width: 28, height: 28, borderRadius:'50%', background:'var(--accent)', color:'#fff',
        display:'flex', alignItems:'center', justifyContent:'center', fontSize: 12, fontFamily:'var(--serif)',
      }}>j</span>
    </div>
  );
}

function HubHome() {
  const hour = 14; // sample
  const greeting = hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening';
  return (
    <div style={{padding:'36px 40px 60px', maxWidth: 1000}}>
      <div className="kicker" style={{marginBottom: 8}}>tuesday · may 5</div>
      <h1 style={{fontSize: 38, letterSpacing:'-0.02em', lineHeight: 1.1}}>{greeting}, Jordan.</h1>
      <p style={{fontSize: 15, color:'var(--muted)', marginTop: 10, maxWidth: 560, lineHeight: 1.55}}>
        Bullseye scanned <span style={{color:'var(--fg)'}}>1,487 listings</span> overnight.
        Five scored above your threshold of 70.
      </p>

      {/* Top score — interactive */}
      <div style={{marginTop: 28}}>
        <div className="kicker" style={{marginBottom: 10}}>top deal · just now</div>
        <ScoreBigInteractive defaultOpen={false}/>
      </div>

      {/* recent activity row */}
      <div style={{marginTop: 36, display:'grid', gridTemplateColumns:'1fr 1fr', gap: 18}}>
        <HubCard title="overnight summary">
          <HubKV k="listings scanned" v="1,487"/>
          <HubKV k="appraised"        v="575"/>
          <HubKV k="alerts sent"      v="5" tone="accent"/>
          <HubKV k="estimated margin found" v="+$1,240" tone="good"/>
        </HubCard>
        <HubCard title="watches working">
          <HubMini q="canon ae-1"      polls={288} alerts={4} top={78}/>
          <HubMini q="vitamix 5200"    polls={288} alerts={2} top={71}/>
          <HubMini q="hasselblad 500"  polls={288} alerts={1} top={71}/>
          <HubMini q="lg ultrafine 4k" polls={288} alerts={1} top={52}/>
        </HubCard>
      </div>

      <div style={{marginTop: 36}}>
        <div style={{display:'flex', alignItems:'baseline', justifyContent:'space-between', marginBottom: 10}}>
          <div className="kicker">today's appraisals</div>
          <a href="#" style={{fontSize: 12, color:'var(--accent)'}}>view all 23 →</a>
        </div>
        <CleanFeed/>
      </div>
    </div>
  );
}

function HubCard({title, children}) {
  return (
    <div className="card" style={{padding: 18}}>
      <div className="kicker" style={{marginBottom: 12}}>{title}</div>
      <div style={{display:'flex', flexDirection:'column', gap: 0}}>{children}</div>
    </div>
  );
}
function HubKV({k, v, tone}) {
  const c = tone === 'accent' ? 'var(--accent)' : tone === 'good' ? 'var(--accent-2)' : 'var(--fg)';
  return (
    <div style={{display:'flex', justifyContent:'space-between', alignItems:'baseline', padding:'8px 0', borderBottom:'1px dashed var(--border)'}}>
      <span style={{fontSize: 13, color:'var(--muted)'}}>{k}</span>
      <span style={{fontFamily:'var(--mono)', fontSize: 14, color: c, fontVariantNumeric:'tabular-nums'}}>{v}</span>
    </div>
  );
}
function HubMini({q, polls, alerts, top}) {
  const tone = top >= 70 ? 'score-good' : top >= 50 ? 'score-ok' : 'score-meh';
  return (
    <div style={{display:'grid', gridTemplateColumns:'1fr auto auto', gap: 12, alignItems:'center', padding:'8px 0', borderBottom:'1px dashed var(--border)'}}>
      <span style={{fontFamily:'var(--mono)', fontSize: 12, color:'var(--fg)'}}>{q}</span>
      <span className="mono" style={{fontSize: 11, color: alerts > 0 ? 'var(--accent)' : 'var(--muted)'}}>{alerts} alerts</span>
      <span className={`score ${tone}`} style={{padding:'2px 8px'}}><span className="num" style={{fontSize: 13}}>{top}</span></span>
    </div>
  );
}

function CleanFeed() {
  const rows = [
    {t:'Canon AE-1 Program 50/1.8',  p:380, f:540, s:78, when:'12m', tone:'good'},
    {t:'Vitamix 5200 (refurb)',       p:195, f:260, s:64, when:'18m', tone:'ok'},
    {t:'Hasselblad 500C/M w/ 80mm',   p:1450,f:1850,s:71, when:'24m', tone:'good'},
    {t:'LG 27UK850 4K monitor',       p:410, f:460, s:52, when:'34m', tone:'ok'},
  ];
  const cls = {good:'score-good', ok:'score-ok', meh:'score-meh'};
  return (
    <div className="card" style={{padding: 0}}>
      {rows.map((r, i) => (
        <div key={i} style={{
          display:'grid', gridTemplateColumns:'48px 1fr auto auto', gap: 16, alignItems:'center',
          padding:'14px 18px', borderBottom: i < rows.length-1 ? '1px solid var(--border)' : 'none',
        }}>
          <div className="ph" style={{height: 40, width: 40, fontSize: 0}}>·</div>
          <div>
            <div style={{fontFamily:'var(--serif)', fontSize: 15}}>{r.t}</div>
            <div className="mono" style={{fontSize: 11, color:'var(--muted)', marginTop: 3}}>
              <span style={{color:'var(--accent)'}}>${r.p}</span> · fair ${r.f} · {r.when} ago
            </div>
          </div>
          <span className={`score ${cls[r.tone]}`}><span className="num">{r.s}</span></span>
          <button className="btn btn-sm">Open</button>
        </div>
      ))}
    </div>
  );
}

// Cleaner Event Tail (for dashboard)
function EventTailClean() {
  const events = [
    {t:'14:08:21', kind:'poll',   q:'canon ae-1',     meta:'14 listings · 3 new',  status:'ok'},
    {t:'14:08:23', kind:'score',  q:'AE-1 Program',   meta:'78 · fair $540 ±$35',  status:'hit'},
    {t:'14:08:23', kind:'alert',  q:'jordan@…',       meta:'email queued',         status:'hit'},
    {t:'14:08:24', kind:'comps',  q:'AE-1 Program',   meta:'cache hit · ttl 4h',   status:'ok'},
    {t:'14:08:25', kind:'verify', q:'listing 29104',  meta:'timeout · retry 1/3',  status:'warn'},
    {t:'14:08:27', kind:'verify', q:'listing 29104',  meta:'authentic · 0.91',     status:'ok'},
    {t:'14:08:30', kind:'poll',   q:'vitamix 5200',   meta:'8 listings · 1 new',   status:'ok'},
  ];
  const kindColor = {poll:'var(--muted-2)', score:'var(--fg)', alert:'var(--accent)', comps:'var(--muted-2)', verify:'var(--accent-2)'};
  const statusDot = {ok:'#a3c08a', warn:'var(--warn)', hit:'var(--accent)', err:'var(--accent)'};
  return (
    <div className="card" style={{overflow:'hidden'}}>
      <div style={{padding:'12px 16px', borderBottom:'1px solid var(--border)', display:'flex', alignItems:'center', justifyContent:'space-between'}}>
        <div className="kicker">event tail · 2s refresh</div>
        <span className="chip"><span className="dot dot-live"/>streaming</span>
      </div>
      <div>
        {events.map((e, i) => (
          <div key={i} style={{
            display:'grid', gridTemplateColumns:'12px 70px 70px 1fr auto', gap: 14, alignItems:'center',
            padding:'10px 16px', borderBottom: i < events.length-1 ? '1px solid var(--border)' : 'none',
            background: e.status === 'hit' ? 'var(--accent-soft)' : 'transparent',
          }}>
            <span className="dot" style={{background: statusDot[e.status]}}/>
            <span className="mono" style={{fontSize: 11, color:'var(--muted)'}}>{e.t}</span>
            <span style={{
              fontSize: 10, fontFamily:'var(--mono)', textTransform:'uppercase', letterSpacing:'0.06em',
              color: kindColor[e.kind], fontWeight: 500,
            }}>{e.kind}</span>
            <span style={{fontSize: 13, color:'var(--fg)'}}>
              {e.q} <span className="muted" style={{fontSize: 12}}>· {e.meta}</span>
            </span>
            <span/>
          </div>
        ))}
      </div>
    </div>
  );
}

window.HubApp = HubApp;
window.EventTailClean = EventTailClean;
