import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.colors as pc
import re
from collections import defaultdict

st.set_page_config(
    page_title="Cricket Analytics Dashboard",
    page_icon="🏏",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    .main .block-container { padding-top: 1.5rem; }
    .metric-card {
        background: #1e293b; border-radius: 12px;
        padding: 1rem 1.2rem; text-align: center; border: 1px solid #334155;
    }
    .metric-card .label { font-size: 0.72rem; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em; }
    .metric-card .value { font-size: 1.5rem; font-weight: 700; color: #f1f5f9; margin-top: 2px; }
    .metric-card .sub   { font-size: 0.72rem; color: #64748b; margin-top: 2px; }
    .section-header {
        font-size: 1.05rem; font-weight: 600; color: #e2e8f0;
        border-left: 3px solid #3b82f6; padding-left: 10px; margin: 1.2rem 0 0.7rem;
    }
    div[data-testid="stFileUploader"] { border: 2px dashed #334155; border-radius: 10px; padding: 0.5rem; }
    .file-badge {
        display: inline-block; background: #1e3a5f; border: 1px solid #2563eb;
        border-radius: 6px; padding: 2px 8px; font-size: 0.72rem; color: #93c5fd; margin: 2px;
    }
</style>
""", unsafe_allow_html=True)

SCATTER_TMPL = "plotly_dark"

# ════════════════════════════════════════════════════════════════════════════════
# PARSER
# ════════════════════════════════════════════════════════════════════════════════

def detect_match_type(filename):
    fn = filename.lower()
    if any(k in fn for k in ["20 league","t20","twenty20","20over","20_league","_20_"," 20 "]):
        return "T20"
    if any(k in fn for k in ["odi","one day","oneday","list a"]):
        return "ODI"
    if any(k in fn for k in ["test","wtc","1st test","2nd test","3rd test","4th test","5th test"]):
        return "Test"
    return "Unknown"

def extract_season(filename):
    m = re.search(r'(20\d\d)', filename)
    return m.group(1) if m else "Unknown"

def parse_batting_line(line):
    line = line.strip()
    if not line or line.startswith('-') or line.startswith('*'):
        return None
    if any(line.startswith(k) for k in ['Extras','TOTAL','Fall','----']):
        return None
    m = re.match(
        r'^([A-Z][A-Za-z\s\'\-\.]+?)\s+'
        r'(c\s+&\s+b\s+\S+|c\s+\S+\s+b\s+\S+|lbw\s+b\s+\S+|'
        r'st\s+\S+\s+b\s+\S+|b\s+\S+|run\s+out(?:\s+\([^)]+\))?|'
        r'hit\s+wicket|not\s+out|retired\s+hurt|retired|absent)'
        r'\s+(\d+)\s+(\d+)\s+(\d+|-)\s+(\d+|-)$',
        line, re.IGNORECASE
    )
    if not m:
        return None
    name      = m.group(1).strip()
    dismissal = m.group(2).strip().lower()
    runs  = int(m.group(3))
    balls = int(m.group(4))
    fours = int(m.group(5)) if m.group(5) != '-' else 0
    sixes = int(m.group(6)) if m.group(6) != '-' else 0
    is_out = not any(k in dismissal for k in ['not out','retired','absent'])
    how_out = 'not out'
    if is_out:
        if 'c &' in dismissal or dismissal.startswith('c '):
            how_out = 'caught'
        elif dismissal.startswith('lbw'):
            how_out = 'lbw'
        elif dismissal.startswith('b '):
            how_out = 'bowled'
        elif dismissal.startswith('st '):
            how_out = 'stumped'
        elif 'run out' in dismissal:
            how_out = 'run out'
        else:
            how_out = 'other'
    return dict(name=name, runs=runs, balls=balls, fours=fours, sixes=sixes,
                out=is_out, how_out=how_out, position=0)  # position overwritten by caller

def parse_bowling_line(line):
    line = line.strip()
    if not line or line.startswith('-') or line.startswith('*') or 'Fall' in line:
        return None
    m = re.match(
        r'^([A-Z][A-Za-z\s\'\-\.]+?)\s+'
        r'(\d+(?:\.\d+)?)\s+(\d+)\s+(\d+)\s+(\d+)\s+([\d.]+)$',
        line
    )
    if not m:
        return None
    return dict(name=m.group(1).strip(), overs=float(m.group(2)),
                maidens=int(m.group(3)), runs=int(m.group(4)),
                wickets=int(m.group(5)), economy=float(m.group(6)))

def parse_scorecard(text, filename, match_type, season):
    lines = text.split('\n')
    innings_list = []
    current_team = None
    current_inn_num = 0
    in_bat = False
    in_bowl = False
    bat_rows, bowl_rows = [], []
    bat_order = 0
    total_runs = total_wkts = 0
    total_overs = ""

    def flush():
        if current_team and (bat_rows or bowl_rows):
            innings_list.append(dict(
                team=current_team, innings_num=current_inn_num,
                batting=list(bat_rows), bowling=list(bowl_rows),
                total_runs=total_runs, total_wickets=total_wkts,
                total_overs=total_overs, match_type=match_type,
                season=season, filename=filename,
            ))

    for raw in lines:
        line = raw.strip()
        inn_h = re.match(r'^(.+?)\s+-\s+(\d+(?:st|nd|rd|th))\s+Innings', line, re.IGNORECASE)
        if inn_h:
            flush()
            current_team = inn_h.group(1).strip()
            current_inn_num = {'1st':1,'2nd':2,'3rd':3,'4th':4}.get(inn_h.group(2).lower(), 1)
            in_bat, in_bowl = True, False
            bat_rows.clear(); bowl_rows.clear()
            bat_order = total_runs = total_wkts = 0; total_overs = ""
            continue
        if line.startswith('TOTAL'):
            tm = re.search(r'(\d+)\s*$', line)
            if tm: total_runs = int(tm.group(1))
            wm = re.search(r'\((\d+)\s+wkts?', line)
            am = re.search(r'all out', line, re.IGNORECASE)
            om = re.search(r'(\d+(?:\.\d+)?)\s+overs', line)
            total_wkts = int(wm.group(1)) if wm else (10 if am else 10)
            total_overs = om.group(1) if om else ""
            in_bat, in_bowl = False, False
            continue
        if line.startswith('Extras'):
            in_bat = False
            continue
        if re.match(r'^\s*O\s+[MD]\s+R\s+W', line):
            in_bat, in_bowl = False, True
            continue
        if 'Fall of Wickets' in line or re.match(r'^\d+-\d+\s+\d+-\d+', line):
            in_bowl = False
            continue
        if re.match(r'^[-*]{10,}', line):
            continue
        if in_bat:
            p = parse_batting_line(line)
            if p:
                bat_order += 1
                p['position'] = bat_order
                bat_rows.append(p)
        elif in_bowl:
            p = parse_bowling_line(line)
            if p:
                bowl_rows.append(p)
    flush()
    return innings_list

# ════════════════════════════════════════════════════════════════════════════════
# STAT BUILDERS  — key = (player_name, team, match_type)
# ════════════════════════════════════════════════════════════════════════════════

def build_player_batting(all_innings):
    acc = defaultdict(lambda: dict(
        innings=0, runs=0, balls=0, fours=0, sixes=0,
        outs=0, not_outs=0, fifties=0, hundreds=0, high_score=0,
        positions=[], seasons=set(),
        caught=0, bowled=0, lbw=0, stumped=0, run_out=0, other_out=0,
    ))
    for inn in all_innings:
        for r in inn['batting']:
            key = (r['name'], inn['team'], inn['match_type'])
            p = acc[key]
            p['innings']   += 1
            p['runs']      += r['runs']
            p['balls']     += r['balls']
            p['fours']     += r['fours']
            p['sixes']     += r['sixes']
            p['high_score'] = max(p['high_score'], r['runs'])
            p['positions'].append(r['position'])
            p['seasons'].add(inn['season'])
            if r['out']:
                p['outs'] += 1
                h = r.get('how_out', 'other')  # defensive for stale cached data
                if h == 'caught':    p['caught']   += 1
                elif h == 'bowled':  p['bowled']   += 1
                elif h == 'lbw':     p['lbw']      += 1
                elif h == 'stumped': p['stumped']  += 1
                elif h == 'run out': p['run_out']  += 1
                else:                p['other_out']+= 1
            else:
                p['not_outs'] += 1
            if r['runs'] >= 100: p['hundreds'] += 1
            elif r['runs'] >= 50: p['fifties']  += 1

    records = []
    for (name, team, mt), p in acc.items():
        avg_pos = round(sum(p['positions'])/len(p['positions']), 1) if p['positions'] else 0
        sr   = round(p['runs']/p['balls']*100, 2) if p['balls'] > 0 else 0.0
        avg  = round(p['runs']/p['outs'],      2) if p['outs']  > 0 else float(p['runs'])
        denom = p['outs'] if p['outs'] > 0 else 1
        bnd_r = p['fours']*4 + p['sixes']*6
        records.append(dict(
            Player=name, Team=team, Match_Type=mt,
            Innings=p['innings'], Runs=p['runs'], Balls=p['balls'],
            Fours=p['fours'], Sixes=p['sixes'],
            HighScore=p['high_score'], Outs=p['outs'], NotOuts=p['not_outs'],
            Fifties=p['fifties'], Hundreds=p['hundreds'],
            AvgPosition=avg_pos, Positions=p['positions'],
            Seasons=sorted(p['seasons']),
            StrikeRate=sr, Average=avg,
            BoundaryPct=round(bnd_r/p['runs']*100, 1) if p['runs'] > 0 else 0.0,
            SixPct=round(p['sixes']*6/p['runs']*100, 1) if p['runs'] > 0 else 0.0,
            CatchPct=round(p['caught']  /denom*100, 1),
            BowledPct=round(p['bowled'] /denom*100, 1),
            LBWPct=round(p['lbw']       /denom*100, 1),
            StumpedPct=round(p['stumped']/denom*100, 1),
            RunOutPct=round(p['run_out'] /denom*100, 1),
        ))
    return pd.DataFrame(records) if records else pd.DataFrame()

def build_player_bowling(all_innings):
    acc = defaultdict(lambda: dict(innings=0, overs=0.0, maidens=0, runs=0, wickets=0, seasons=set()))
    for inn in all_innings:
        for r in inn['bowling']:
            key = (r['name'], inn['team'], inn['match_type'])
            p = acc[key]
            p['innings']  += 1
            p['overs']    += r['overs']
            p['maidens']  += r['maidens']
            p['runs']     += r['runs']
            p['wickets']  += r['wickets']
            p['seasons'].add(inn['season'])
    records = []
    for (name, team, mt), p in acc.items():
        full = int(p['overs']); part = round((p['overs']-full)*10)
        balls = full*6 + part
        economy  = round(p['runs']/p['overs'],   2) if p['overs']   > 0 else 0.0
        bowl_avg = round(p['runs']/p['wickets'], 2) if p['wickets'] > 0 else None
        bowl_sr  = round(balls/p['wickets'],     2) if p['wickets'] > 0 else None
        records.append(dict(
            Player=name, Team=team, Match_Type=mt,
            Innings=p['innings'], Overs=round(p['overs'],1),
            Maidens=p['maidens'], Runs=p['runs'], Wickets=p['wickets'],
            Seasons=sorted(p['seasons']),
            Economy=economy, BowlingAvg=bowl_avg, BowlingSR=bowl_sr,
        ))
    return pd.DataFrame(records) if records else pd.DataFrame()

def build_team_batting(all_innings):
    rows = []
    for inn in all_innings:
        runs  = inn['total_runs']
        fours = sum(r['fours'] for r in inn['batting'])
        sixes = sum(r['sixes'] for r in inn['batting'])
        balls = sum(r['balls'] for r in inn['batting'])
        bnd_r = fours*4 + sixes*6
        rows.append(dict(
            Team=inn['team'], Match_Type=inn['match_type'],
            Season=inn['season'], Innings=inn['innings_num'],
            Runs=runs, Wickets=inn['total_wickets'],
            Fours=fours, Sixes=sixes,
            BoundaryPct=round(bnd_r/runs*100, 1) if runs > 0 else 0,
            SixPct=round(sixes*6/runs*100, 1) if runs > 0 else 0,
            TeamSR=round(runs/balls*100, 2) if balls > 0 else 0,
        ))
    return pd.DataFrame(rows) if rows else pd.DataFrame()

def build_team_bowling(all_innings):
    rows = []
    for inn in all_innings:
        wkts = sum(r['wickets'] for r in inn['bowling'])
        runs = sum(r['runs']    for r in inn['bowling'])
        ovrs = sum(r['overs']   for r in inn['bowling'])
        rows.append(dict(
            Team=inn['team'], Match_Type=inn['match_type'],
            Season=inn['season'], Innings=inn['innings_num'],
            Wickets=wkts, RunsConceded=runs,
            Overs=round(ovrs,1), Economy=round(runs/ovrs,2) if ovrs>0 else 0,
        ))
    return pd.DataFrame(rows) if rows else pd.DataFrame()

# ════════════════════════════════════════════════════════════════════════════════
# SESSION STATE
# ════════════════════════════════════════════════════════════════════════════════
if 'all_innings' not in st.session_state: st.session_state.all_innings = []
if 'file_log'    not in st.session_state: st.session_state.file_log    = []

# ════════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ════════════════════════════════════════════════════════════════════════════════
with st.sidebar:
    st.markdown("## 🏏 Cricket Analytics")
    st.markdown("---")
    uploaded_files = st.file_uploader("Upload Scorecard Files (.txt)", type=["txt"],
                                       accept_multiple_files=True)
    if uploaded_files:
        added = 0
        for uf in uploaded_files:
            if any(f['filename'] == uf.name for f in st.session_state.file_log):
                continue
            text    = uf.read().decode("utf-8", errors="ignore")
            mt      = detect_match_type(uf.name)
            season  = extract_season(uf.name)
            innings = parse_scorecard(text, uf.name, mt, season)
            st.session_state.all_innings.extend(innings)
            st.session_state.file_log.append(
                dict(filename=uf.name, type=mt, season=season, innings=len(innings)))
            added += 1
        if added: st.success(f"✅ {added} new file(s) loaded!")

    st.markdown("---")
    if st.session_state.file_log:
        st.markdown("**Loaded files:**")
        for f in st.session_state.file_log:
            c1, c2 = st.columns([4,1])
            with c1:
                st.markdown(f"<div class='file-badge'>{f['type']}</div> `{f['filename'][:24]}`",
                            unsafe_allow_html=True)
            with c2:
                if st.button("✕", key=f"del_{f['filename']}"):
                    st.session_state.all_innings = [
                        i for i in st.session_state.all_innings if i['filename'] != f['filename']]
                    st.session_state.file_log = [
                        x for x in st.session_state.file_log if x['filename'] != f['filename']]
                    st.rerun()
        st.markdown("---")
        if st.button("🗑️ Clear ALL data", use_container_width=True):
            st.session_state.all_innings = []
            st.session_state.file_log   = []
            st.rerun()
    st.caption("Data lives in your session only.")

# ════════════════════════════════════════════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════════════════════════════════════════════

def metric_html(label, value, sub=""):
    return (f"<div class='metric-card'><div class='label'>{label}</div>"
            f"<div class='value'>{value}</div><div class='sub'>{sub}</div></div>")

def section(title):
    st.markdown(f"<div class='section-header'>{title}</div>", unsafe_allow_html=True)

def ops_filter(df, col, op_str, val):
    if not val: return df
    fn = {'<': lambda a,b: a<b, '=': lambda a,b: a==b, '>': lambda a,b: a>b}[op_str]
    sub = df[df[col].notna()]
    return sub[fn(sub[col], val)]

def _hex_to_rgba(hex_str, alpha=0.85):
    """Convert '#rrggbb' or 'rgb(r,g,b)' to rgba string safely."""
    h = hex_str.strip()
    if h.startswith('#'):
        h = h.lstrip('#')
        if len(h) == 3:
            h = ''.join(c*2 for c in h)
        r, g, b = int(h[0:2],16), int(h[2:4],16), int(h[4:6],16)
        return f'rgba({r},{g},{b},{alpha})'
    if h.startswith('rgb'):
        nums = re.findall(r'[\d.]+', h)
        r,g,b = int(float(nums[0])), int(float(nums[1])), int(float(nums[2]))
        return f'rgba({r},{g},{b},{alpha})'
    return f'rgba(99,130,246,{alpha})'

def make_scatter(df, x, y, text_col, color_col, size_col, x_label, y_label,
                 color_scale='RdYlGn', fixed_size=None):
    """Build a Plotly scatter. fixed_size=N gives uniform bubbles (bowling graphs)."""
    if df.empty:
        return go.Figure()
    color_vals = df[color_col].fillna(0).astype(float)
    c_min, c_max = color_vals.min(), color_vals.max()
    colorscale = pc.get_colorscale(color_scale)

    def get_color(val):
        if c_max == c_min: return 'rgba(99,130,246,0.8)'
        t = float(max(0.0, min(1.0, (val - c_min) / (c_max - c_min))))
        sampled = pc.sample_colorscale(colorscale, t)[0]
        # sample_colorscale may return a hex string or an rgb tuple depending on version
        if isinstance(sampled, str):
            return _hex_to_rgba(sampled)
        # it's a tuple of floats (0-1)
        r, g, b = int(sampled[0]*255), int(sampled[1]*255), int(sampled[2]*255)
        return f'rgba({r},{g},{b},0.85)'

    if fixed_size is not None:
        sizes = [fixed_size] * len(df)
    else:
        raw = df[size_col].fillna(1).clip(lower=1).astype(float)
        mn, mx = raw.min(), raw.max()
        # normalise to 14–28 so bubbles never overwhelm or disappear
        sizes = (14 + (raw - mn) / (mx - mn + 1e-9) * 14).tolist()

    fig = go.Figure(go.Scatter(
        x=df[x], y=df[y],
        mode='markers+text',
        text=df[text_col],
        textposition='top center',
        textfont=dict(size=10),
        marker=dict(
            size=sizes,
            color=[get_color(v) for v in color_vals],
            line=dict(width=0.5, color='rgba(255,255,255,0.25)'),
        ),
        customdata=df[[text_col, x, y]].values,
        hovertemplate=(
            "<b>%{customdata[0]}</b><br>"
            f"{x_label}: %{{customdata[1]}}<br>"
            f"{y_label}: %{{customdata[2]}}<extra></extra>"
        ),
    ))
    fig.update_layout(
        xaxis_title=x_label, yaxis_title=y_label,
        template=SCATTER_TMPL, height=370,
        margin=dict(l=10, r=10, t=10, b=10),
        showlegend=False,
    )
    return fig

# ── Filter rows ──────────────────────────────────────────────────────────────

def filter_row_bat(key_prefix, all_teams, all_seasons, max_inn):
    c1,c2,c3,c4,c5,c6 = st.columns([2,2,2,2,2.5,2.5])
    with c1: teams = st.multiselect("Team",     all_teams,          key=f"{key_prefix}_t")
    with c2: pos   = st.multiselect("Position", list(range(1,12)),  key=f"{key_prefix}_p")
    with c3: seas  = st.multiselect("Season",   all_seasons,        key=f"{key_prefix}_s")
    with c4: inns  = st.multiselect("Innings #",list(range(1,max_inn+1)), key=f"{key_prefix}_i")
    with c5:
        a1,a2 = st.columns([1,2])
        with a1: avg_op = st.selectbox("Avg", ['>','<','='], key=f"{key_prefix}_aop")
        with a2: avg_v  = st.number_input("avg", 0.0, step=5.0, label_visibility="collapsed", key=f"{key_prefix}_av")
    with c6:
        s1,s2 = st.columns([1,2])
        with s1: sr_op = st.selectbox("SR", ['>','<','='], key=f"{key_prefix}_sop")
        with s2: sr_v  = st.number_input("sr", 0.0, step=5.0, label_visibility="collapsed", key=f"{key_prefix}_sv")
    return teams, pos, seas, inns, avg_op, (avg_v if avg_v>0 else None), sr_op, (sr_v if sr_v>0 else None)

def filter_row_bowl(key_prefix, all_teams, all_seasons, max_inn):
    c1,c2,c3,c4,c5,c6 = st.columns([2,2,2,2.5,2.5,2.5])
    with c1: teams  = st.multiselect("Team",     all_teams,               key=f"{key_prefix}_t")
    with c2: seas   = st.multiselect("Season",   all_seasons,             key=f"{key_prefix}_s")
    with c3: inns   = st.multiselect("Innings #",list(range(1,max_inn+1)),key=f"{key_prefix}_i")
    with c4:
        a1,a2 = st.columns([1,2])
        with a1: avg_op  = st.selectbox("Avg",  ['>','<','='], key=f"{key_prefix}_aop")
        with a2: avg_v   = st.number_input("avg",0.0,step=5.0, label_visibility="collapsed", key=f"{key_prefix}_av")
    with c5:
        e1,e2 = st.columns([1,2])
        with e1: econ_op = st.selectbox("Econ", ['<','>','='], key=f"{key_prefix}_eop")
        with e2: econ_v  = st.number_input("ec", 0.0,step=0.5, label_visibility="collapsed", key=f"{key_prefix}_ev")
    with c6:
        s1,s2 = st.columns([1,2])
        with s1: sr_op   = st.selectbox("Bowl SR",['<','>','='],key=f"{key_prefix}_sop")
        with s2: sr_v    = st.number_input("sr",  0.0,step=5.0, label_visibility="collapsed", key=f"{key_prefix}_sv")
    return (teams, seas, inns,
            avg_op,  avg_v  if avg_v  > 0 else None,
            econ_op, econ_v if econ_v > 0 else None,
            sr_op,   sr_v   if sr_v   > 0 else None)

# ── Apply filters ────────────────────────────────────────────────────────────

def apply_bat_filters(df, fmt, teams, pos, seas, inns, avg_op, avg_v, sr_op, sr_v):
    if df.empty: return df
    df = df[df['Match_Type'] == fmt].copy()
    if teams: df = df[df['Team'].isin(teams)]
    if seas:  df = df[df['Seasons'].apply(lambda s: any(x in s for x in seas))]
    if inns:  df = df[df['Innings'].isin(inns)]
    if pos:   df = df[df['Positions'].apply(lambda ps: any(p in pos for p in ps))]
    df = ops_filter(df, 'Average',    avg_op, avg_v)
    df = ops_filter(df, 'StrikeRate', sr_op,  sr_v)
    return df

def apply_bowl_filters(df, fmt, teams, seas, inns, avg_op, avg_v, econ_op, econ_v, sr_op, sr_v):
    if df.empty: return df
    df = df[df['Match_Type'] == fmt].copy()
    if teams: df = df[df['Team'].isin(teams)]
    if seas:  df = df[df['Seasons'].apply(lambda s: any(x in s for x in seas))]
    if inns:  df = df[df['Innings'].isin(inns)]
    df = ops_filter(df, 'BowlingAvg', avg_op,  avg_v)
    df = ops_filter(df, 'Economy',    econ_op, econ_v)
    df = ops_filter(df, 'BowlingSR',  sr_op,   sr_v)
    return df

# ════════════════════════════════════════════════════════════════════════════════
# GRAPH DEFINITIONS
# ════════════════════════════════════════════════════════════════════════════════

def bat_graphs(df, fmt):
    g1, g2 = st.columns(2)
    if fmt == "T20":
        with g1:
            st.caption("Strike Rate vs Average — bubble = innings played")
            st.plotly_chart(make_scatter(df, 'Average','StrikeRate','Player',
                'StrikeRate','Innings','Average','Strike Rate','RdYlGn'),
                use_container_width=True)
        with g2:
            st.caption("Six% vs Boundary% — bubble = runs scored")
            st.plotly_chart(make_scatter(df, 'BoundaryPct','SixPct','Player',
                'SixPct','Runs','Boundary %','Six %','Blues'),
                use_container_width=True)
    elif fmt == "ODI":
        with g1:
            st.caption("Strike Rate vs Average — bubble = innings played")
            st.plotly_chart(make_scatter(df, 'Average','StrikeRate','Player',
                'StrikeRate','Innings','Average','Strike Rate','RdYlGn'),
                use_container_width=True)
        with g2:
            st.caption("Centuries (Y) vs Fifties (X) — bubble = runs")
            st.plotly_chart(make_scatter(df, 'Fifties','Hundreds','Player',
                'Average','Runs','Fifties (50s)','Centuries (100s)','Viridis'),
                use_container_width=True)
    else:  # Test
        with g1:
            st.caption("Average vs Innings Played — bubble = high score")
            st.plotly_chart(make_scatter(df, 'Innings','Average','Player',
                'Average','HighScore','Innings Played','Batting Average','Viridis'),
                use_container_width=True)
        with g2:
            st.caption("Centuries (Y) vs Fifties (X) — bubble = innings")
            st.plotly_chart(make_scatter(df, 'Fifties','Hundreds','Player',
                'Average','Innings','Fifties (50s)','Centuries (100s)','RdYlGn'),
                use_container_width=True)

def bowl_graphs(df, fmt):
    g1, g2 = st.columns(2)
    FS = 18  # fixed bubble size for all bowling graphs
    if fmt == "T20":
        with g1:
            st.caption("Economy vs Wickets")
            st.plotly_chart(make_scatter(df,'Economy','Wickets','Player',
                'Economy',None,'Economy','Wickets','RdYlGn_r',fixed_size=FS),
                use_container_width=True)
        with g2:
            pdf = df[df['BowlingAvg'].notna()]
            st.caption("Bowling Average vs Economy")
            st.plotly_chart(make_scatter(pdf,'Economy','BowlingAvg','Player',
                'BowlingAvg',None,'Economy','Bowling Average','RdYlGn_r',fixed_size=FS),
                use_container_width=True)
    elif fmt == "ODI":
        with g1:
            pdf = df[df['BowlingAvg'].notna()]
            st.caption("Economy vs Bowling Average")
            st.plotly_chart(make_scatter(pdf,'Economy','BowlingAvg','Player',
                'Economy',None,'Economy','Bowling Average','RdYlGn_r',fixed_size=FS),
                use_container_width=True)
        with g2:
            pdf2 = df[df['BowlingSR'].notna()]
            st.caption("Wickets vs Bowling Strike Rate")
            st.plotly_chart(make_scatter(pdf2,'BowlingSR','Wickets','Player',
                'Economy',None,'Bowling Strike Rate','Wickets','Blues_r',fixed_size=FS),
                use_container_width=True)
    else:  # Test
        with g1:
            pdf = df[df['BowlingAvg'].notna() & df['BowlingSR'].notna()]
            st.caption("Bowling Average vs Strike Rate")
            st.plotly_chart(make_scatter(pdf,'BowlingSR','BowlingAvg','Player',
                'Economy',None,'Strike Rate','Average','RdYlGn_r',fixed_size=FS),
                use_container_width=True)
        with g2:
            st.caption("Economy vs Wickets")
            st.plotly_chart(make_scatter(df,'Economy','Wickets','Player',
                'Economy',None,'Economy','Wickets','RdYlGn_r',fixed_size=FS),
                use_container_width=True)

def team_bat_graphs(df):
    g1, g2 = st.columns(2)
    with g1:
        st.caption("Team Strike Rate vs Runs — bubble = fours hit")
        st.plotly_chart(make_scatter(df,'Runs','TeamSR','Team',
            'TeamSR','Fours','Runs Scored','Strike Rate','RdYlGn'),
            use_container_width=True)
    with g2:
        st.caption("Boundary% vs Six% — bubble = sixes hit")
        st.plotly_chart(make_scatter(df,'BoundaryPct','SixPct','Team',
            'SixPct','Sixes','Boundary %','Six %','Blues'),
            use_container_width=True)

def team_bowl_graphs(df):
    g1, g2 = st.columns(2)
    FS = 22
    with g1:
        st.caption("Economy vs Wickets Taken")
        st.plotly_chart(make_scatter(df,'Economy','Wickets','Team',
            'Economy',None,'Economy','Wickets','RdYlGn_r',fixed_size=FS),
            use_container_width=True)
    with g2:
        st.caption("Runs Conceded vs Wickets")
        st.plotly_chart(make_scatter(df,'RunsConceded','Wickets','Team',
            'Economy',None,'Runs Conceded','Wickets','Blues_r',fixed_size=FS),
            use_container_width=True)

# ════════════════════════════════════════════════════════════════════════════════
# STAT TABLES
# ════════════════════════════════════════════════════════════════════════════════

def bat_table(df):
    cols = ['Player','Team','Innings','Runs','Balls','Fours','Sixes',
            'HighScore','Hundreds','Fifties','Average','StrikeRate',
            'BoundaryPct','SixPct','CatchPct','BowledPct','LBWPct',
            'StumpedPct','RunOutPct','Outs','NotOuts']
    show = df[[c for c in cols if c in df.columns]].sort_values('Runs', ascending=False)
    show = show.rename(columns={
        'StrikeRate':'SR','BoundaryPct':'Bdry%','SixPct':'Six%',
        'CatchPct':'Ct%','BowledPct':'Bwld%','LBWPct':'LBW%',
        'StumpedPct':'St%','RunOutPct':'RO%','HighScore':'HS','NotOuts':'NO',
    })
    fmt_cols = {c: st.column_config.NumberColumn(format="%.1f")
                for c in ['SR','Average','Bdry%','Six%','Ct%','Bwld%','LBW%','St%','RO%']}
    st.dataframe(show.reset_index(drop=True), use_container_width=True,
                 hide_index=True, column_config=fmt_cols)

def bowl_table(df):
    cols = ['Player','Team','Innings','Overs','Maidens','Runs','Wickets',
            'Economy','BowlingAvg','BowlingSR']
    show = df[[c for c in cols if c in df.columns]].sort_values('Wickets', ascending=False)
    show = show.rename(columns={'BowlingAvg':'Avg','BowlingSR':'SR'})
    fmt_cols = {c: st.column_config.NumberColumn(format="%.2f") for c in ['Avg','Economy','SR']}
    st.dataframe(show.reset_index(drop=True), use_container_width=True,
                 hide_index=True, column_config=fmt_cols)

# ════════════════════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════════════════════

if not st.session_state.all_innings:
    st.title("🏏 Cricket Analytics Dashboard")
    st.markdown("""
**Welcome!** Upload scorecard `.txt` files via the sidebar.

**Filename naming:**
- T20 → include `t20` or `20 league`
- ODI → include `odi`
- Test → include `test` or `wtc`

The 4-digit year in the filename is auto-detected as the season.
    """)
    st.info("📂 Upload files via the sidebar to get started.")
    st.stop()

# Build dataframes
bat_df       = build_player_batting(st.session_state.all_innings)
bowl_df      = build_player_bowling(st.session_state.all_innings)
team_bat_df  = build_team_batting(st.session_state.all_innings)
team_bowl_df = build_team_bowling(st.session_state.all_innings)
all_seasons  = sorted({i['season'] for i in st.session_state.all_innings if i['season'] != 'Unknown'})

st.title("🏏 Cricket Analytics Dashboard")
fmt_tabs = st.tabs(["🏏 Test", "🟡 ODI", "⚡ T20"])

for tab_widget, (tab_label, fmt) in zip(fmt_tabs,
        [("Test","Test"),("ODI","ODI"),("T20","T20")]):
    with tab_widget:
        fmt_innings   = [i for i in st.session_state.all_innings if i['match_type'] == fmt]
        if not fmt_innings:
            st.info(f"No {fmt} data yet — upload a file with `{fmt.lower()}` in the filename.")
            continue

        all_fmt_teams = sorted({i['team'] for i in fmt_innings})
        n_matches     = len({i['filename'] for i in fmt_innings})
        st.markdown(f"**{n_matches} match(es) · {len(fmt_innings)} innings · {len(all_fmt_teams)} team(s)**")

        fc, _ = st.columns([2,8])
        with fc:
            focus = st.radio("View", ["Players","Teams"], horizontal=True, key=f"focus_{fmt}")

        max_inn = 4 if fmt == "Test" else 2

        # ── PLAYERS ──────────────────────────────────────────────────────────
        if focus == "Players":
            bat_tab, bowl_tab = st.tabs(["🏏 Batting","⚾ Bowling"])

            with bat_tab:
                with st.expander("🔍 Filters", expanded=True):
                    teams,pos,seas,inns,avg_op,avg_v,sr_op,sr_v = \
                        filter_row_bat(f"pb_{fmt}", all_fmt_teams, all_seasons, max_inn)
                fdf = apply_bat_filters(bat_df, fmt, teams, pos, seas, inns, avg_op, avg_v, sr_op, sr_v)

                if fdf.empty:
                    st.warning("No data matches the current filters.")
                else:
                    mc = st.columns(6)
                    for col,(lbl,val) in zip(mc,[
                        ("Players",    len(fdf)),
                        ("Total Runs", fdf['Runs'].sum()),
                        ("Best Avg",   f"{fdf['Average'].max():.1f}"),
                        ("Best SR",    f"{fdf['StrikeRate'].max():.1f}"),
                        ("100s",       fdf['Hundreds'].sum()),
                        ("50s",        fdf['Fifties'].sum()),
                    ]):
                        with col: st.markdown(metric_html(lbl,val), unsafe_allow_html=True)

                    section("📊 Graphs")
                    bat_graphs(fdf, fmt)
                    section("📋 Player Batting Stats")
                    bat_table(fdf)

            with bowl_tab:
                with st.expander("🔍 Filters", expanded=True):
                    b_teams,b_seas,b_inns,avg_op,avg_v,econ_op,econ_v,sr_op,sr_v = \
                        filter_row_bowl(f"pbw_{fmt}", all_fmt_teams, all_seasons, max_inn)
                bfdf = apply_bowl_filters(bowl_df, fmt, b_teams, b_seas, b_inns,
                                          avg_op, avg_v, econ_op, econ_v, sr_op, sr_v)

                if bfdf.empty:
                    st.warning("No data matches the current filters.")
                else:
                    bmc = st.columns(5)
                    for col,(lbl,val) in zip(bmc,[
                        ("Bowlers",   len(bfdf)),
                        ("Wickets",   bfdf['Wickets'].sum()),
                        ("Best Econ", f"{bfdf[bfdf['Economy']>0]['Economy'].min():.2f}"
                                      if not bfdf[bfdf['Economy']>0].empty else "—"),
                        ("Best Avg",  f"{bfdf['BowlingAvg'].dropna().min():.1f}"
                                      if not bfdf['BowlingAvg'].dropna().empty else "—"),
                        ("Best SR",   f"{bfdf['BowlingSR'].dropna().min():.1f}"
                                      if not bfdf['BowlingSR'].dropna().empty else "—"),
                    ]):
                        with col: st.markdown(metric_html(lbl,val), unsafe_allow_html=True)

                    section("📊 Graphs")
                    bowl_graphs(bfdf, fmt)
                    section("📋 Player Bowling Stats")
                    bowl_table(bfdf)

        # ── TEAMS ────────────────────────────────────────────────────────────
        else:
            t_bat  = team_bat_df[team_bat_df['Match_Type']  == fmt].copy() if not team_bat_df.empty  else pd.DataFrame()
            t_bowl = team_bowl_df[team_bowl_df['Match_Type'] == fmt].copy() if not team_bowl_df.empty else pd.DataFrame()

            tf, _ = st.columns([3,7])
            with tf:
                t_sel = st.multiselect("Filter by team", all_fmt_teams, key=f"tsel_{fmt}")
            if t_sel:
                t_bat  = t_bat[t_bat['Team'].isin(t_sel)]   if not t_bat.empty  else t_bat
                t_bowl = t_bowl[t_bowl['Team'].isin(t_sel)] if not t_bowl.empty else t_bowl

            bat_tab2, bowl_tab2 = st.tabs(["🏏 Batting","⚾ Bowling"])

            with bat_tab2:
                if t_bat.empty:
                    st.info("No team batting data for this format.")
                else:
                    tmc = st.columns(4)
                    for col,(lbl,val) in zip(tmc,[
                        ("Highest Score", t_bat['Runs'].max()),
                        ("Avg Score",     f"{t_bat['Runs'].mean():.0f}"),
                        ("Best SR",       f"{t_bat['TeamSR'].max():.1f}"),
                        ("Best Bdry%",    f"{t_bat['BoundaryPct'].max():.1f}%"),
                    ]):
                        with col: st.markdown(metric_html(lbl,val), unsafe_allow_html=True)
                    section("📊 Graphs")
                    team_bat_graphs(t_bat)
                    section("📋 Team Batting Stats")
                    st.dataframe(
                        t_bat[['Team','Season','Innings','Runs','Wickets',
                               'Fours','Sixes','BoundaryPct','SixPct','TeamSR']
                        ].sort_values('Runs', ascending=False).reset_index(drop=True),
                        use_container_width=True, hide_index=True,
                        column_config={
                            'BoundaryPct': st.column_config.NumberColumn("Bdry%", format="%.1f"),
                            'SixPct':      st.column_config.NumberColumn("Six%",  format="%.1f"),
                            'TeamSR':      st.column_config.NumberColumn("SR",    format="%.2f"),
                        }
                    )

            with bowl_tab2:
                if t_bowl.empty:
                    st.info("No team bowling data for this format.")
                else:
                    section("📊 Graphs")
                    team_bowl_graphs(t_bowl)
                    section("📋 Team Bowling Stats")
                    st.dataframe(
                        t_bowl[['Team','Season','Innings','Wickets',
                                'Overs','RunsConceded','Economy']
                        ].sort_values('Wickets', ascending=False).reset_index(drop=True),
                        use_container_width=True, hide_index=True,
                        column_config={
                            'Economy': st.column_config.NumberColumn(format="%.2f"),
                        }
                    )
