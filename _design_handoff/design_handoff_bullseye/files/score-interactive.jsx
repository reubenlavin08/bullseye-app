// Bullseye — ScoreBig with clickable breakdown popover
// Replaces the previous ScoreBig. Click the score number to reveal the
// breakdown inline (no drawer): math, comp distribution, and listing comps.

const SAMPLE = {
  asking: 380, fair: 540, confidence: 35, score: 78,
  median: 565, iqr: [495, 615], comps: 23,
  title: 'Canon AE-1 Program w/ 50mm f/1.8',
  location: 'Oakland · 4.2 mi', listed: '12 min ago',
  reasons: [
    {tag:'+18', tone:'good',  label:'Below trimmed median',     body:'Asking $380 vs comp median $565 (33% under).'},
    {tag:'+12', tone:'good',  label:'Tight comp distribution',   body:'IQR ±$60 — pricing signal is strong.'},
    {tag:'+8',  tone:'good',  label:'Includes 50mm f/1.8',       body:'Photos confirm; comps with lens average +$110.'},
    {tag:'−6',  tone:'bad',   label:'Condition flags ambiguous', body:"Seller wrote 'works' but no test roll."},
    {tag:'−4',  tone:'bad',   label:'Older listing format',      body:'Light meter description omitted.'},
  ],
  compRows: [
    {t:'AE-1 Program (CLA done)',     p:695, when:'2d'},
    {t:'AE-1P + 50/1.8',              p:625, when:'12d'},
    {t:'AE-1P body + lens, working',  p:590, when:'21d'},
    {t:'Canon AE-1 Program kit',      p:565, when:'4d'},
    {t:'AE-1 Program 50mm tested',    p:540, when:'31d'},
    {t:'AE-1P + 50/1.8 + strap',      p:480, when:'18d'},
    {t:'Canon film SLR AE-1P + lens', p:510, when:'8d'},
    {t:'Canon AE-1 Program lot',      p:455, when:'40d'},
  ],
};

function ScoreBigInteractive({ data = SAMPLE, defaultOpen = false, embedded = false }) {
  const [open, setOpen] = React.useState(defaultOpen);
  const [tab, setTab] = React.useState('math'); // 'math' | 'comps'
  const tone = data.score >= 70 ? 'var(--accent-2)' : data.score >= 50 ? 'var(--warn)' : 'var(--muted)';

  return (
    <div className={embedded ? '' : 'card'} style={{padding: embedded ? 0 : 24, background:'var(--bg-elev)'}}>
      <div style={{display:'flex', alignItems:'flex-start', gap: 22}}>
        <button
          onClick={() => setOpen(o => !o)}
          aria-expanded={open}
          style={{
            border:'none', background:'transparent', padding: 0, cursor:'pointer', textAlign:'left',
            minWidth: 110, paddingRight: 22, borderRight:'1px solid var(--border)',
          }}>
          <div className="kicker" style={{marginBottom: 4}}>score</div>
          <div style={{fontFamily:'var(--serif)', fontSize: 60, lineHeight: 1, color: tone, fontVariantNumeric:'tabular-nums', letterSpacing:'-0.02em'}}>
            {data.score}
          </div>
          <div style={{display:'flex', alignItems:'center', gap: 6, marginTop: 8}}>
            <span className="mono" style={{fontSize: 11, color:'var(--muted)'}}>of 100</span>
            <span style={{
              fontSize: 10, fontFamily:'var(--mono)', color:'var(--accent)',
              padding:'1px 5px', borderRadius: 3, background:'var(--accent-soft)',
              border:'1px solid #f1d9c5',
            }}>{open ? 'hide' : 'why?'}</span>
          </div>
        </button>
        <div style={{flex: 1, minWidth: 0}}>
          <div style={{fontFamily:'var(--serif)', fontSize: 18, lineHeight: 1.3}}>{data.title}</div>
          <div className="muted" style={{fontSize: 12, marginTop: 4}}>{data.location} · {data.listed}</div>
          <div style={{display:'flex', gap: 32, marginTop: 18}}>
            <Stat label="asking" value={`$${data.asking}`} accent />
            <Stat label="fair value" value={`$${data.fair}`} sub={`±$${data.confidence}`} />
            <Stat label="vs. median" value={`−$${data.median - data.asking}`} sub={`${data.comps} comps`} good />
          </div>
        </div>
      </div>

      {open && (
        <div style={{marginTop: 22, paddingTop: 18, borderTop:'1px solid var(--border)', animation:'bs-reveal .18s ease'}}>
          <style>{`@keyframes bs-reveal { from {opacity:0; transform: translateY(-4px)} to {opacity:1; transform: none} }`}</style>
          {/* tabs */}
          <div style={{display:'flex', gap: 0, marginBottom: 16, borderBottom:'1px solid var(--border)'}}>
            {[['math','How we got 78'],['comps',`${data.comps} comps`]].map(([k,l]) => (
              <button key={k} onClick={() => setTab(k)} style={{
                background:'transparent', border:'none', padding:'8px 14px 10px',
                fontSize: 13, fontFamily:'var(--sans)',
                color: tab === k ? 'var(--fg)' : 'var(--muted)',
                borderBottom: tab === k ? '2px solid var(--accent)' : '2px solid transparent',
                cursor:'pointer', marginBottom: -1,
              }}>{l}</button>
            ))}
          </div>

          {tab === 'math' && <BreakdownMath data={data}/>}
          {tab === 'comps' && <BreakdownComps data={data}/>}
        </div>
      )}
    </div>
  );
}

