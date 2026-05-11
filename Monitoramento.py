"""
Monitor de Sistema — CPU, RAM, Disco, Rede
Acesso local:  http://localhost:5001
Acesso externo via ngrok (configure NGROK_TOKEN no .env)
"""

import os
import json
import threading
import time
from collections import deque
from datetime import datetime
from dotenv import load_dotenv
import psutil
from flask import Flask, jsonify

load_dotenv()

# ─────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────
PORTA              = int(os.getenv("MONITOR_PORTA", "5001"))
NGROK_TOKEN        = os.getenv("NGROK_TOKEN", "").strip()
INTERVALO_COLETA   = int(os.getenv("MONITOR_INTERVALO", "3"))
MAX_HISTORICO      = int(os.getenv("MONITOR_HISTORICO", "120"))
LIMIAR_PICO_CPU    = int(os.getenv("LIMIAR_PICO_CPU", "80"))
LIMIAR_PICO_RAM    = int(os.getenv("LIMIAR_PICO_RAM", "85"))
ARQUIVO_PICOS      = "monitor_picos.json"

# ─────────────────────────────────────────
# ESTADO
# ─────────────────────────────────────────
lock = threading.Lock()

historico_cpu    = deque(maxlen=MAX_HISTORICO)
historico_ram    = deque(maxlen=MAX_HISTORICO)
historico_labels = deque(maxlen=MAX_HISTORICO)

picos_cpu = []
picos_ram = []

ultimo_net_io   = psutil.net_io_counters()
ultimo_net_time = time.time()

# ─────────────────────────────────────────
# PERSISTÊNCIA DE PICOS
# ─────────────────────────────────────────
def carregar_picos():
    global picos_cpu, picos_ram
    if os.path.exists(ARQUIVO_PICOS):
        try:
            with open(ARQUIVO_PICOS, "r", encoding="utf-8") as f:
                dados = json.load(f)
                picos_cpu = dados.get("cpu", [])[-50:]
                picos_ram = dados.get("ram", [])[-50:]
        except:
            pass

def salvar_picos():
    try:
        with open(ARQUIVO_PICOS, "w", encoding="utf-8") as f:
            json.dump({"cpu": picos_cpu[-50:], "ram": picos_ram[-50:]}, f, ensure_ascii=False)
    except:
        pass

# ─────────────────────────────────────────
# COLETA DE DADOS
# ─────────────────────────────────────────
def coletar():
    global ultimo_net_io, ultimo_net_time
    carregar_picos()

    while True:
        label = datetime.now().strftime("%H:%M:%S")
        cpu   = psutil.cpu_percent(interval=None)
        mem   = psutil.virtual_memory()
        ram   = round(mem.percent, 1)

        with lock:
            historico_labels.append(label)
            historico_cpu.append(cpu)
            historico_ram.append(ram)

            if cpu >= LIMIAR_PICO_CPU:
                picos_cpu.append({"horario": label, "valor": cpu})
                if len(picos_cpu) > 50:
                    picos_cpu.pop(0)
                salvar_picos()

            if ram >= LIMIAR_PICO_RAM:
                picos_ram.append({"horario": label, "valor": ram})
                if len(picos_ram) > 50:
                    picos_ram.pop(0)
                salvar_picos()

        time.sleep(INTERVALO_COLETA)

# ─────────────────────────────────────────
# FLASK
# ─────────────────────────────────────────
app = Flask(__name__)
import logging
logging.getLogger("werkzeug").setLevel(logging.ERROR)

