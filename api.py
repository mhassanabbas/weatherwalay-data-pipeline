# ═══════════════════════════════════════════════════════
# Weather map API with timeline animation and interactive comparison UI
# ═══════════════════════════════════════════════════════
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
import pymongo
import os
import json

# Fallback logger wrapper to prevent crashes if error_logger is missing
try:
    from error_logger import get_all_errors, get_today_errors, log_error
except ImportError:
    def log_error(*args, **kwargs): pass
    def get_all_errors(): return []
    def get_today_errors(): return []
MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017")
DB_NAME   = "weather_db"
MAPS_DIR  = "maps"
TILES_DIR = "tiles"
LAT_MIN, LAT_MAX = 23.0, 37.0
LON_MIN, LON_MAX = 60.0, 77.0
NON_SOURCE_COLLECTIONS = {'pipeline_errors', 'system.indexes'}
HOURS_PER_DAY = [0, 3, 6, 9, 12, 15, 18, 21]

_TURBO_STOPS = ['#30123b', '#4675ed', '#1bcfd4', '#a4fc3b', '#f3c63a', '#fb8022', '#7a0402']

VAR_INFO = {
    'avgtemp': {'label': 'Temperature', 'unit': '°C',  'cmap': _TURBO_STOPS, 'min': 12, 'max': 34},
    'mintemp': {'label': 'Min Temp',    'unit': '°C',  'cmap': _TURBO_STOPS, 'min': 5,  'max': 28},
    'maxtemp': {'label': 'Max Temp',    'unit': '°C',  'cmap': _TURBO_STOPS, 'min': 14, 'max': 40},
    'avghum' : {'label': 'Humidity',    'unit': '%',   'cmap': _TURBO_STOPS, 'min': 40, 'max': 92},
    'avgwind': {'label': 'Wind Speed',  'unit': 'm/s', 'cmap': _TURBO_STOPS, 'min': 0,  'max': 9},
}

app = FastAPI(title="Weather Map API - Windy Style, Enhanced")


def get_db():
    client = pymongo.MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    return client, client[DB_NAME]


def get_all_sources():
    try:
        client, db = get_db()
        names = sorted([n for n in db.list_collection_names() if n not in NON_SOURCE_COLLECTIONS])
        client.close()
        return names
    except Exception as e:
        log_error("api", "get_all_sources", type(e).__name__, str(e))
        return []


def get_variables_for_source(source):
    if not os.path.exists(MAPS_DIR):
        return list(VAR_INFO.keys())
    files = [f for f in os.listdir(MAPS_DIR) if f.startswith(f"{source}_") and f.endswith('.png')]
    vars_found = sorted([f.replace('.png', '').replace(f'{source}_', '') for f in files])
    return vars_found if vars_found else list(VAR_INFO.keys())


def get_hours_for_source(source):
    base = os.path.join(TILES_DIR, source)
    if not os.path.exists(base):
        return HOURS_PER_DAY
    hours = set()
    for variable in os.listdir(base):
        var_path = os.path.join(base, variable)
        if os.path.isdir(var_path):
            for h in os.listdir(var_path):
                if h.isdigit():
                    hours.add(int(h))
    return sorted(list(hours)) if hours else HOURS_PER_DAY


@app.get("/")
def home():
    return {
        "message": "Weather Map API - Windy Style, Enhanced",
        "endpoints": [
            "/sources", "/variables/{source}", "/hours/{source}",
            "/fullmap/{source}/{variable}",
            "/map/{source}/{variable}",
            "/zoommap/{source}/{variable}/{zoom}/{hour}",
            "/compare/{source1}/{source2}/{variable}",
            "/query?lat=33.6&lon=73.0&source=ECM_Global_HR&hour=12",
            "/errors", "/errors/today",
        ]
    }


@app.get("/sources")
def get_sources():
    sources = get_all_sources()
    if not sources:
        raise HTTPException(status_code=404, detail="No source collections found. Run pipeline first.")
    return {"total_sources": len(sources), "sources": sources}


@app.get("/variables/{source}")
def get_variables(source: str):
    variables = get_variables_for_source(source)
    return {"source": source, "variables": variables}