function Stat({label, value, sub, accent, good}) {
  const c = accent ? 'var(--accent)' : good ? 'var(--accent-2)' : 'var(--fg)';
  return (
    <div>
      <div className="kicker" style={{fontSize: 10, marginBottom: 2}}>{label}</div>
      <div style={{fontFamily:'var(--serif)', fontSize: 20, color: c, fontVariantNumeric:'tabular-nums'}}>{value}</div>
      {sub && <div className="muted" style={{fontSize: 11, marginTop: 1}}>{sub}</div>}
    </div>
  );
}

function BreakdownMath({data}) {
  const colors = {
    good:  {bg:'var(--accent-2-soft)', fg:'var(--accent-2)', bd:'#c4d2b6'},
    bad:   {bg:'var(--accent-soft)',   fg:'var(--accent)',   bd:'#f1d9c5'},
  };
  return (
    <div>
      {/* distribution rail */}
      <DistRail data={data}/>
      {/* reason rows */}
      <div style={{marginTop: 18, display:'flex', flexDirection:'column'}}>
        {data.reasons.map((r, i) => {
          const c = colors[r.tone];
          return (
            <div key={i} style={{display:'flex', gap: 12, padding:'10px 0', borderBottom: i < data.reasons.length-1 ? '1px solid var(--border)' : 'none'}}>
              <span style={{
                fontFamily:'var(--mono)', fontSize: 11, fontWeight: 500,
                background: c.bg, color: c.fg, border: `1px solid ${c.bd}`,
                padding:'3px 7px', borderRadius: 4, minWidth: 42, textAlign:'center', height:'fit-content',
              }}>{r.tag}</span>
              <div>
                <div style={{fontSize: 13, fontWeight: 500}}>{r.label}</div>
                <div className="muted" style={{fontSize: 12, marginTop: 2, lineHeight: 1.45}}>{r.body}</div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function DistRail({data}) {
  const min = 250, max = 750;
  const x = v => ((v - min) / (max - min)) * 100;
  return (
    <div style={{position:'relative', height: 60, marginBottom: 4}}>
      <div style={{position:'absolute', left: 0, right: 0, top: 30, height: 1, background:'var(--border)'}}/>
      <div style={{
        position:'absolute', top: 24, height: 13,
        left: `${x(data.iqr[0])}%`, width: `${x(data.iqr[1]) - x(data.iqr[0])}%`,
        background:'var(--accent-2-soft)', border:'1px solid #c4d2b6', borderRadius: 3,
      }}/>
      <div style={{position:'absolute', top: 20, height: 21, left: `${x(data.median)}%`, width: 2, background:'var(--accent-2)'}}/>
      <div style={{position:'absolute', top: 0, left: `${x(data.median)}%`, transform:'translateX(-50%)', fontSize: 10, color:'var(--accent-2)', fontFamily:'var(--mono)'}}>median</div>
      <div style={{position:'absolute', top: 16, height: 29, left: `${x(data.asking)}%`, width: 2, background:'var(--accent)'}}/>
      <div style={{position:'absolute', bottom: 0, left: `${x(data.asking)}%`, transform:'translateX(-50%)', fontSize: 11, color:'var(--accent)', fontFamily:'var(--serif)', fontWeight: 500, fontVariantNumeric:'tabular-nums'}}>${data.asking}</div>
      <div style={{position:'absolute', top: 40, left: 0, fontSize: 10, color:'var(--muted)', fontFamily:'var(--mono)'}}>${min}</div>
      <div style={{position:'absolute', top: 40, right: 0, fontSize: 10, color:'var(--muted)', fontFamily:'var(--mono)'}}>${max}</div>
    </div>
  );
}

function BreakdownComps({data}) {
  const max = Math.max(...data.compRows.map(c => c.p));
  return (
    <div>
      <div className="muted" style={{fontSize: 12, marginBottom: 12}}>
        eBay sold listings, last 90 days, same model. Trimmed to remove top/bottom 5%.
      </div>
      {data.compRows.map((c, i) => {
        const w = (c.p / max) * 100;
        return (
          <a key={i} href="#" style={{display:'block', padding:'10px 0', borderTop:'1px solid var(--border)', textDecoration:'none'}}>
            <div style={{display:'flex', justifyContent:'space-between', gap: 10, marginBottom: 6}}>
              <span style={{fontSize: 13, color:'var(--fg)'}}>{c.t}</span>
              <span style={{fontFamily:'var(--mono)', fontSize: 12, fontVariantNumeric:'tabular-nums'}}>${c.p}</span>
            </div>
            <div style={{display:'flex', alignItems:'center', gap: 10}}>
              <div style={{flex: 1, height: 4, background:'var(--bg-sunk)', borderRadius: 99, overflow:'hidden'}}>
                <div style={{height:'100%', width:`${w}%`, background:'var(--accent-2)'}}/>
              </div>
              <span style={{fontSize: 11, color:'var(--muted)', fontFamily:'var(--mono)'}}>{c.when} ago</span>
            </div>
          </a>
        );
      })}
    </div>
  );
}

window.ScoreBigInteractive = ScoreBigInteractive;
window.SAMPLE_SCORE = SAMPLE;