@app.route("/api/stats")
def api_stats():
    mem  = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    net  = psutil.net_io_counters()

    with lock:
        hist_labels = list(historico_labels)
        hist_cpu    = list(historico_cpu)
        hist_ram    = list(historico_ram)
        p_cpu = list(picos_cpu[-20:])
        p_ram = list(picos_ram[-20:])

    return jsonify({
        "atual": {
            "cpu":             psutil.cpu_percent(interval=None),
            "cpu_nucleos":     psutil.cpu_count(),
            "ram_pct":         round(mem.percent, 1),
            "ram_usado_mb":    round(mem.used   / 1024**2),
            "ram_total_mb":    round(mem.total  / 1024**2),
            "disco_pct":       round(disk.percent, 1),
            "disco_usado_gb":  round(disk.used  / 1024**3, 1),
            "disco_total_gb":  round(disk.total / 1024**3, 1),
            "rede_enviado_mb":  round(net.bytes_sent / 1024**2, 1),
            "rede_recebido_mb": round(net.bytes_recv / 1024**2, 1),
        },
        "historico": {
            "labels": hist_labels,
            "cpu":    hist_cpu,
            "ram":    hist_ram,
        },
        "picos": {
            "cpu":        p_cpu,
            "ram":        p_ram,
            "limiar_cpu": LIMIAR_PICO_CPU,
            "limiar_ram": LIMIAR_PICO_RAM,
        },
        "config": { "intervalo_seg": INTERVALO_COLETA }
    })

@app.route("/api/limpar_picos", methods=["POST"])
def limpar_picos():
    global picos_cpu, picos_ram
    with lock:
        picos_cpu.clear()
        picos_ram.clear()
    salvar_picos()
    return jsonify({"ok": True})

@app.route("/")
def pagina():
    return HTML