@app.get("/hours/{source}")
def get_hours(source: str):
    return {"source": source, "hours": get_hours_for_source(source)}


@app.get("/fullmap/{source}/{variable}")
def get_fullmap(source: str, variable: str):
    map_path = os.path.join(MAPS_DIR, f"{source}_{variable}.png")
    if not os.path.exists(map_path):
        raise HTTPException(status_code=404, detail=f"Map not found: {map_path}")
    return FileResponse(map_path, media_type="image/png")


@app.get("/zoommap/{source}/{variable}/{zoom}/{hour}")
def get_zoom_image(source: str, variable: str, zoom: int, hour: int):
    zoom_path = os.path.join(TILES_DIR, source, variable, str(hour), f"z{zoom}.png")
    if os.path.exists(zoom_path):
        return FileResponse(zoom_path, media_type="image/png")
    
    fallback = os.path.join(TILES_DIR, source, variable, str(hour), "z6.png")
    if os.path.exists(fallback):
        return FileResponse(fallback, media_type="image/png")
        
    fullmap = os.path.join(MAPS_DIR, f"{source}_{variable}.png")
    if os.path.exists(fullmap):
        return FileResponse(fullmap, media_type="image/png")

    raise HTTPException(status_code=404, detail=f"Zoom image not found for source '{source}', hour {hour}.")


