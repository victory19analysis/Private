import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import re
from collections import defaultdict

# ─── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Cricket Analytics Dashboard",
    page_icon="🏏",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── Custom CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    .main .block-container { padding-top: 1.5rem; }
    .metric-card {
        background: #1e293b;
        border-radius: 12px;
        padding: 1rem 1.2rem;
        text-align: center;
        border: 1px solid #334155;
    }
    .metric-card .label { font-size: 0.75rem; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em; }
    .metric-card .value { font-size: 1.6rem; font-weight: 700; color: #f1f5f9; margin-top: 2px; }
    .metric-card .sub   { font-size: 0.75rem; color: #64748b; margin-top: 2px; }
    .section-header {
        font-size: 1.1rem; font-weight: 600; color: #e2e8f0;
        border-left: 3px solid #3b82f6; padding-left: 10px;
        margin: 1.2rem 0 0.8rem;
    }
    .stTabs [data-baseweb="tab"] { font-size: 0.9rem; }
    div[data-testid="stFileUploader"] { border: 2px dashed #334155; border-radius: 10px; padding: 0.5rem; }
    .file-badge {
        display: inline-block;
        background: #1e3a5f;
        border: 1px solid #2563eb;
        border-radius: 6px;
        padding: 2px 8px;
        font-size: 0.75rem;
        color: #93c5fd;
        margin: 2px;
    }
</style>
""", unsafe_allow_html=True)


# ─── Scorecard Parser ──────────────────────────────────────────────────────────

def detect_match_type(filename: str) -> str:
    fn = filename.lower()
    if any(k in fn for k in ["20 league", "t20", "twenty20", "20over", "20_league", " 20 "]):
        return "T20"
    if any(k in fn for k in ["odi", "one day", "oneday", "list a"]):
        return "ODI"
    if any(k in fn for k in ["test", "wtc", "4th", "5th", "1st test", "2nd test", "3rd test"]):
        return "Test"
    return "Unknown"

def extract_season(filename: str) -> str:
    m = re.search(r'(20\d\d)', filename)
    return m.group(1) if m else "Unknown"

def parse_batting_line(line: str):
    """Parse a batting scorecard line. Returns dict or None."""
    line = line.strip()
    if not line or line.startswith('-') or line.startswith('*') or line.startswith('Extras') or line.startswith('TOTAL'):
        return None
    # Pattern: Name ... R B 4s 6s
    m = re.match(
        r'^([A-Z][A-Za-z\s\'\-]+?)\s+'   # Name (greedy up to dismissal)
        r'(c\s+\S+\s+b\s+\S+|'           # caught
        r'lbw\s+b\s+\S+|'                # lbw
        r'b\s+\S+|'                       # bowled
        r'run\s+out|'                     # run out
        r'st\s+\S+\s+b\s+\S+|'           # stumped
        r'hit\s+wicket|'                  # hit wicket
        r'not\s+out|'                     # not out
        r'retired|'                       # retired
        r'absent)'                        # absent
        r'\s+(\d+)\s+(\d+)\s+(\d+|-)\s+(\d+|-)$',
        line, re.IGNORECASE
    )
    if not m:
        return None
    name = m.group(1).strip()
    dismissal = m.group(2).strip().lower()
    runs  = int(m.group(3))
    balls = int(m.group(4))
    fours = int(m.group(5)) if m.group(5) != '-' else 0
    sixes = int(m.group(6)) if m.group(6) != '-' else 0
    out   = 'not out' not in dismissal and 'absent' not in dismissal and 'retired' not in dismissal
    how_out = 'not out' if not out else dismissal
    return dict(name=name, runs=runs, balls=balls, fours=fours, sixes=sixes,
                out=out, dismissal=how_out)

def parse_bowling_line(line: str):
    """Parse a bowling scorecard line. Returns dict or None."""
    line = line.strip()
    if not line or line.startswith('-') or line.startswith('*') or 'Fall' in line:
        return None
    # Name  O  M/D  R  W  Econ
    m = re.match(
        r'^([A-Z][A-Za-z\s\'\-]+?)\s+'
        r'(\d+(?:\.\d+)?)\s+(\d+)\s+(\d+)\s+(\d+)\s+([\d.]+)$',
        line
    )
    if not m:
        return None
    return dict(
        name=m.group(1).strip(),
        overs=float(m.group(2)),
        maidens=int(m.group(3)),
        runs=int(m.group(4)),
        wickets=int(m.group(5)),
        economy=float(m.group(6)),
    )

def parse_scorecard(text: str, filename: str, match_type: str, season: str):
    """Parse a full scorecard text, return list of innings dicts."""
    lines = text.split('\n')
    innings_list = []
    current_team = None
    current_innings_num = 0
    in_batting = False
    in_bowling = False
    batting_rows = []
    bowling_rows = []
    batting_order = 0
    total_runs = 0
    total_wickets = 0
    total_overs_str = ""

    def flush_innings():
        if current_team and (batting_rows or bowling_rows):
            innings_list.append(dict(
                team=current_team,
                innings_num=current_innings_num,
                batting=list(batting_rows),
                bowling=list(bowling_rows),
                total_runs=total_runs,
                total_wickets=total_wickets,
                total_overs=total_overs_str,
                match_type=match_type,
                season=season,
                filename=filename,
            ))

    for raw in lines:
        line = raw.strip()

        # Detect innings header  e.g. "India - 1st Innings"
        inn_header = re.match(r'^(.+?)\s+-\s+(\d+(?:st|nd|rd|th))\s+Innings', line, re.IGNORECASE)
        if inn_header:
            flush_innings()
            current_team = inn_header.group(1).strip()
            ord_map = {'1st': 1, '2nd': 2, '3rd': 3, '4th': 4}
            current_innings_num = ord_map.get(inn_header.group(2).lower(), 1)
            in_batting = True
            in_bowling = False
            batting_rows.clear()
            bowling_rows.clear()
            batting_order = 0
            total_runs = 0
            total_wickets = 0
            total_overs_str = ""
            continue

        # TOTAL line
        tot_m = re.search(r'TOTAL.*?(\d+)\s*$', line)
        if tot_m:
            total_runs = int(tot_m.group(1))
            wkt_m = re.search(r'\((\d+)\s+wkts?', line)
            all_m = re.search(r'all out', line, re.IGNORECASE)
            over_m = re.search(r'(\d+(?:\.\d+)?)\s+overs', line)
            total_wickets = int(wkt_m.group(1)) if wkt_m else (10 if all_m else 10)
            total_overs_str = over_m.group(1) if over_m else ""
            in_batting = False
            in_bowling = True
            continue

        # Extras line
        if line.startswith('Extras'):
            in_batting = False
            continue

        # Bowling header (O M R W Econ)
        if re.match(r'^\s*O\s+M\s+R\s+W', line) or re.match(r'^\s*O\s+D\s+R\s+W', line):
            in_batting = False
            in_bowling = True
            continue

        # Fall of Wickets
        if 'Fall of Wickets' in line or re.match(r'^\d+-\d+', line):
            in_bowling = False
            continue

        if in_batting:
            parsed = parse_batting_line(line)
            if parsed:
                batting_order += 1
                parsed['position'] = batting_order
                batting_rows.append(parsed)

        elif in_bowling:
            parsed = parse_bowling_line(line)
            if parsed:
                bowling_rows.append(parsed)

    flush_innings()
    return innings_list


def build_player_batting_stats(all_innings):
    """Aggregate per-player batting stats across multiple innings."""
    player_stats = defaultdict(lambda: dict(
        matches=set(), innings=0, runs=0, balls=0,
        fours=0, sixes=0, outs=0, fifties=0, hundreds=0,
        positions=[], seasons=set(), match_types=set(),
        not_outs=0, high_score=0,
        dot_balls=0,  # cannot derive from scorecard alone; placeholder
    ))
    for inn in all_innings:
        for row in inn['batting']:
            key = (row['name'], inn['match_type'])
            p = player_stats[key]
            p['team'] = inn['team']
            p['match_types'].add(inn['match_type'])
            p['seasons'].add(inn['season'])
            p['innings'] += 1
            p['runs'] += row['runs']
            p['balls'] += row['balls']
            p['fours'] += row['fours']
            p['sixes'] += row['sixes']
            if row['out']:
                p['outs'] += 1
            else:
                p['not_outs'] += 1
            if row['runs'] >= 100:
                p['hundreds'] += 1
            elif row['runs'] >= 50:
                p['fifties'] += 1
            p['positions'].append(row['position'])
            p['high_score'] = max(p['high_score'], row['runs'])
    records = []
    for (name, mt), p in player_stats.items():
        avg_pos = round(sum(p['positions']) / len(p['positions']), 1) if p['positions'] else 0
        sr = round(p['runs'] / p['balls'] * 100, 2) if p['balls'] > 0 else 0
        avg = round(p['runs'] / p['outs'], 2) if p['outs'] > 0 else (p['runs'] if p['innings'] > 0 else 0)
        boundary_runs = p['fours'] * 4 + p['sixes'] * 6
        boundary_pct = round(boundary_runs / p['runs'] * 100, 1) if p['runs'] > 0 else 0
        records.append(dict(
            Player=name,
            Match_Type=mt,
            Innings=p['innings'],
            Runs=p['runs'],
            Balls=p['balls'],
            Avg_Position=avg_pos,
            Positions=p['positions'],
            Seasons=sorted(p['seasons']),
            HighScore=p['high_score'],
            Outs=p['outs'],
            NotOuts=p['not_outs'],
            Fours=p['fours'],
            Sixes=p['sixes'],
            Fifties=p['fifties'],
            Hundreds=p['hundreds'],
            StrikeRate=sr,
            Average=avg,
            BoundaryPct=boundary_pct,
        ))
    return pd.DataFrame(records)


def build_player_bowling_stats(all_innings):
    """Aggregate per-player bowling stats."""
    player_stats = defaultdict(lambda: dict(
        innings=0, overs=0.0, maidens=0, runs=0,
        wickets=0, seasons=set(), match_types=set(),
    ))
    for inn in all_innings:
        for row in inn['bowling']:
            key = (row['name'], inn['match_type'])
            p = player_stats[key]
            p['seasons'].add(inn['season'])
            p['match_types'].add(inn['match_type'])
            p['innings'] += 1
            p['overs'] += row['overs']
            p['maidens'] += row['maidens']
            p['runs'] += row['runs']
            p['wickets'] += row['wickets']
    records = []
    for (name, mt), p in player_stats.items():
        balls = int(p['overs']) * 6 + round((p['overs'] % 1) * 10)
        economy = round(p['runs'] / p['overs'], 2) if p['overs'] > 0 else 0
        bowl_avg = round(p['runs'] / p['wickets'], 2) if p['wickets'] > 0 else None
        bowl_sr  = round(balls / p['wickets'], 2) if p['wickets'] > 0 else None
        records.append(dict(
            Player=name,
            Match_Type=mt,
            Innings=p['innings'],
            Overs=round(p['overs'], 1),
            Maidens=p['maidens'],
            Runs=p['runs'],
            Wickets=p['wickets'],
            Seasons=sorted(p['seasons']),
            Economy=economy,
            BowlingAvg=bowl_avg,
            BowlingSR=bowl_sr,
        ))
    return pd.DataFrame(records)


def build_team_batting_stats(all_innings):
    rows = []
    for inn in all_innings:
        runs = inn['total_runs']
        wkts = inn['total_wickets']
        fours = sum(r['fours'] for r in inn['batting'])
        sixes = sum(r['sixes'] for r in inn['batting'])
        balls_faced = sum(r['balls'] for r in inn['batting'])
        boundary_runs = fours * 4 + sixes * 6
        boundary_pct = round(boundary_runs / runs * 100, 1) if runs > 0 else 0
        sr = round(runs / balls_faced * 100, 2) if balls_faced > 0 else 0
        rows.append(dict(
            Team=inn['team'],
            Match_Type=inn['match_type'],
            Season=inn['season'],
            Innings=inn['innings_num'],
            Runs=runs,
            Wickets=wkts,
            Fours=fours,
            Sixes=sixes,
            BoundaryPct=boundary_pct,
            TeamSR=sr,
        ))
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def build_team_bowling_stats(all_innings):
    rows = []
    for inn in all_innings:
        total_wkts = sum(r['wickets'] for r in inn['bowling'])
        total_runs = sum(r['runs'] for r in inn['bowling'])
        total_overs = sum(r['overs'] for r in inn['bowling'])
        econ = round(total_runs / total_overs, 2) if total_overs > 0 else 0
        rows.append(dict(
            Team=inn['team'],
            Match_Type=inn['match_type'],
            Season=inn['season'],
            Innings=inn['innings_num'],
            Wickets=total_wkts,
            RunsConceded=total_runs,
            Overs=round(total_overs, 1),
            Economy=econ,
        ))
    return pd.DataFrame(rows) if rows else pd.DataFrame()


# ─── Session State ─────────────────────────────────────────────────────────────
if 'all_innings' not in st.session_state:
    st.session_state.all_innings = []
if 'file_log' not in st.session_state:
    st.session_state.file_log = []


# ─── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🏏 Cricket Analytics")
    st.markdown("---")

    uploaded_files = st.file_uploader(
        "Upload Scorecard Files (.txt)",
        type=["txt"],
        accept_multiple_files=True,
        help="Upload plain-text scorecards. Format: filename must contain 'test', 'odi', or '20 league'.",
    )

    if uploaded_files:
        new_added = 0
        for uf in uploaded_files:
            already = any(f['filename'] == uf.name for f in st.session_state.file_log)
            if not already:
                text = uf.read().decode("utf-8", errors="ignore")
                mt = detect_match_type(uf.name)
                season = extract_season(uf.name)
                innings = parse_scorecard(text, uf.name, mt, season)
                st.session_state.all_innings.extend(innings)
                st.session_state.file_log.append(dict(filename=uf.name, type=mt, season=season, innings=len(innings)))
                new_added += 1
        if new_added:
            st.success(f"✅ {new_added} new file(s) loaded!")

    st.markdown("---")

    if st.session_state.file_log:
        st.markdown("**Loaded files:**")
        for f in st.session_state.file_log:
            col1, col2 = st.columns([3, 1])
            with col1:
                st.markdown(f"<div class='file-badge'>{f['type']}</div> `{f['filename'][:28]}`", unsafe_allow_html=True)
            with col2:
                if st.button("✕", key=f"del_{f['filename']}", help="Remove this file"):
                    st.session_state.all_innings = [
                        i for i in st.session_state.all_innings if i['filename'] != f['filename']
                    ]
                    st.session_state.file_log = [x for x in st.session_state.file_log if x['filename'] != f['filename']]
                    st.rerun()

        st.markdown("---")
        if st.button("🗑️ Clear ALL data", use_container_width=True):
            st.session_state.all_innings = []
            st.session_state.file_log = []
            st.rerun()

    st.markdown("---")
    st.caption("Data stays in your session only — nothing is stored permanently.")


# ─── Main ──────────────────────────────────────────────────────────────────────
if not st.session_state.all_innings:
    st.title("🏏 Cricket Analytics Dashboard")
    st.markdown("""
    **Welcome!** Upload your scorecard `.txt` files using the sidebar to get started.

    **File naming convention:**
    - T20 matches → include `20 league` or `t20` in filename
    - ODI matches → include `odi` in filename
    - Test matches → include `test` or `wtc` in filename

    **Scorecard format:** Standard tabular scorecard with batting and bowling sections.
    """)
    st.info("📂 Use the sidebar to upload scorecard files.")
    st.stop()

# Build all stat dataframes
bat_df  = build_player_batting_stats(st.session_state.all_innings)
bowl_df = build_player_bowling_stats(st.session_state.all_innings)
team_bat_df  = build_team_batting_stats(st.session_state.all_innings)
team_bowl_df = build_team_bowling_stats(st.session_state.all_innings)

all_seasons = sorted(set(i['season'] for i in st.session_state.all_innings if i['season'] != 'Unknown'))

# ─── Format tabs ───────────────────────────────────────────────────────────────
format_tab_names = ["🏏 Test", "🟡 ODI", "⚡ T20"]
format_map = {"🏏 Test": "Test", "🟡 ODI": "ODI", "⚡ T20": "T20"}

st.title("🏏 Cricket Analytics Dashboard")
fmt_tabs = st.tabs(format_tab_names)

SCATTER_TEMPLATE = "plotly_dark"

def metric_html(label, value, sub=""):
    return f"""<div class='metric-card'>
        <div class='label'>{label}</div>
        <div class='value'>{value}</div>
        <div class='sub'>{sub}</div>
    </div>"""


def section(title):
    st.markdown(f"<div class='section-header'>{title}</div>", unsafe_allow_html=True)


def apply_bat_filters(df, fmt, focus, seasons, innings_filter, pos_filter, avg_op, avg_val, sr_op, sr_val):
    if df.empty:
        return df
    df = df[df['Match_Type'] == fmt].copy()
    if seasons:
        df = df[df['Seasons'].apply(lambda s: any(x in s for x in seasons))]
    if innings_filter:
        df = df[df['Innings'].isin(innings_filter)]
    if pos_filter:
        # any overlap with position list
        df = df[df['Positions'].apply(lambda ps: any(p in pos_filter for p in ps))]
    ops = {'<': lambda a, b: a < b, '=': lambda a, b: a == b, '>': lambda a, b: a > b}
    if avg_val is not None:
        df = df[ops[avg_op](df['Average'], avg_val)]
    if sr_val is not None:
        df = df[ops[sr_op](df['StrikeRate'], sr_val)]
    return df


def apply_bowl_filters(df, fmt, seasons, innings_filter, avg_op, avg_val, econ_op, econ_val, sr_op, sr_val):
    if df.empty:
        return df
    df = df[df['Match_Type'] == fmt].copy()
    if seasons:
        df = df[df['Seasons'].apply(lambda s: any(x in s for x in seasons))]
    if innings_filter:
        df = df[df['Innings'].isin(innings_filter)]
    ops = {'<': lambda a, b: a < b, '=': lambda a, b: a == b, '>': lambda a, b: a > b}
    if avg_val is not None:
        df_filtered = df[df['BowlingAvg'].notna()]
        df = df_filtered[ops[avg_op](df_filtered['BowlingAvg'], avg_val)]
    if econ_val is not None:
        df = df[ops[econ_op](df['Economy'], econ_val)]
    if sr_val is not None:
        df_filtered = df[df['BowlingSR'].notna()]
        df = df_filtered[ops[sr_op](df_filtered['BowlingSR'], sr_val)]
    return df


def render_format_tab(fmt):
    fmt_innings = [i for i in st.session_state.all_innings if i['match_type'] == fmt]
    if not fmt_innings:
        st.info(f"No {fmt} data uploaded yet. Upload scorecards with '{fmt.lower()}' in the filename.")
        return

    # Top summary
    teams = list(set(i['team'] for i in fmt_innings))
    total_matches = len(set(i['filename'] for i in fmt_innings))
    st.markdown(f"**{total_matches} match(es) · {len(fmt_innings)} innings · {len(teams)} team(s)**")

    focus_col, _ = st.columns([2, 8])
    with focus_col:
        focus = st.radio("View", ["Players", "Teams"], horizontal=True, key=f"focus_{fmt}")

    # ── Player view ────────────────────────────────────────────────────────────
    if focus == "Players":
        bat_tab, bowl_tab = st.tabs(["🏏 Batting", "⚾ Bowling"])

        # Batting
        with bat_tab:
            with st.expander("🔍 Filters", expanded=True):
                fc1, fc2, fc3, fc4, fc5 = st.columns([2, 2, 2, 3, 3])
                with fc1:
                    pos_opts = list(range(1, 12))
                    pos_sel = st.multiselect("Position", pos_opts, key=f"pos_{fmt}")
                with fc2:
                    season_sel = st.multiselect("Season", all_seasons, key=f"bat_season_{fmt}")
                with fc3:
                    max_inn = 4 if fmt == "Test" else 2
                    inn_opts = list(range(1, max_inn + 1))
                    inn_sel = st.multiselect("Innings #", inn_opts, key=f"bat_inn_{fmt}")
                with fc4:
                    a1, a2 = st.columns([1, 2])
                    with a1:
                        avg_op = st.selectbox("Avg", ['>', '<', '='], key=f"avg_op_{fmt}")
                    with a2:
                        avg_val = st.number_input("", min_value=0.0, value=0.0, step=5.0, key=f"avg_val_{fmt}", label_visibility="collapsed")
                    avg_val = avg_val if avg_val > 0 else None
                with fc5:
                    s1, s2 = st.columns([1, 2])
                    with s1:
                        sr_op = st.selectbox("SR", ['>', '<', '='], key=f"sr_op_{fmt}")
                    with s2:
                        sr_val = st.number_input("", min_value=0.0, value=0.0, step=5.0, key=f"sr_val_{fmt}", label_visibility="collapsed")
                    sr_val = sr_val if sr_val > 0 else None

            fdf = apply_bat_filters(bat_df, fmt, focus, season_sel, inn_sel, pos_sel, avg_op, avg_val, sr_op, sr_val)

            if fdf.empty:
                st.warning("No batting data matches current filters.")
            else:
                # Summary metrics
                cols = st.columns(6)
                metrics = [
                    ("Players", len(fdf), ""),
                    ("Total Runs", fdf['Runs'].sum(), ""),
                    ("Best Avg", f"{fdf['Average'].max():.1f}", ""),
                    ("Best SR", f"{fdf['StrikeRate'].max():.1f}", ""),
                    ("Centuries", fdf['Hundreds'].sum(), ""),
                    ("Fifties", fdf['Fifties'].sum(), ""),
                ]
                for col, (lbl, val, sub) in zip(cols, metrics):
                    with col:
                        st.markdown(metric_html(lbl, val, sub), unsafe_allow_html=True)

                # Graphs
                section("📊 Analytics Graphs")
                if fmt == "T20":
                    g1, g2 = st.columns(2)
                    with g1:
                        st.caption("Strike Rate vs Average")
                        fig = px.scatter(fdf, x='Average', y='StrikeRate', text='Player',
                                         color='StrikeRate', color_continuous_scale='RdYlGn',
                                         size='Runs', size_max=40,
                                         template=SCATTER_TEMPLATE,
                                         labels={'Average': 'Batting Average', 'StrikeRate': 'Strike Rate'})
                        fig.update_traces(textposition='top center', textfont_size=10)
                        fig.update_layout(height=380, margin=dict(l=10, r=10, t=20, b=10), showlegend=False)
                        st.plotly_chart(fig, use_container_width=True)
                    with g2:
                        st.caption("Boundary % vs Sixes proportion")
                        fdf2 = fdf.copy()
                        fdf2['SixPct'] = (fdf2['Sixes'] * 6 / fdf2['Runs'].replace(0, 1) * 100).round(1)
                        fig2 = px.scatter(fdf2, x='BoundaryPct', y='SixPct', text='Player',
                                          color='Runs', color_continuous_scale='Blues',
                                          size='Innings', size_max=30,
                                          template=SCATTER_TEMPLATE,
                                          labels={'BoundaryPct': 'Boundary %', 'SixPct': 'Six-run %'})
                        fig2.update_traces(textposition='top center', textfont_size=10)
                        fig2.update_layout(height=380, margin=dict(l=10, r=10, t=20, b=10), showlegend=False)
                        st.plotly_chart(fig2, use_container_width=True)

                elif fmt == "ODI":
                    g1, g2 = st.columns(2)
                    with g1:
                        st.caption("Strike Rate vs Average")
                        fig = px.scatter(fdf, x='Average', y='StrikeRate', text='Player',
                                         color='StrikeRate', color_continuous_scale='RdYlGn',
                                         size='Runs', size_max=40,
                                         template=SCATTER_TEMPLATE)
                        fig.update_traces(textposition='top center', textfont_size=10)
                        fig.update_layout(height=380, margin=dict(l=10, r=10, t=20, b=10), showlegend=False)
                        st.plotly_chart(fig, use_container_width=True)
                    with g2:
                        st.caption("Centuries & Fifties by Player")
                        fig2 = go.Figure()
                        fig2.add_bar(x=fdf['Player'], y=fdf['Hundreds'], name='100s', marker_color='#f59e0b')
                        fig2.add_bar(x=fdf['Player'], y=fdf['Fifties'], name='50s', marker_color='#3b82f6')
                        fig2.update_layout(barmode='stack', template=SCATTER_TEMPLATE,
                                           height=380, margin=dict(l=10, r=10, t=20, b=10))
                        st.plotly_chart(fig2, use_container_width=True)

                else:  # Test
                    g1, g2 = st.columns(2)
                    with g1:
                        st.caption("Average vs Innings Played")
                        fig = px.scatter(fdf, x='Innings', y='Average', text='Player',
                                         color='Average', color_continuous_scale='Viridis',
                                         size='HighScore', size_max=40,
                                         template=SCATTER_TEMPLATE,
                                         labels={'Innings': 'Innings Played', 'Average': 'Batting Average'})
                        fig.update_traces(textposition='top center', textfont_size=10)
                        fig.update_layout(height=380, margin=dict(l=10, r=10, t=20, b=10), showlegend=False)
                        st.plotly_chart(fig, use_container_width=True)
                    with g2:
                        st.caption("Centuries vs Fifties")
                        fig2 = go.Figure()
                        fig2.add_bar(x=fdf['Player'], y=fdf['Hundreds'], name='100s', marker_color='#f59e0b')
                        fig2.add_bar(x=fdf['Player'], y=fdf['Fifties'], name='50s', marker_color='#6366f1')
                        fig2.update_layout(barmode='group', template=SCATTER_TEMPLATE,
                                           height=380, margin=dict(l=10, r=10, t=20, b=10))
                        st.plotly_chart(fig2, use_container_width=True)

                # Stats table
                section("📋 Player Batting Stats")
                show_cols = ['Player', 'Innings', 'Runs', 'Balls', 'Fours', 'Sixes',
                             'HighScore', 'Hundreds', 'Fifties', 'Average', 'StrikeRate', 'BoundaryPct',
                             'Outs', 'NotOuts']
                show_df = fdf[[c for c in show_cols if c in fdf.columns]].sort_values('Runs', ascending=False)
                show_df = show_df.rename(columns={'BoundaryPct': 'Boundary%', 'StrikeRate': 'SR',
                                                   'HighScore': 'HS', 'NotOuts': 'NO'})
                st.dataframe(show_df.reset_index(drop=True), use_container_width=True,
                             hide_index=True,
                             column_config={
                                 'SR': st.column_config.NumberColumn(format="%.2f"),
                                 'Average': st.column_config.NumberColumn(format="%.2f"),
                                 'Boundary%': st.column_config.NumberColumn(format="%.1f"),
                             })

        # Bowling
        with bowl_tab:
            with st.expander("🔍 Filters", expanded=True):
                bc1, bc2, bc3, bc4, bc5 = st.columns([2, 2, 2, 3, 3])
                with bc1:
                    b_season_sel = st.multiselect("Season", all_seasons, key=f"bowl_season_{fmt}")
                with bc2:
                    b_max_inn = 4 if fmt == "Test" else 2
                    b_inn_opts = list(range(1, b_max_inn + 1))
                    b_inn_sel = st.multiselect("Innings #", b_inn_opts, key=f"bowl_inn_{fmt}")
                with bc3:
                    e1, e2 = st.columns([1, 2])
                    with e1:
                        econ_op = st.selectbox("Econ", ['<', '>', '='], key=f"econ_op_{fmt}")
                    with e2:
                        econ_val = st.number_input("", min_value=0.0, value=0.0, step=0.5, key=f"econ_val_{fmt}", label_visibility="collapsed")
                    econ_val = econ_val if econ_val > 0 else None
                with bc4:
                    ba1, ba2 = st.columns([1, 2])
                    with ba1:
                        b_avg_op = st.selectbox("Avg", ['<', '>', '='], key=f"b_avg_op_{fmt}")
                    with ba2:
                        b_avg_val = st.number_input("", min_value=0.0, value=0.0, step=5.0, key=f"b_avg_val_{fmt}", label_visibility="collapsed")
                    b_avg_val = b_avg_val if b_avg_val > 0 else None
                with bc5:
                    bs1, bs2 = st.columns([1, 2])
                    with bs1:
                        b_sr_op = st.selectbox("Bowl SR", ['<', '>', '='], key=f"b_sr_op_{fmt}")
                    with bs2:
                        b_sr_val = st.number_input("", min_value=0.0, value=0.0, step=5.0, key=f"b_sr_val_{fmt}", label_visibility="collapsed")
                    b_sr_val = b_sr_val if b_sr_val > 0 else None

            bfdf = apply_bowl_filters(bowl_df, fmt, b_season_sel, b_inn_sel,
                                       b_avg_op, b_avg_val, econ_op, econ_val, b_sr_op, b_sr_val)

            if bfdf.empty:
                st.warning("No bowling data matches current filters.")
            else:
                bcols = st.columns(5)
                bmetrics = [
                    ("Bowlers", len(bfdf), ""),
                    ("Total Wickets", bfdf['Wickets'].sum(), ""),
                    ("Best Economy", f"{bfdf[bfdf['Economy']>0]['Economy'].min():.2f}", ""),
                    ("Best Avg", f"{bfdf[bfdf['BowlingAvg'].notna()]['BowlingAvg'].min():.1f}", "" if not bfdf[bfdf['BowlingAvg'].notna()].empty else ""),
                    ("Best SR", f"{bfdf[bfdf['BowlingSR'].notna()]['BowlingSR'].min():.1f}", "" if not bfdf[bfdf['BowlingSR'].notna()].empty else ""),
                ]
                for col, (lbl, val, sub) in zip(bcols, bmetrics):
                    with col:
                        st.markdown(metric_html(lbl, val, sub), unsafe_allow_html=True)

                section("📊 Bowling Analytics Graphs")

                if fmt == "T20":
                    bg1, bg2 = st.columns(2)
                    with bg1:
                        st.caption("Economy vs Wickets")
                        fig = px.scatter(bfdf, x='Economy', y='Wickets', text='Player',
                                         color='Economy', color_continuous_scale='RdYlGn_r',
                                         size='Overs', size_max=35,
                                         template=SCATTER_TEMPLATE)
                        fig.update_traces(textposition='top center', textfont_size=10)
                        fig.update_layout(height=380, margin=dict(l=10, r=10, t=20, b=10), showlegend=False)
                        st.plotly_chart(fig, use_container_width=True)
                    with bg2:
                        st.caption("Bowling Average vs Economy")
                        plot_df = bfdf[bfdf['BowlingAvg'].notna()]
                        if not plot_df.empty:
                            fig2 = px.scatter(plot_df, x='Economy', y='BowlingAvg', text='Player',
                                              color='Wickets', color_continuous_scale='Blues',
                                              size='Wickets', size_max=35,
                                              template=SCATTER_TEMPLATE)
                            fig2.update_traces(textposition='top center', textfont_size=10)
                            fig2.update_layout(height=380, margin=dict(l=10, r=10, t=20, b=10), showlegend=False)
                            st.plotly_chart(fig2, use_container_width=True)
                        else:
                            st.info("Not enough wicket data for this chart.")

                elif fmt == "ODI":
                    bg1, bg2 = st.columns(2)
                    with bg1:
                        st.caption("Economy vs Bowling Average")
                        plot_df = bfdf[bfdf['BowlingAvg'].notna()]
                        if not plot_df.empty:
                            fig = px.scatter(plot_df, x='Economy', y='BowlingAvg', text='Player',
                                             color='Wickets', color_continuous_scale='RdYlGn_r',
                                             size='Overs', size_max=35,
                                             template=SCATTER_TEMPLATE)
                            fig.update_traces(textposition='top center', textfont_size=10)
                            fig.update_layout(height=380, margin=dict(l=10, r=10, t=20, b=10), showlegend=False)
                            st.plotly_chart(fig, use_container_width=True)
                        else:
                            st.info("Insufficient data for this chart.")
                    with bg2:
                        st.caption("Wickets vs Bowling Strike Rate")
                        plot_df = bfdf[bfdf['BowlingSR'].notna()]
                        if not plot_df.empty:
                            fig2 = px.scatter(plot_df, x='BowlingSR', y='Wickets', text='Player',
                                              color='Economy', color_continuous_scale='Blues_r',
                                              size='Overs', size_max=35,
                                              template=SCATTER_TEMPLATE,
                                              labels={'BowlingSR': 'Bowling Strike Rate'})
                            fig2.update_traces(textposition='top center', textfont_size=10)
                            fig2.update_layout(height=380, margin=dict(l=10, r=10, t=20, b=10), showlegend=False)
                            st.plotly_chart(fig2, use_container_width=True)
                        else:
                            st.info("Insufficient data for this chart.")

                else:  # Test
                    bg1, bg2 = st.columns(2)
                    with bg1:
                        st.caption("Bowling Average vs Strike Rate")
                        plot_df = bfdf[bfdf['BowlingAvg'].notna() & bfdf['BowlingSR'].notna()]
                        if not plot_df.empty:
                            fig = px.scatter(plot_df, x='BowlingSR', y='BowlingAvg', text='Player',
                                             color='Economy', color_continuous_scale='RdYlGn_r',
                                             size='Wickets', size_max=40,
                                             template=SCATTER_TEMPLATE,
                                             labels={'BowlingSR': 'Strike Rate', 'BowlingAvg': 'Average'})
                            fig.update_traces(textposition='top center', textfont_size=10)
                            fig.update_layout(height=380, margin=dict(l=10, r=10, t=20, b=10), showlegend=False)
                            st.plotly_chart(fig, use_container_width=True)
                        else:
                            st.info("Insufficient data for this chart.")
                    with bg2:
                        st.caption("Wickets by bowler")
                        fig2 = px.bar(bfdf.sort_values('Wickets', ascending=True),
                                      x='Wickets', y='Player', orientation='h',
                                      color='Economy', color_continuous_scale='RdYlGn_r',
                                      template=SCATTER_TEMPLATE)
                        fig2.update_layout(height=380, margin=dict(l=10, r=10, t=20, b=10), showlegend=False)
                        st.plotly_chart(fig2, use_container_width=True)

                section("📋 Player Bowling Stats")
                b_show = ['Player', 'Innings', 'Overs', 'Maidens', 'Runs', 'Wickets',
                           'BowlingAvg', 'Economy', 'BowlingSR']
                b_show_df = bfdf[[c for c in b_show if c in bfdf.columns]].sort_values('Wickets', ascending=False)
                b_show_df = b_show_df.rename(columns={'BowlingAvg': 'Avg', 'BowlingSR': 'SR'})
                st.dataframe(b_show_df.reset_index(drop=True), use_container_width=True,
                             hide_index=True,
                             column_config={
                                 'Avg': st.column_config.NumberColumn(format="%.2f"),
                                 'Economy': st.column_config.NumberColumn(format="%.2f"),
                                 'SR': st.column_config.NumberColumn(format="%.2f"),
                             })

    # ── Teams view ─────────────────────────────────────────────────────────────
    else:
        t_bat = team_bat_df[team_bat_df['Match_Type'] == fmt] if not team_bat_df.empty else pd.DataFrame()
        t_bowl = team_bowl_df[team_bowl_df['Match_Type'] == fmt] if not team_bowl_df.empty else pd.DataFrame()

        bat_tab2, bowl_tab2 = st.tabs(["🏏 Batting", "⚾ Bowling"])

        with bat_tab2:
            if t_bat.empty:
                st.info("No team batting data for this format.")
            else:
                tc1, tc2, tc3 = st.columns(3)
                with tc1:
                    st.markdown(metric_html("Highest Score", t_bat['Runs'].max(), ""), unsafe_allow_html=True)
                with tc2:
                    st.markdown(metric_html("Avg Team Score", f"{t_bat['Runs'].mean():.0f}", ""), unsafe_allow_html=True)
                with tc3:
                    st.markdown(metric_html("Best Boundary%", f"{t_bat['BoundaryPct'].max():.1f}%", ""), unsafe_allow_html=True)

                section("Team Scores")
                fig = px.bar(t_bat, x='Team', y='Runs', color='Season',
                              barmode='group', template=SCATTER_TEMPLATE,
                              text='Runs')
                fig.update_layout(height=350, margin=dict(l=10, r=10, t=20, b=10))
                st.plotly_chart(fig, use_container_width=True)

                section("Team Stats Table")
                st.dataframe(t_bat[['Team', 'Season', 'Innings', 'Runs', 'Wickets',
                                     'Fours', 'Sixes', 'BoundaryPct', 'TeamSR']].sort_values('Runs', ascending=False),
                             use_container_width=True, hide_index=True)

        with bowl_tab2:
            if t_bowl.empty:
                st.info("No team bowling data for this format.")
            else:
                section("Team Bowling Economy")
                fig = px.bar(t_bowl, x='Team', y='Economy', color='Season',
                              barmode='group', template=SCATTER_TEMPLATE,
                              text='Wickets')
                fig.update_layout(height=350, margin=dict(l=10, r=10, t=20, b=10))
                st.plotly_chart(fig, use_container_width=True)
                section("Team Bowling Stats Table")
                st.dataframe(t_bowl[['Team', 'Season', 'Innings', 'Wickets', 'Overs',
                                      'RunsConceded', 'Economy']].sort_values('Wickets', ascending=False),
                             use_container_width=True, hide_index=True)


# ─── Render each format tab ────────────────────────────────────────────────────
for tab_widget, tab_label in zip(fmt_tabs, format_tab_names):
    with tab_widget:
        render_format_tab(format_map[tab_label])