# ─────────────────────────────────────────
# HTML
# ─────────────────────────────────────────
HTML = """<!DOCTYPE html>
<html lang="pt-br">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0,maximum-scale=1.0">
<title>Monitor do Sistema</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;900&display=swap');
:root{
  --bg:#070707;--surface:#0f0f0f;--card:#131313;--border:#1a1a1a;
  --red:#e8002d;--green:#00e676;--blue:#38bdf8;--yellow:#ffc107;
  --purple:#a78bfa;--orange:#fb923c;--text:#f0f0f0;--dim:#4a4a4a;
}
*{box-sizing:border-box;margin:0;padding:0;}
body{font-family:'Inter',sans-serif;background:var(--bg);color:var(--text);min-height:100vh;overflow-x:hidden;}

/* HEADER */
header{background:var(--surface);border-bottom:2px solid var(--red);padding:12px 20px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:100;box-shadow:0 2px 24px rgba(232,0,45,.18);}
.logo{display:flex;align-items:center;gap:12px;}
.badge{background:var(--red);color:#fff;font-weight:900;font-size:.68rem;padding:5px 11px;border-radius:4px;letter-spacing:2px;box-shadow:0 0 18px rgba(232,0,45,.55);}
.logo-info h1{font-size:.9rem;font-weight:700;letter-spacing:.5px;}
.logo-info p{font-size:.58rem;color:var(--dim);margin-top:2px;}
.hright{display:flex;align-items:center;gap:18px;}
.relogio{font-size:1.35rem;font-weight:900;letter-spacing:3px;color:var(--red);text-shadow:0 0 18px rgba(232,0,45,.7);font-variant-numeric:tabular-nums;}
.ping{display:flex;align-items:center;gap:6px;font-size:.6rem;color:var(--dim);}
.dot{width:8px;height:8px;border-radius:50%;background:var(--green);box-shadow:0 0 8px var(--green);animation:pulse 1.8s infinite;}
.dot.off{background:var(--red);box-shadow:0 0 8px var(--red);animation:none;}
@keyframes pulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.25;transform:scale(.65)}}

main{padding:14px 16px;max-width:1120px;margin:0 auto;}

/* SEÇÃO */
.sec{font-size:.56rem;text-transform:uppercase;letter-spacing:2px;color:var(--dim);border-bottom:1px solid var(--border);padding-bottom:8px;margin-bottom:13px;display:flex;align-items:center;justify-content:space-between;}
.sec-l{display:flex;align-items:center;gap:8px;}
.acc{color:var(--red);}
.live-badge{background:var(--red);color:#fff;font-size:.48rem;font-weight:700;padding:2px 6px;border-radius:20px;letter-spacing:1px;animation:blink 2s infinite;}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.35}}

/* GAUGES CIRCULARES */
.gauges-row{display:grid;grid-template-columns:repeat(auto-fill,minmax(185px,1fr));gap:14px;margin-bottom:16px;}
.gauge-wrap{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:20px 16px;text-align:center;position:relative;overflow:hidden;transition:transform .2s,box-shadow .2s;}
.gauge-wrap:hover{transform:translateY(-3px);box-shadow:0 8px 28px rgba(0,0,0,.5);}
.gauge-wrap::before{content:'';position:absolute;top:0;left:0;right:0;height:2px;}
.gw-cpu::before{background:var(--red);box-shadow:0 0 9px var(--red);}
.gw-ram::before{background:var(--blue);box-shadow:0 0 9px var(--blue);}
.gw-disco::before{background:var(--orange);box-shadow:0 0 9px var(--orange);}
.gw-netr::before{background:var(--green);box-shadow:0 0 9px var(--green);}
.gw-nete::before{background:var(--purple);box-shadow:0 0 9px var(--purple);}

.gauge-canvas-wrap{position:relative;width:130px;height:130px;margin:0 auto 10px;}
.gauge-center{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;padding-bottom:14px;pointer-events:none;}
.gauge-pct{font-size:1.9rem;font-weight:900;font-variant-numeric:tabular-nums;line-height:1;text-shadow:0 0 12px currentColor,0 0 24px currentColor;letter-spacing:-1px;}
.gauge-unit{font-size:.58rem;color:var(--dim);letter-spacing:1px;margin-top:3px;}
.gauge-pct{color:var(--green);}
.gw-netr .gauge-pct{color:var(--green);}
.gw-nete .gauge-pct{color:var(--purple);}

.gauge-label{font-size:.62rem;text-transform:uppercase;letter-spacing:1.5px;color:var(--dim);margin-bottom:10px;}
.gauge-sub{font-size:.65rem;color:var(--dim);line-height:1.5;margin-top:2px;}

/* SPARKLINES (mini gráfico abaixo de cada gauge) */
.spark-wrap{margin-top:8px;height:28px;}
.spark-wrap canvas{height:28px !important;}

/* GRÁFICOS GRANDES */
.graficos{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:16px;}
@media(max-width:580px){.graficos{grid-template-columns:1fr;}}
.gb{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:14px 16px;position:relative;}
.gb::before{content:'';position:absolute;top:0;left:0;right:0;height:2px;border-radius:14px 14px 0 0;}
.gb-cpu::before{background:var(--red);box-shadow:0 0 9px var(--red);}
.gb-ram::before{background:var(--blue);box-shadow:0 0 9px var(--blue);}
.gb-disco::before{background:var(--orange);box-shadow:0 0 9px var(--orange);}
.gh{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;}
.gh-t{font-size:.56rem;text-transform:uppercase;letter-spacing:1.5px;color:var(--dim);}
.gh-v{font-size:1.1rem;font-weight:800;font-variant-numeric:tabular-nums;}
.gb-cpu .gh-v{color:var(--red);text-shadow:0 0 12px rgba(232,0,45,.5);}
.gb-ram .gh-v{color:var(--blue);text-shadow:0 0 12px rgba(56,189,248,.5);}
.gb-disco .gh-v{color:var(--orange);text-shadow:0 0 12px rgba(251,146,60,.5);}
.chart-main{height:100px !important;}

/* PICOS */
.picos-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:14px;}
@media(max-width:580px){.picos-grid{grid-template-columns:1fr;}}
.pb{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:14px 16px;position:relative;}
.pb::before{content:'';position:absolute;top:0;left:0;right:0;height:2px;border-radius:14px 14px 0 0;}
.pb-cpu::before{background:var(--red);box-shadow:0 0 9px var(--red);}
.pb-ram::before{background:var(--blue);box-shadow:0 0 9px var(--blue);}
.pb-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;}
.pb-titulo{font-size:.56rem;text-transform:uppercase;letter-spacing:1.5px;color:var(--dim);}
.btn-limpar{background:none;border:1px solid #292929;color:#484848;font-size:.55rem;padding:3px 9px;border-radius:4px;cursor:pointer;transition:all .2s;font-family:inherit;}
.btn-limpar:hover{border-color:var(--red);color:var(--red);}
.pico-max{background:#181818;border-radius:8px;padding:8px 12px;margin-bottom:9px;display:flex;justify-content:space-between;align-items:center;}
.pm-label{font-size:.58rem;color:var(--dim);}
.pm-val{font-weight:800;font-size:.88rem;}
.pb-cpu .pm-val{color:var(--red);}
.pb-ram .pm-val{color:var(--blue);}
.picos-lista{max-height:170px;overflow-y:auto;scrollbar-width:thin;scrollbar-color:#1e1e1e transparent;}
.pico-item{display:flex;justify-content:space-between;align-items:center;padding:6px 8px;border-radius:7px;margin-bottom:3px;font-size:.68rem;animation:fadeIn .3s ease;}
@keyframes fadeIn{from{opacity:0;transform:translateX(-6px)}to{opacity:1;transform:translateX(0)}}
.pico-item:hover{background:#1c1c1c;}
.ph{color:var(--dim);font-variant-numeric:tabular-nums;}
.pv-cpu{color:var(--red);font-weight:700;}
.pv-ram{color:var(--blue);font-weight:700;}
.pico-empty{font-size:.65rem;color:var(--dim);text-align:center;padding:22px 0;}

footer{text-align:center;font-size:.56rem;color:var(--dim);padding:13px;border-top:1px solid var(--border);margin-top:6px;}
footer strong{color:var(--red);}
</style>
</head>
<body>
<header>
  <div class="logo">
    <div class="badge">SYS</div>
    <div class="logo-info">
      <h1>Monitor do Sistema</h1>
      <p>CPU &middot; RAM &middot; Disco &middot; Rede &mdash; tempo real</p>
    </div>
  </div>
  <div class="hright">
    <div class="relogio" id="relogio">00:00:00</div>
    <div class="ping"><div class="dot" id="dot"></div><span id="dotLabel">conectando</span></div>
  </div>
</header>

<main>
  <!-- GAUGES -->
  <div class="sec" style="margin-bottom:13px;">
    <div class="sec-l"><span class="acc">&#9616;</span>&nbsp;Indicadores &mdash; agora <span class="live-badge">LIVE</span></div>
    <span id="syncLabel">&mdash;</span>
  </div>
  <div class="gauges-row">
    <div class="gauge-wrap gw-cpu">
      <div class="gauge-label">CPU</div>
      <div class="gauge-canvas-wrap">
        <canvas id="arcCpu"></canvas>
        <div class="gauge-center"><div class="gauge-pct" id="arcCpuVal">&mdash;</div><div class="gauge-unit">%</div></div>
      </div>
      <div class="gauge-sub" id="subCpu">&mdash; n&uacute;cleos</div>
      <div class="spark-wrap"><canvas id="spkCpu"></canvas></div>
    </div>
    <div class="gauge-wrap gw-ram">
      <div class="gauge-label">RAM</div>
      <div class="gauge-canvas-wrap">
        <canvas id="arcRam"></canvas>
        <div class="gauge-center"><div class="gauge-pct" id="arcRamVal">&mdash;</div><div class="gauge-unit">%</div></div>
      </div>
      <div class="gauge-sub" id="subRam">&mdash; / &mdash; MB</div>
      <div class="spark-wrap"><canvas id="spkRam"></canvas></div>
    </div>
    <div class="gauge-wrap gw-disco">
      <div class="gauge-label">Disco</div>
      <div class="gauge-canvas-wrap">
        <canvas id="arcDisco"></canvas>
        <div class="gauge-center"><div class="gauge-pct" id="arcDiscoVal">&mdash;</div><div class="gauge-unit">%</div></div>
      </div>
      <div class="gauge-sub" id="subDisco">&mdash; / &mdash; GB</div>
      <div class="spark-wrap"><canvas id="spkDisco"></canvas></div>
    </div>
    <div class="gauge-wrap gw-netr">
      <div class="gauge-label">Rede &#8595; recebido</div>
      <div style="padding:22px 0 8px;font-size:2rem;font-weight:900;color:var(--green);font-variant-numeric:tabular-nums;" id="gNetR">&mdash;</div>
      <div class="gauge-sub">MB desde o boot</div>
      <div class="spark-wrap"><canvas id="spkNetR"></canvas></div>
    </div>
    <div class="gauge-wrap gw-nete">
      <div class="gauge-label">Rede &#8593; enviado</div>
      <div style="padding:22px 0 8px;font-size:2rem;font-weight:900;color:var(--purple);font-variant-numeric:tabular-nums;" id="gNetE">&mdash;</div>
      <div class="gauge-sub">MB desde o boot</div>
      <div class="spark-wrap"><canvas id="spkNetE"></canvas></div>
    </div>
  </div>

  <!-- GRÁFICOS PRINCIPAIS -->
  <div class="sec"><div class="sec-l"><span class="acc">&#9616;</span>&nbsp;Gr&aacute;ficos hist&oacute;ricos <span class="live-badge">LIVE</span></div></div>
  <div class="graficos">
    <div class="gb gb-cpu">
      <div class="gh"><div class="gh-t">CPU &mdash; hist&oacute;rico</div><div class="gh-v" id="cpuLive">&mdash;%</div></div>
      <canvas id="chartCpu" class="chart-main"></canvas>
    </div>
    <div class="gb gb-ram">
      <div class="gh"><div class="gh-t">RAM &mdash; hist&oacute;rico</div><div class="gh-v" id="ramLive">&mdash;%</div></div>
      <canvas id="chartRam" class="chart-main"></canvas>
    </div>
    <div class="gb gb-disco" style="grid-column:1/-1">
      <div class="gh"><div class="gh-t">Disco &mdash; uso ao longo do tempo</div><div class="gh-v" id="discoLive" style="color:var(--orange);text-shadow:0 0 12px rgba(251,146,60,.5)">&mdash;%</div></div>
      <canvas id="chartDisco" class="chart-main"></canvas>
    </div>
  </div>

  <!-- PICOS -->
  <div class="sec">
    <div class="sec-l"><span class="acc">&#9616;</span>&nbsp;Hist&oacute;rico de picos &mdash; salvo em disco</div>
    <button class="btn-limpar" onclick="limparPicos()">Limpar tudo</button>
  </div>
  <div class="picos-grid">
    <div class="pb pb-cpu">
      <div class="pb-header">
        <div class="pb-titulo" id="titCpu">CPU &mdash; picos</div>
      </div>
      <div class="pico-max"><span class="pm-label">Maior pico</span><span class="pm-val" id="maxCpu">&mdash;</span></div>
      <div class="picos-lista" id="listaCpu"><div class="pico-empty">Nenhum pico ainda</div></div>
    </div>
    <div class="pb pb-ram">
      <div class="pb-header">
        <div class="pb-titulo" id="titRam">RAM &mdash; picos</div>
      </div>
      <div class="pico-max"><span class="pm-label">Maior pico</span><span class="pm-val" id="maxRam">&mdash;</span></div>
      <div class="picos-lista" id="listaRam"><div class="pico-empty">Nenhum pico ainda</div></div>
    </div>
  </div>
</main>

<footer>
  Atualiza a cada <strong id="intLabel">3</strong>s &nbsp;&middot;&nbsp; <strong>Monitor do Sistema</strong> &nbsp;&middot;&nbsp; <span id="rodape">&mdash;</span>
</footer>

<script>
// ── RELÓGIO ──────────────────────────────────────────
setInterval(()=>{
  document.getElementById('relogio').textContent=new Date().toLocaleTimeString('pt-BR',{hour12:false});
},1000);

// ── CORES ────────────────────────────────────────────
const C={red:'#e8002d',blue:'#38bdf8',orange:'#fb923c',green:'#00e676',purple:'#a78bfa',bg:'#131313',border:'#1a1a1a'};
function corCpu(v){return v>80?C.red:v>50?'#ffc107':C.green;}
function corRam(v){return v>85?C.red:v>60?'#ffc107':C.blue;}
function corDisco(v){return v>90?C.red:v>70?'#ffc107':C.orange;}

// ── GAUGE ARCO (doughnut) ────────────────────────────
function criarArc(id, cor){
  return new Chart(document.getElementById(id).getContext('2d'),{
    type:'doughnut',
    data:{datasets:[{
      data:[0,100],
      backgroundColor:[cor,C.border],
      borderWidth:0,
      borderRadius:4,
    }]},
    options:{
      responsive:true,maintainAspectRatio:true,cutout:'72%',
      animation:{duration:600,easing:'easeInOutQuart'},
      plugins:{legend:{display:false},tooltip:{enabled:false}},
      rotation:-110,circumference:220,
    }
  });
}

function atualizarArc(arc, pct, cor){
  arc.data.datasets[0].data=[pct,100-pct];
  arc.data.datasets[0].backgroundColor=[cor,C.border];
  arc.update();
}

// ── SPARKLINE ───────────────────────────────────────
function criarSpark(id, cor){
  return new Chart(document.getElementById(id).getContext('2d'),{
    type:'line',
    data:{labels:[],datasets:[{data:[],borderColor:cor,backgroundColor:cor+'20',borderWidth:1.5,pointRadius:0,tension:0.4,fill:true}]},
    options:{
      responsive:true,maintainAspectRatio:false,animation:{duration:200},
      plugins:{legend:{display:false},tooltip:{enabled:false}},
      scales:{x:{display:false},y:{display:false,min:0,max:100}}
    }
  });
}

function pushSpark(sp, val){
  sp.data.labels.push('');
  sp.data.datasets[0].data.push(val);
  if(sp.data.labels.length>30){sp.data.labels.shift();sp.data.datasets[0].data.shift();}
  sp.update('none');
}

// ── GRÁFICO PRINCIPAL ────────────────────────────────
function criarGrafico(id, cor){
  return new Chart(document.getElementById(id).getContext('2d'),{
    type:'line',
    data:{labels:[],datasets:[{data:[],borderColor:cor,backgroundColor:cor+'18',borderWidth:2,pointRadius:0,tension:0.4,fill:true}]},
    options:{
      responsive:true,maintainAspectRatio:false,animation:{duration:350},
      plugins:{legend:{display:false},tooltip:{enabled:true,callbacks:{label:c=>' '+c.raw+'%'}}},
      scales:{
        x:{display:false},
        y:{min:0,max:100,display:true,grid:{color:'#161616'},
          ticks:{color:'#3a3a3a',font:{size:9},callback:v=>v+'%',maxTicksLimit:4},border:{display:false}}
      }
    }
  });
}

function pushG(g,label,val){
  g.data.labels.push(label);g.data.datasets[0].data.push(val);
  if(g.data.labels.length>120){g.data.labels.shift();g.data.datasets[0].data.shift();}
  g.update('none');
}

// Instâncias
const arcCpu=criarArc('arcCpu',C.red);
const arcRam=criarArc('arcRam',C.blue);
const arcDisco=criarArc('arcDisco',C.orange);

const spkCpu=criarSpark('spkCpu',C.red);
const spkRam=criarSpark('spkRam',C.blue);
const spkDisco=criarSpark('spkDisco',C.orange);
const spkNetR=criarSpark('spkNetR',C.green);
const spkNetE=criarSpark('spkNetE',C.purple);

const gCpu=criarGrafico('chartCpu',C.red);
const gRam=criarGrafico('chartRam',C.blue);
const gDisco=criarGrafico('chartDisco',C.orange);

// ── NÚMERO ANIMADO ───────────────────────────────────
function animNum(el, destino, sufixo=''){
  const atual=parseFloat(el.dataset.val||0)||0;
  const diff=destino-atual;
  const steps=8;let step=0;
  clearInterval(el._interval);
  el.dataset.val=destino;
  el._interval=setInterval(()=>{
    step++;
    const v=atual+diff*(step/steps);
    el.textContent=v.toFixed(1)+sufixo;
    if(step>=steps){el.textContent=destino+sufixo;clearInterval(el._interval);}
  },30);
}

// ── PICOS ────────────────────────────────────────────
function renderPicos(lista,cid,tipo){
  const el=document.getElementById(cid);
  if(!lista||!lista.length){el.innerHTML='<div class="pico-empty">Nenhum pico registrado ainda</div>';return null;}
  const cls=tipo==='cpu'?'pv-cpu':'pv-ram';
  el.innerHTML=[...lista].reverse().map(p=>
    `<div class="pico-item"><span class="ph">${p.horario}</span><span class="${cls}">${p.valor.toFixed(1)}%</span></div>`
  ).join('');
  return Math.max(...lista.map(p=>p.valor));
}

async function limparPicos(){
  await fetch('/api/limpar_picos',{method:'POST'});
  atualizar();
}

// ── HISTÓRICO ────────────────────────────────────────
let historicoCarregado=false;
let netRPrev=null,netEPrev=null;

async function atualizar(){
  try{
    const r=await fetch('/api/stats');const d=await r.json();const a=d.atual;
    document.getElementById('dot').className='dot';
    document.getElementById('dotLabel').textContent='online';
    document.getElementById('intLabel').textContent=d.config.intervalo_seg;

    // Arcos + números animados com cor dinâmica
    const cCpu=corCpu(a.cpu),cRam=corRam(a.ram_pct),cDisco=corDisco(a.disco_pct);
    atualizarArc(arcCpu,a.cpu,cCpu);
    atualizarArc(arcRam,a.ram_pct,cRam);
    atualizarArc(arcDisco,a.disco_pct,cDisco);

    const eArcCpu=document.getElementById('arcCpuVal');
    const eArcRam=document.getElementById('arcRamVal');
    const eArcDisco=document.getElementById('arcDiscoVal');
    eArcCpu.style.color=cCpu;
    eArcRam.style.color=cRam;
    eArcDisco.style.color=cDisco;
    animNum(eArcCpu,a.cpu);
    animNum(eArcRam,a.ram_pct);
    animNum(eArcDisco,a.disco_pct);

    document.getElementById('subCpu').textContent=a.cpu_nucleos+' n\xFAcleos l\xF3gicos';
    document.getElementById('subRam').textContent=a.ram_usado_mb+' / '+a.ram_total_mb+' MB';
    document.getElementById('subDisco').textContent=a.disco_usado_gb+' / '+a.disco_total_gb+' GB';

    // Rede (mostra delta se possível)
    const netR=a.rede_recebido_mb,netE=a.rede_enviado_mb;
    document.getElementById('gNetR').textContent=netR+' MB';
    document.getElementById('gNetE').textContent=netE+' MB';
    const deltaR=netRPrev!==null?Math.max(0,netR-netRPrev):0;
    const deltaE=netEPrev!==null?Math.max(0,netE-netEPrev):0;
    netRPrev=netR;netEPrev=netE;

    // Sparklines
    pushSpark(spkCpu,a.cpu);
    pushSpark(spkRam,a.ram_pct);
    pushSpark(spkDisco,a.disco_pct);
    pushSpark(spkNetR,Math.min(100,deltaR));
    pushSpark(spkNetE,Math.min(100,deltaE));

    // Gráficos grandes
    const h=d.historico;
    if(!historicoCarregado&&h.labels.length>0){
      gCpu.data.labels=[...h.labels];gCpu.data.datasets[0].data=[...h.cpu];gCpu.update('none');
      gRam.data.labels=[...h.labels];gRam.data.datasets[0].data=[...h.ram];gRam.update('none');
      historicoCarregado=true;
    } else if(historicoCarregado&&h.labels.length>0){
      const ul=h.labels[h.labels.length-1];
      if(ul!==gCpu.data.labels[gCpu.data.labels.length-1]){
        pushG(gCpu,ul,h.cpu[h.cpu.length-1]);
        pushG(gRam,ul,h.ram[h.ram.length-1]);
      }
    }
    pushG(gDisco,new Date().toLocaleTimeString('pt-BR',{hour12:false}),a.disco_pct);

    document.getElementById('cpuLive').textContent=a.cpu+'%';
    document.getElementById('ramLive').textContent=a.ram_pct+'%';
    document.getElementById('discoLive').textContent=a.disco_pct+'%';

    // Picos
    const p=d.picos;
    document.getElementById('titCpu').textContent='CPU \u2014 picos acima de '+p.limiar_cpu+'%';
    document.getElementById('titRam').textContent='RAM \u2014 picos acima de '+p.limiar_ram+'%';
    const mCpu=renderPicos(p.cpu,'listaCpu','cpu');
    const mRam=renderPicos(p.ram,'listaRam','ram');
    document.getElementById('maxCpu').textContent=mCpu!==null?mCpu.toFixed(1)+'%':'\u2014';
    document.getElementById('maxRam').textContent=mRam!==null?mRam.toFixed(1)+'%':'\u2014';

    const ag=new Date().toLocaleTimeString('pt-BR');
    document.getElementById('syncLabel').textContent='sync: '+ag;
    document.getElementById('rodape').textContent=ag;
  }catch(e){
    document.getElementById('dot').className='dot off';
    document.getElementById('dotLabel').textContent='offline';
  }
}

atualizar();
setInterval(atualizar,3000);
</script>
</body>
</html>"""