@app.get("/map/{source}/{variable}")
def get_map(source: str, variable: str):
    all_sources   = get_all_sources() or [source]
    all_variables = get_variables_for_source(source)
    hours         = get_hours_for_source(source)
    info          = VAR_INFO.get(variable, {'label': variable, 'unit': '', 'cmap': ['#000', '#fff'], 'min': 0, 'max': 100})
    colors        = ', '.join(info['cmap'])

    initial_hour_str = f"{hours[0]:02d}:00" if hours else "00:00"

    source_options = '\n'.join([f'<option value="{s}" {"selected" if s == source else ""}>{s}</option>' for s in all_sources])
    var_buttons = '\n'.join([
        f'''<button id="vbtn-{v}" onclick="switchVariable('{v}')" class="var-btn {'active' if v == variable else ''}">
            {VAR_INFO.get(v, {}).get('label', v)}
        </button>''' for v in all_variables
    ])

    html = f"""<!DOCTYPE html>
<html>
<head>
    <title>Weather Map - {source}</title>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <style>
        * {{ box-sizing:border-box; margin:0; padding:0; }}
        body {{ font-family:'Segoe UI', system-ui, -apple-system, sans-serif; background:#0b0f19; color:white; overflow:hidden; }}
        
        #topbar {{ position:fixed; top:12px; left:12px; right:12px; z-index:2000; 
                   background:rgba(15, 23, 42, 0.75); backdrop-filter:blur(12px); -webkit-backdrop-filter:blur(12px);
                   padding:10px 18px; border-radius:14px; display:flex; align-items:center; gap:14px; flex-wrap:wrap;
                   border:1px solid rgba(255,255,255,0.1); box-shadow:0 8px 32px rgba(0,0,0,0.4); }}
        #topbar h2 {{ color:#38bdf8; font-size:16px; white-space:nowrap; font-weight:800; letter-spacing:0.5px; }}
        #topbar h2 span {{ color:#f8fafc; font-weight:300; }}
        #source-select {{ padding:6px 12px; border-radius:20px; border:1px solid rgba(255,255,255,0.15);
                           background:#1e293b; color:white; font-size:13px; font-weight:600; cursor:pointer; outline:none; }}
        .var-btn {{ background:rgba(30, 41, 59, 0.6); color:#94a3b8; border:1px solid rgba(255,255,255,0.08); padding:6px 14px;
                   border-radius:20px; cursor:pointer; font-size:12px; transition:all 0.2s ease; font-weight:600; }}
        .var-btn:hover {{ background:rgba(51, 65, 85, 0.8); color:white; transform:translateY(-1px); }}
        .var-btn.active {{ background:#2563eb; color:white; border-color:#60a5fa; box-shadow:0 0 12px rgba(37,99,235,0.5); }}
        
        #map {{ height:100vh; width:100vw; z-index:1; background:#0b0f19; }}

        .leaflet-image-layer {{
            transition: opacity 0.35s ease-in-out;
            filter: blur(8px) contrast(110%);
            mix-blend-mode: screen;
            image-rendering: -webkit-optimize-contrast;
        }}

        #legend {{ position:absolute; bottom:95px; right:16px; z-index:1000; background:rgba(15, 23, 42, 0.82);
                   backdrop-filter:blur(10px); padding:14px 18px; border-radius:12px; color:white; min-width:200px; 
                   border:1px solid rgba(255,255,255,0.1); box-shadow:0 6px 20px rgba(0,0,0,0.4); }}
        #legend h4 {{ font-size:12px; color:#38bdf8; margin-bottom:8px; font-weight:700; text-transform:uppercase; letter-spacing:0.5px; }}
        #legend-bar {{ height:10px; width:100%; border-radius:6px; background:linear-gradient(to right, {colors}); margin-bottom:6px; }}
        #legend-labels {{ display:flex; justify-content:space-between; font-size:11px; color:#cbd5e1; font-weight:600; }}
        
        #opacity-box {{ position:absolute; bottom:95px; left:16px; z-index:1000; background:rgba(15, 23, 42, 0.82);
                   backdrop-filter:blur(10px); padding:10px 14px; border-radius:12px; color:white; border:1px solid rgba(255,255,255,0.1); }}
        #opacity-box label {{ display:block; font-size:11px; color:#94a3b8; font-weight:600; margin-bottom:4px; }}
        input[type=range] {{ width:120px; cursor:pointer; accent-color:#38bdf8; }}

        #timebar {{ position:fixed; bottom:16px; left:16px; right:16px; z-index:2000; 
                    background:rgba(15, 23, 42, 0.85); backdrop-filter:blur(12px); -webkit-backdrop-filter:blur(12px);
                    padding:12px 20px; border-radius:16px; display:flex; gap:16px; align-items:center; 
                    border:1px solid rgba(255,255,255,0.1); box-shadow:0 8px 32px rgba(0,0,0,0.5); }}
        #play-btn {{
            background:#2563eb; color:white; border:none; width:38px; height:38px;
            border-radius:50%; cursor:pointer; font-size:14px; display:flex;
            align-items:center; justify-content:center; flex-shrink:0;
            transition:all 0.2s ease; box-shadow:0 0 10px rgba(37,99,235,0.4);
        }}
        #play-btn:hover {{ background:#3b82f6; transform:scale(1.05); }}
        #play-btn.playing {{ background:#ef4444; box-shadow:0 0 10px rgba(239,68,68,0.4); }}
        
        .scrubber-container {{ flex:1; display:flex; flex-direction:column; gap:4px; }}
        .scrubber-labels {{ display:flex; justify-content:space-between; font-size:11px; color:#64748b; font-weight:700; }}
        #time-slider {{ width:100%; -webkit-appearance:none; height:6px; border-radius:3px; background:#334155; outline:none; cursor:pointer; }}
        #time-slider::-webkit-slider-thumb {{ -webkit-appearance:none; width:18px; height:18px; border-radius:50%; background:#38bdf8; cursor:pointer; box-shadow:0 0 8px #38bdf8; }}
        
        #time-display {{ background:#1e293b; padding:6px 14px; border-radius:20px; border:1px solid rgba(255,255,255,0.1);
                        font-size:13px; font-weight:700; color:#38bdf8; min-width:80px; text-align:center; }}

        #statsbar {{ position:absolute; top:70px; left:16px; z-index:1000; background:rgba(15, 23, 42, 0.75);
                    backdrop-filter:blur(8px); padding:8px 16px; border-radius:20px; color:white; font-size:12px; display:flex; gap:16px; border:1px solid rgba(255,255,255,0.08); }}
        .stat {{ color:#94a3b8; font-weight:500; }}
        .stat span {{ color:#38bdf8; font-weight:700; }}
    </style>
</head>
<body>

<div id="topbar">
    <h2>Weather<span>Map</span></h2>
    <select id="source-select" onchange="switchSource(this.value)">{source_options}</select>
    <div style="display:flex;gap:6px;flex-wrap:wrap">{var_buttons}</div>
</div>

<div id="map"></div>

<div id="statsbar">
    <div class="stat">Source: <span id="stat-source">{source}</span></div>
    <div class="stat">Variable: <span id="stat-var">{info['label']}</span></div>
    <div class="stat">Time: <span id="stat-hour">{initial_hour_str}</span></div>
</div>

<div id="legend">
    <h4 id="legend-title">{info['label']} ({info['unit']})</h4>
    <div id="legend-bar"></div>
    <div id="legend-labels">
        <span id="leg-min">{info['min']} {info['unit']}</span>
        <span id="leg-max">{info['max']} {info['unit']}</span>
    </div>
</div>

<div id="opacity-box">
    <label>Layer Opacity</label>
    <input type="range" min="0" max="100" value="82" oninput="setOpacity(this.value)"/>
    <span id="op-val" style="font-size:11px; font-weight:700; color:#38bdf8; margin-left:4px;">82%</span>
</div>

<div id="timebar">
    <button id="play-btn" onclick="togglePlay()" title="Play Timeline">▶</button>
    <div class="scrubber-container">
        <input type="range" id="time-slider" min="0" max="{max(0, len(hours)-1)}" value="0" step="1" oninput="onScrub(this.value)"/>
        <div class="scrubber-labels">
            <span>00:00</span>
            <span>06:00</span>
            <span>12:00</span>
            <span>18:00</span>
            <span>21:00</span>
        </div>
    </div>
    <div id="time-display">{initial_hour_str}</div>
</div>

<script>
    var curSource  = '{source}';
    var curVar     = '{variable}';
    var availableHours = {json.dumps(hours)};
    var hourIndex  = 0;
    var curHour    = availableHours[0] || 0;
    var varInfo    = {json.dumps(VAR_INFO)};
    var opacityVal = 0.82;
    var currentBucket = null;

    var activeLayerSlot = 'A';
    var layerA = null;
    var layerB = null;
    var isPlaying = false;
    var playTimer = null;

    var LAT_MIN = {LAT_MIN}, LAT_MAX = {LAT_MAX}, LON_MIN = {LON_MIN}, LON_MAX = {LON_MAX};
    var pakBounds = [[LAT_MIN, LON_MIN],[LAT_MAX, LON_MAX]];

    var map = L.map('map', {{ zoomControl: true, maxBounds: pakBounds, maxBoundsViscosity: 0.8 }});
    map.fitBounds(pakBounds);

    L.tileLayer('https://{{s}}.basemaps.cartocdn.com/dark_all/{{z}}/{{x}}/{{y}}{{r}}.png', {{
        attribution: '&copy; OpenStreetMap &copy; CartoDB', maxZoom: 18
    }}).addTo(map);

    function pickBucket(z) {{
        if (z <= 5) return 4;
        if (z <= 7) return 6;
        return 8;
    }}

    function getImageUrl(hour) {{
        var bucket = pickBucket(map.getZoom());
        return '/zoommap/' + curSource + '/' + curVar + '/' + bucket + '/' + hour;
    }}

    function transitionToHour(targetHour) {{
        curHour = targetHour;
        var url = getImageUrl(curHour);
        var nextSlot = (activeLayerSlot === 'A') ? 'B' : 'A';
        var currentLayer = (activeLayerSlot === 'A') ? layerA : layerB;
        var nextLayer    = (nextSlot === 'A') ? layerA : layerB;

        var img = new Image();
        img.onload = function() {{
            if (nextLayer && map.hasLayer(nextLayer)) map.removeLayer(nextLayer);
            nextLayer = L.imageOverlay(url, pakBounds, {{opacity: 0.0}}).addTo(map);

            if (nextSlot === 'A') layerA = nextLayer;
            else layerB = nextLayer;

            requestAnimationFrame(function() {{
                if (currentLayer) currentLayer.setOpacity(0.0);
                nextLayer.setOpacity(opacityVal);
            }});

            activeLayerSlot = nextSlot;
            
            setTimeout(function() {{
                if (currentLayer && map.hasLayer(currentLayer)) {{
                    map.removeLayer(currentLayer);
                }}
            }}, 350);
        }};
        img.src = url;

        updateTimeUI();
        updateStatsBar();
    }}

    function buildOverlay() {{
        currentBucket = pickBucket(map.getZoom());
        transitionToHour(curHour);
    }}

    buildOverlay();

    map.on('zoomend', function() {{
        var bucket = pickBucket(map.getZoom());
        if (bucket !== currentBucket) buildOverlay();
    }});

    function switchSource(src) {{
        curSource = src;
        window.location.href = '/map/' + curSource + '/' + curVar;
    }}

    function switchVariable(v) {{
        curVar = v;
        buildOverlay();
        updateLegend(v);
        updateButtons(v);
        updateStatsBar();
    }}

    function onScrub(idx) {{
        if (isPlaying) stopPlayback();
        hourIndex = parseInt(idx);
        transitionToHour(availableHours[hourIndex]);
    }}

    function togglePlay() {{
        if (isPlaying) stopPlayback();
        else startPlayback();
    }}

    function startPlayback() {{
        isPlaying = true;
        var btn = document.getElementById('play-btn');
        btn.innerText = '❚❚';
        btn.className = 'playing';

        playTimer = setInterval(function() {{
            hourIndex = (hourIndex + 1) % availableHours.length;
            document.getElementById('time-slider').value = hourIndex;
            transitionToHour(availableHours[hourIndex]);
        }}, 800);
    }}

    function stopPlayback() {{
        isPlaying = false;
        if (playTimer) clearInterval(playTimer);
        var btn = document.getElementById('play-btn');
        btn.innerText = '▶';
        btn.className = '';
    }}

    function updateTimeUI() {{
        var hourStr = String(curHour).padStart(2,'0') + ':00';
        document.getElementById('time-display').innerText = hourStr;
        document.getElementById('time-slider').value = hourIndex;
    }}

    function updateLegend(v) {{
        var info = varInfo[v];
        if (!info) return;
        document.getElementById('legend-title').innerText = info.label + ' (' + info.unit + ')';
        document.getElementById('legend-bar').style.background = 'linear-gradient(to right,' + info.cmap.join(',') + ')';
        document.getElementById('leg-min').innerText = info.min + ' ' + info.unit;
        document.getElementById('leg-max').innerText = info.max + ' ' + info.unit;
    }}

    function updateButtons(activeVar) {{
        document.querySelectorAll('.var-btn').forEach(function(btn) {{
            btn.className = 'var-btn' + (btn.id === 'vbtn-' + activeVar ? ' active' : '');
        }});
    }}

    function updateStatsBar() {{
        var info = varInfo[curVar];
        document.getElementById('stat-source').innerText = curSource;
        document.getElementById('stat-var').innerText    = info ? info.label : curVar;
        document.getElementById('stat-hour').innerText   = String(curHour).padStart(2,'0') + ':00';
    }}

    function setOpacity(val) {{
        opacityVal = val / 100;
        var activeLayer = (activeLayerSlot === 'A') ? layerA : layerB;
        if (activeLayer) activeLayer.setOpacity(opacityVal);
        document.getElementById('op-val').innerText = val + '%';
    }}
</script>
</body>
</html>"""
    return HTMLResponse(content=html)