# ─────────────────────────────────────────
# NGROK
# ─────────────────────────────────────────
def iniciar_ngrok(porta, token):
    try:
        from pyngrok import ngrok, conf as ngrok_conf
        if token:
            ngrok_conf.get_default().auth_token = token
        tunnel = ngrok.connect(porta, "http")
        print(f"\n  [NGROK] Acesso externo: {tunnel.public_url}\n")
    except Exception as e:
        print(f"  [NGROK] Falha: {e}")

# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────
if __name__ == "__main__":
    print(f"""
  Monitor do Sistema iniciando...
  Local  : http://localhost:{PORTA}
  Rede   : http://SEU_IP:{PORTA}
  Coleta : a cada {INTERVALO_COLETA}s
  Pico CPU: >{LIMIAR_PICO_CPU}%  |  Pico RAM: >{LIMIAR_PICO_RAM}%
    """)

    threading.Thread(target=coletar, daemon=True).start()

    if NGROK_TOKEN:
        threading.Thread(target=iniciar_ngrok, args=(PORTA, NGROK_TOKEN), daemon=True).start()
    else:
        print("  [INFO] NGROK_TOKEN ausente no .env — acesso apenas na rede local\n")

    app.run(host="0.0.0.0", port=PORTA, debug=False, use_reloader=False)