@app.get("/compare/{source1}/{source2}/{variable}")
def compare_sources(source1: str, source2: str, variable: str):
    all_sources = get_all_sources() or [source1, source2]
    info   = VAR_INFO.get(variable, {'label': variable, 'unit': '', 'cmap': ['#000', '#fff'], 'min': 0, 'max': 100})
    colors = ', '.join(info['cmap'])

    left_opts  = '\n'.join([f'<option value="{s}" {"selected" if s==source1 else ""}>{s}</option>' for s in all_sources])
    right_opts = '\n'.join([f'<option value="{s}" {"selected" if s==source2 else ""}>{s}</option>' for s in all_sources])
    var_btns   = '\n'.join([
        f'<button onclick="switchVar(\'{v}\')" id="cvbtn-{v}" '
        f'style="background:{"#2563eb" if v==variable else "rgba(30,41,59,0.8)"};color:white;border:1px solid rgba(255,255,255,0.1);'
        f'padding:5px 12px;border-radius:20px;cursor:pointer;font-size:12px;font-weight:600;">{VAR_INFO[v]["label"]}</button>'
        for v in VAR_INFO
    ])

    html = f"""<!DOCTYPE html>
<html>
<head>
    <title>Compare: {source1} vs {source2}</title>
    <meta charset="utf-8" />
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <style>
        *{{box-sizing:border-box;margin:0;padding:0;}}
        body{{font-family:'Segoe UI',system-ui,sans-serif;background:#0b0f19;color:white;overflow:hidden;}}
        #topbar{{position:fixed;top:12px;left:12px;right:12px;z-index:2000;background:rgba(15,23,42,0.82);
            backdrop-filter:blur(12px);padding:10px 18px;display:flex;align-items:center;gap:12px;flex-wrap:wrap;
            border-radius:14px;border:1px solid rgba(255,255,255,0.1);}}
        #topbar h2{{color:#38bdf8;font-size:15px;white-space:nowrap;font-weight:800;}}
        select{{padding:5px 10px;border-radius:20px;border:1px solid rgba(255,255,255,0.15);background:#1e293b;color:white;font-size:12px;cursor:pointer;}}
        #maps-container{{display:flex;height:100vh;width:100vw;}}
        .map-wrapper{{flex:1;position:relative;}}
        .map-label{{position:absolute;top:70px;left:50%;transform:translateX(-50%);z-index:1000;
            background:rgba(15,23,42,0.85);backdrop-filter:blur(8px);color:white;padding:6px 16px;border-radius:20px;
            font-size:12px;font-weight:bold;white-space:nowrap;border:1px solid rgba(255,255,255,0.1);}}
        .map-div{{height:100%;width:100%;}}
        .divider{{width:2px;background:#38bdf8;z-index:1000;box-shadow:0 0 10px #38bdf8;}}
        #legend{{position:fixed;bottom:20px;right:20px;z-index:2000;background:rgba(15,23,42,0.85);
            backdrop-filter:blur(10px);padding:12px 16px;border-radius:12px;color:white;min-width:180px;border:1px solid rgba(255,255,255,0.1);}}
        #legend h4{{font-size:11px;color:#38bdf8;margin-bottom:6px;text-transform:uppercase;}}
        .lgbar{{height:10px;width:100%;border-radius:4px;background:linear-gradient(to right,{colors});margin-bottom:4px;}}
        .lgrow{{display:flex;justify-content:space-between;font-size:11px;color:#cbd5e1;}}
        .leaflet-image-layer {{ filter: blur(8px) contrast(110%); mix-blend-mode: screen; }}
    </style>
</head>
<body>
<div id="topbar">
    <h2>Model Compare</h2>
    <span style="color:#38bdf8;font-size:12px;font-weight:700;">LEFT:</span>
    <select id="lsel" onchange="switchLeft(this.value)">{left_opts}</select>
    <span style="color:#f87171;font-size:12px;font-weight:700;">RIGHT:</span>
    <select id="rsel" onchange="switchRight(this.value)">{right_opts}</select>
    <div style="display:flex;gap:6px;flex-wrap:wrap">{var_btns}</div>
</div>
<div id="maps-container">
    <div class="map-wrapper"><div class="map-label" style="color:#38bdf8" id="llabel">{source1}</div><div id="ml" class="map-div"></div></div>
    <div class="divider"></div>
    <div class="map-wrapper"><div class="map-label" style="color:#f87171" id="rlabel">{source2}</div><div id="mr" class="map-div"></div></div>
</div>
<div id="legend">
    <h4 id="ltitle">{info['label']} ({info['unit']})</h4>
    <div class="lgbar" id="lgbar"></div>
    <div class="lgrow"><span>{info['min']} {info['unit']}</span><span>{info['max']} {info['unit']}</span></div>
</div>
<script>
    var curVar='{variable}',curLeft='{source1}',curRight='{source2}';
    var bounds=[[{LAT_MIN},{LON_MIN}],[{LAT_MAX},{LON_MAX}]];
    var varInfo={json.dumps(VAR_INFO)};
    var tileOpts={{attribution:'&copy; OpenStreetMap &copy; CartoDB',maxZoom:18}};
    var mapL=L.map('ml',{{zoomControl:true, maxBounds: bounds, maxBoundsViscosity: 0.8}});
    var mapR=L.map('mr',{{zoomControl:false, maxBounds: bounds, maxBoundsViscosity: 0.8}});
    
    L.tileLayer('https://{{s}}.basemaps.cartocdn.com/dark_all/{{z}}/{{x}}/{{y}}{{r}}.png',tileOpts).addTo(mapL);
    L.tileLayer('https://{{s}}.basemaps.cartocdn.com/dark_all/{{z}}/{{x}}/{{y}}{{r}}.png',tileOpts).addTo(mapR);
    
    var ovL=L.imageOverlay('/fullmap/'+curLeft+'/'+curVar,bounds,{{opacity:0.75}}).addTo(mapL);
    var ovR=L.imageOverlay('/fullmap/'+curRight+'/'+curVar,bounds,{{opacity:0.75}}).addTo(mapR);

    mapL.fitBounds(bounds);
    mapR.fitBounds(bounds);

    var syncing=false;
    mapL.on('move',function(){{if(syncing)return;syncing=true;mapR.setView(mapL.getCenter(),mapL.getZoom(),{{animate:false}});syncing=false;}});
    mapR.on('move',function(){{if(syncing)return;syncing=true;mapL.setView(mapR.getCenter(),mapR.getZoom(),{{animate:false}});syncing=false;}});

    function switchLeft(s){{curLeft=s;ovL.setUrl('/fullmap/'+curLeft+'/'+curVar);document.getElementById('llabel').innerText=s;}}
    function switchRight(s){{curRight=s;ovR.setUrl('/fullmap/'+curRight+'/'+curVar);document.getElementById('rlabel').innerText=s;}}
    function switchVar(v){{
        curVar=v;
        ovL.setUrl('/fullmap/'+curLeft+'/'+v);
        ovR.setUrl('/fullmap/'+curRight+'/'+v);
        var info=varInfo[v];
        if(info){{
            document.getElementById('ltitle').innerText=info.label+' ('+info.unit+')';
            document.getElementById('lgbar').style.background='linear-gradient(to right,'+info.cmap.join(',')+')';
        }}
        document.querySelectorAll('[id^="cvbtn-"]').forEach(function(b){{ b.style.background=b.id==='cvbtn-'+v?'#2563eb':'rgba(30,41,59,0.8)'; }});
    }}
</script>
</body>
</html>"""
    return HTMLResponse(content=html)


@app.get("/query")
def query_point(lat: float = 33.6, lon: float = 73.0, source: str = "ECM_Global_HR", hour: int = 12):
    try:
        client, db = get_db()
        if source not in db.list_collection_names():
            raise HTTPException(status_code=404, detail=f"Source collection '{source}' not found.")

        collection = db[source]
        docs = list(collection.find({"forecast_hour": hour}, {"_id": 0}))
        if not docs:
            raise HTTPException(status_code=404, detail=f"No data for source '{source}' at hour {hour}")

        best_doc, best_dist = None, float('inf')
        for doc in docs:
            dist = ((doc['lat'] - lat) ** 2 + (doc['lon'] - lon) ** 2) ** 0.5
            if dist < best_dist:
                best_dist, best_doc = dist, doc

        client.close()
        return {
            "query": {"lat": lat, "lon": lon, "source": source, "hour": hour},
            "nearest_point": {"lat": best_doc['lat'], "lon": best_doc['lon']},
            "distance_degrees": round(best_dist, 4),
            "data": {k: best_doc.get(k) for k in
                     ['source_name', 'model', 'forecast_date', 'forecast_hour',
                      'avgtemp', 'mintemp', 'maxtemp', 'avghum', 'avgwind', 'pressure']}
        }
    except HTTPException:
        raise
    except Exception as e:
        log_error("api", "query_point", type(e).__name__, str(e), extra={"lat": lat, "lon": lon, "source": source})
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/errors")
def get_errors():
    errors  = get_all_errors()
    failed  = [e for e in errors if e.get('status') == 'failed']
    success = [e for e in errors if e.get('status') == 'success']
    return {"total_logged": len(errors), "total_failed": len(failed), "total_success": len(success), "errors": failed[:50]}


@app.get("/errors/today")
def get_errors_today():
    errors = get_today_errors()
    failed = [e for e in errors if e.get('status') == 'failed']
    return {"total_today": len(errors), "failed_today": len(failed), "errors": failed}