from playwright.sync_api import sync_playwright, TimeoutError
from time import sleep, time
import os
import logging
import glob
import threading
import psutil
from datetime import datetime
from dotenv import load_dotenv
import requests
from flask import Flask, jsonify

load_dotenv()

TEMPO_ATUALIZACAO_SEGUNDOS = int(os.getenv("TEMPO_ATUALIZACAO_SEGUNDOS", 3600))
MODO_ATUALIZACAO = os.getenv("MODO_ATUALIZACAO", "F5").strip().upper()

# ─────────────────────────────────────────
# LOG
# ─────────────────────────────────────────
class CapturarLogsHandler(logging.Handler):
    def __init__(self, estado, lock, max_linhas=60):
        super().__init__()
        self._estado = estado
        self._lock = lock
        self._max = max_linhas

    def emit(self, record):
        linha = self.format(record)
        with self._lock:
            self._estado["logs_recentes"].append(linha)
            if len(self._estado["logs_recentes"]) > self._max:
                self._estado["logs_recentes"].pop(0)


def configurar_log(estado, lock):
    os.makedirs("logs", exist_ok=True)
    data_hoje = datetime.now().strftime("%Y-%m-%d")
    arquivo_log = f"logs/robo_{data_hoje}.log"

    fmt = logging.Formatter("[%(asctime)s] %(levelname)s - %(message)s", datefmt="%H:%M:%S")

    handler_arquivo = logging.FileHandler(arquivo_log, encoding="utf-8")
    handler_arquivo.setFormatter(fmt)

    handler_console = logging.StreamHandler()
    handler_console.setFormatter(fmt)

    handler_captura = CapturarLogsHandler(estado, lock)
    handler_captura.setFormatter(fmt)

    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)
    logger.addHandler(handler_arquivo)
    logger.addHandler(handler_console)
    logger.addHandler(handler_captura)
    return logger


def limpar_logs_antigos(dias=30):
    arquivos = glob.glob("logs/robo_*.log")
    limite = datetime.now().timestamp() - (dias * 86400)
    for arquivo in arquivos:
        if os.path.getmtime(arquivo) < limite:
            os.remove(arquivo)
            log(f"Log antigo removido: {arquivo}")

# ─────────────────────────────────────────
# ESTADO COMPARTILHADO
# ─────────────────────────────────────────
estado_lock = threading.Lock()
estado = {
    "status": "iniciando",
    "linha": "",
    "ultima_atualizacao": None,
    "ultimo_texto": None,
    "total_reloads": 0,
    "inicio": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
    "logs_recentes": [],
    "tempo_sem_mudar_min": 0,
    "proximo_reload_seg": TEMPO_ATUALIZACAO_SEGUNDOS,
    "modo_atualizacao": MODO_ATUALIZACAO,
    "ultimo_erro": None,
    "ultimo_reload_horario": None,
    "ultima_verificacao": None,
}

logger = configurar_log(estado, estado_lock)

def log(msg, nivel="info"):
    getattr(logger, nivel)(msg)

def atualizar_estado(**kwargs):
    with estado_lock:
        estado.update(kwargs)

# ─────────────────────────────────────────
# FLASK – INTERFACE WEB
# ─────────────────────────────────────────
app = Flask(__name__)
log_flask = logging.getLogger("werkzeug")
log_flask.setLevel(logging.ERROR)  # silencia logs do Flask no terminal

HTML_PAGINA = """<!DOCTYPE html>
<html lang="pt-br">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Monitor Robô MQB</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;900&display=swap');

  :root {
    --bg:        #080808;
    --surface:   #111111;
    --border:    #1e1e1e;
    --red:       #e8002d;
    --red-dim:   #2a0010;
    --green:     #00e676;
    --green-dim: #002a14;
    --yellow:    #ffc107;
    --yellow-dim:#251c00;
    --blue:      #38bdf8;
    --purple:    #a78bfa;
    --muted:     #444;
    --text:      #f0f0f0;
    --text-dim:  #666;
  }

  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Inter', 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; }

  /* ── HEADER ── */
  header {
    background: var(--surface);
    border-bottom: 2px solid var(--red);
    padding: 14px 28px;
    display: flex; align-items: center; justify-content: space-between;
    position: sticky; top: 0; z-index: 100;
    box-shadow: 0 2px 24px rgba(232,0,45,0.12);
  }
  .logo { display: flex; align-items: center; gap: 14px; }
  .logo-badge {
    background: var(--red); color: #fff; font-weight: 900; font-size: 0.8rem;
    padding: 5px 12px; border-radius: 4px; letter-spacing: 3px;
    box-shadow: 0 0 16px rgba(232,0,45,0.5);
  }
  .logo-title { font-size: 1rem; font-weight: 700; letter-spacing: 1px; }
  .logo-sub   { font-size: 0.65rem; color: var(--text-dim); margin-top: 2px; }

  .header-right { display: flex; align-items: center; gap: 22px; }
  .relogio {
    font-size: 1.5rem; font-weight: 900; font-variant-numeric: tabular-nums;
    letter-spacing: 3px; color: var(--red);
    text-shadow: 0 0 16px rgba(232,0,45,0.7);
  }
  .uptime-wrap { text-align: right; }
  .uptime-label { font-size: 0.58rem; text-transform: uppercase; letter-spacing: 1.5px; color: var(--text-dim); }
  .uptime-valor { font-size: 0.85rem; font-weight: 700; font-variant-numeric: tabular-nums; color: var(--green); margin-top: 1px; }
  .ping-wrap { display: flex; align-items: center; gap: 7px; font-size: 0.68rem; color: var(--text-dim); }
  .ping-dot {
    width: 9px; height: 9px; border-radius: 50%;
    background: var(--green); box-shadow: 0 0 8px var(--green);
    animation: pulse 1.8s infinite;
  }
  .ping-dot.off { background: var(--red); box-shadow: 0 0 8px var(--red); animation: none; }
  @keyframes pulse { 0%,100%{opacity:1;transform:scale(1)} 50%{opacity:.35;transform:scale(.75)} }

  /* ── MAIN ── */
  main { padding: 20px 28px; max-width: 1300px; margin: 0 auto; }

  /* ── BANNER ── */
  .banner {
    border-radius: 12px; padding: 18px 22px; margin-bottom: 20px;
    display: flex; align-items: center; gap: 16px;
    border: 1px solid transparent; transition: background .5s, border-color .5s, box-shadow .5s;
  }
  .banner.ok     { background: var(--green-dim);  border-color: var(--green);  box-shadow: 0 0 28px rgba(0,230,118,.1); }
  .banner.alerta { background: var(--yellow-dim); border-color: var(--yellow); box-shadow: 0 0 28px rgba(255,193,7,.1); }
  .banner.erro   { background: var(--red-dim);    border-color: var(--red);    box-shadow: 0 0 28px rgba(232,0,45,.15); }
  .banner-icon   { font-size: 2rem; flex-shrink: 0; }
  .banner-label  { font-size: 0.58rem; text-transform: uppercase; letter-spacing: 2.5px; color: var(--text-dim); }
  .banner-texto  { font-size: 1.05rem; font-weight: 600; margin-top: 3px; transition: color .4s; }
  .banner.ok     .banner-texto { color: var(--green); }
  .banner.alerta .banner-texto { color: var(--yellow); }
  .banner.erro   .banner-texto { color: var(--red); }

  /* ── GRID CARDS ── */
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 12px; margin-bottom: 20px; }
  .card {
    background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
    padding: 16px 18px; position: relative; overflow: hidden;
    transition: transform .2s, box-shadow .2s;
  }
  .card:hover { transform: translateY(-2px); box-shadow: 0 6px 22px rgba(0,0,0,.5); }
  .card::before {
    content: ''; position: absolute; top: 0; left: 0; right: 0; height: 2px;
    transition: background .4s;
  }
  .card.verde::before    { background: var(--green);  box-shadow: 0 0 7px var(--green); }
  .card.amarelo::before  { background: var(--yellow); box-shadow: 0 0 7px var(--yellow); }
  .card.vermelho::before { background: var(--red);    box-shadow: 0 0 7px var(--red); }
  .card.azul::before     { background: var(--blue);   box-shadow: 0 0 7px var(--blue); }
  .card.roxo::before     { background: var(--purple); box-shadow: 0 0 7px var(--purple); }
  .card.neutro::before   { background: #333; }

  .card-label { font-size: 0.58rem; text-transform: uppercase; letter-spacing: 1.5px; color: var(--text-dim); margin-bottom: 7px; }
  .card-valor { font-size: 1.25rem; font-weight: 700; color: var(--text); word-break: break-word; transition: color .4s; }
  .card.verde   .card-valor { color: var(--green); }
  .card.amarelo .card-valor { color: var(--yellow); }
  .card.azul    .card-valor { color: var(--blue); }
  .card.roxo    .card-valor { color: var(--purple); }
  .card-sub { font-size: 0.65rem; color: var(--text-dim); margin-top: 4px; }

  /* ── BARRA ── */
  .barra-wrap { margin-top: 10px; }
  .barra-topo { display: flex; justify-content: space-between; font-size: 0.6rem; color: var(--text-dim); margin-bottom: 4px; }
  .barra { height: 4px; background: #1e1e1e; border-radius: 2px; overflow: hidden; }
  .barra-fill { height: 100%; border-radius: 2px; transition: width .7s cubic-bezier(.4,0,.2,1), background .5s; }

  /* ── GRÁFICOS ── */
  .secao-titulo {
    font-size: 0.6rem; text-transform: uppercase; letter-spacing: 2px; color: var(--text-dim);
    border-bottom: 1px solid var(--border); padding-bottom: 10px; margin-bottom: 14px;
    display: flex; align-items: center; justify-content: space-between;
  }
  .secao-titulo-left { display: flex; align-items: center; gap: 8px; }
  .secao-titulo-left .acc { color: var(--red); }
  .badge-live {
    background: var(--red); color: #fff; font-size: 0.52rem; font-weight: 700;
    padding: 2px 6px; border-radius: 20px; letter-spacing: 1px;
    animation: blink 2s infinite;
  }
  @keyframes blink { 0%,100%{opacity:1} 50%{opacity:.45} }

  .graficos-grid { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px; margin-bottom: 20px; }
  @media(max-width:800px) { .graficos-grid { grid-template-columns: 1fr; } }

  .grafico-box {
    background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
    padding: 14px 16px; position: relative;
  }
  .grafico-box::before {
    content: ''; position: absolute; top: 0; left: 0; right: 0; height: 2px; border-radius: 10px 10px 0 0;
  }
  .grafico-box.g-cpu::before    { background: var(--red);    box-shadow: 0 0 8px var(--red); }
  .grafico-box.g-ram::before    { background: var(--blue);   box-shadow: 0 0 8px var(--blue); }
  .grafico-box.g-reload::before { background: var(--purple); box-shadow: 0 0 8px var(--purple); }

  .grafico-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
  .grafico-titulo { font-size: 0.58rem; text-transform: uppercase; letter-spacing: 1.5px; color: var(--text-dim); }
  .grafico-live   { font-size: 1.1rem; font-weight: 800; font-variant-numeric: tabular-nums; }
  .g-cpu  .grafico-live { color: var(--red); text-shadow: 0 0 10px rgba(232,0,45,.5); }
  .g-ram  .grafico-live { color: var(--blue); text-shadow: 0 0 10px rgba(56,189,248,.5); }
  .g-reload .grafico-live { color: var(--purple); text-shadow: 0 0 10px rgba(167,139,250,.5); }
  .grafico-canvas { height: 75px !important; }

  /* ── LOGS ── */
  .logs-box { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 16px 18px; }
  .logs-pre {
    font-family: 'Consolas', 'Courier New', monospace; font-size: 0.68rem; line-height: 1.75;
    white-space: pre-wrap; word-break: break-all; max-height: 300px; overflow-y: auto;
    color: #666; scrollbar-width: thin; scrollbar-color: #222 transparent;
  }
  .log-info    { color: #666; }
  .log-warning { color: var(--yellow); }
  .log-error   { color: var(--red); font-weight: 600; }

  /* ── FOOTER ── */
  footer {
    text-align: center; font-size: 0.6rem; color: var(--muted);
    padding: 16px; border-top: 1px solid var(--border); margin-top: 8px;
  }
  footer strong { color: var(--red); }
</style>
</head>
<body>

<header>
  <div class="logo">
    <div class="logo-badge">MQB</div>
    <div>
      <div class="logo-title">Monitor do Robô</div>
      <div class="logo-sub">Painel de acompanhamento em tempo real</div>
    </div>
  </div>
  <div class="header-right">
    <div class="uptime-wrap">
      <div class="uptime-label">Uptime</div>
      <div class="uptime-valor" id="uptime">—</div>
    </div>
    <div class="relogio" id="relogio">00:00:00</div>
    <div class="ping-wrap">
      <div class="ping-dot" id="pingDot"></div>
      <span id="pingLabel">conectando</span>
    </div>
  </div>
</header>

<main>
  <div class="banner alerta" id="banner">
    <div class="banner-icon" id="bannerIcon">⏳</div>
    <div>
      <div class="banner-label">Status atual</div>
      <div class="banner-texto" id="bannerTexto">Carregando...</div>
    </div>
    <div style="margin-left:auto;text-align:right;flex-shrink:0;">
      <div style="font-size:0.58rem;text-transform:uppercase;letter-spacing:1.5px;color:var(--text-dim)">Última verificação do robô</div>
      <div style="font-size:0.9rem;font-weight:700;color:var(--text);margin-top:2px;font-variant-numeric:tabular-nums;" id="ultimaVerif">—</div>
    </div>
  </div>

  <div class="grid" id="cards"></div>

  <!-- GRÁFICOS -->
  <div class="secao-titulo" style="margin-bottom:14px;">
    <div class="secao-titulo-left"><span class="acc">▍</span> Gráficos em tempo real <span class="badge-live">LIVE</span></div>
    <span id="graficoInfo" style="color:var(--text-dim)">últimos 30 pontos · 5s cada</span>
  </div>
  <div class="graficos-grid">
    <div class="grafico-box g-cpu">
      <div class="grafico-header">
        <div class="grafico-titulo">CPU — Histórico</div>
        <div class="grafico-live" id="cpuLive">—%</div>
      </div>
      <canvas id="chartCpu" class="grafico-canvas"></canvas>
    </div>
    <div class="grafico-box g-ram">
      <div class="grafico-header">
        <div class="grafico-titulo">RAM — Histórico</div>
        <div class="grafico-live" id="ramLive">—%</div>
      </div>
      <canvas id="chartRam" class="grafico-canvas"></canvas>
    </div>
    <div class="grafico-box g-reload">
      <div class="grafico-header">
        <div class="grafico-titulo">Próximo Reload</div>
        <div class="grafico-live" id="reloadLive">—</div>
      </div>
      <canvas id="chartReload" class="grafico-canvas"></canvas>
    </div>
  </div>

  <!-- LOGS -->
  <div class="secao-titulo">
    <div class="secao-titulo-left"><span class="acc">▍</span> Logs recentes <span class="badge-live">LIVE</span></div>
    <span id="qtdLogs" style="color:var(--text-dim)">0 linhas</span>
  </div>
  <div class="logs-box">
    <pre class="logs-pre" id="logs">Aguardando logs...</pre>
  </div>
</main>

<footer>
  Atualiza a cada 5s &nbsp;·&nbsp; <strong>Robô MQB</strong> &nbsp;·&nbsp; <span id="rodape">—</span>
</footer>

<script>
let TEMPO_ATUALIZACAO_SEG = 3600;
let inicioRobo = null;

// ── RELÓGIO ──────────────────────────────────────────────
function tickRelogio() {
  document.getElementById('relogio').textContent =
    new Date().toLocaleTimeString('pt-BR', {hour12: false});
}
tickRelogio();
setInterval(tickRelogio, 1000);

// ── UPTIME ───────────────────────────────────────────────
function calcUptime() {
  if (!inicioRobo) return '—';
  const diff = Math.floor((Date.now() - inicioRobo) / 1000);
  const h = String(Math.floor(diff / 3600)).padStart(2,'0');
  const m = String(Math.floor((diff % 3600) / 60)).padStart(2,'0');
  const s = String(diff % 60).padStart(2,'0');
  return h + ':' + m + ':' + s;
}
setInterval(() => { document.getElementById('uptime').textContent = calcUptime(); }, 1000);

// ── GRÁFICOS ─────────────────────────────────────────────
const MAX_PTS = 30;

function criarGrafico(id, cor, corFill) {
  const ctx = document.getElementById(id).getContext('2d');
  return new Chart(ctx, {
    type: 'line',
    data: {
      labels: Array(MAX_PTS).fill(''),
      datasets: [{ data: Array(MAX_PTS).fill(null), borderColor: cor, backgroundColor: corFill,
                   borderWidth: 2, pointRadius: 0, tension: 0.4, fill: true }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      animation: { duration: 350 },
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
      scales: {
        x: { display: false },
        y: { min: 0, max: 100, display: true,
          grid: { color: '#181818' },
          ticks: { color: '#444', font: { size: 9 }, callback: v => v+'%', maxTicksLimit: 3 },
          border: { display: false }
        }
      }
    }
  });
}

function criarGraficoReload(id, cor, corFill) {
  const ctx = document.getElementById(id).getContext('2d');
  return new Chart(ctx, {
    type: 'line',
    data: {
      labels: Array(MAX_PTS).fill(''),
      datasets: [{ data: Array(MAX_PTS).fill(null), borderColor: cor, backgroundColor: corFill,
                   borderWidth: 2, pointRadius: 0, tension: 0.4, fill: true }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      animation: { duration: 350 },
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
      scales: {
        x: { display: false },
        y: { min: 0, max: 100, display: true,
          grid: { color: '#181818' },
          ticks: { color: '#444', font: { size: 9 }, callback: v => v+'%', maxTicksLimit: 3 },
          border: { display: false }
        }
      }
    }
  });
}

const gCpu    = criarGrafico('chartCpu',    '#e8002d', 'rgba(232,0,45,0.12)');
const gRam    = criarGrafico('chartRam',    '#38bdf8', 'rgba(56,189,248,0.12)');
const gReload = criarGraficoReload('chartReload', '#a78bfa', 'rgba(167,139,250,0.12)');

function push(g, val) {
  g.data.datasets[0].data.push(val);
  g.data.labels.push('');
  if (g.data.datasets[0].data.length > MAX_PTS) {
    g.data.datasets[0].data.shift();
    g.data.labels.shift();
  }
  g.update('none');
}

// ── HELPERS ──────────────────────────────────────────────
function classeStatus(s) {
  if (!s) return 'neutro';
  const sl = s.toLowerCase();
  if (sl.includes('ok') || sl.includes('normal') || sl.includes('aberto') || sl.includes('sucesso')) return 'ok';
  if (sl.includes('reinici') || sl.includes('tentando') || sl.includes('abrindo') || sl.includes('iniciando') || sl.includes('login') || sl.includes('reload') || sl.includes('periódic')) return 'alerta';
  if (sl.includes('erro') || sl.includes('travou') || sl.includes('fora') || sl.includes('falha') || sl.includes('congelado')) return 'erro';
  return 'neutro';
}

const ICONES = { ok: '✅', alerta: '⚠️', erro: '❌', neutro: '🔄' };

function corPct(pct) { return pct > 80 ? 'vermelho' : pct > 50 ? 'amarelo' : 'verde'; }
function hexPct(pct) { return pct > 80 ? '#e8002d' : pct > 50 ? '#ffc107' : '#00e676'; }

function card(label, valor, sub='', cor='', extra='') {
  return `<div class="card ${cor}">
    <div class="card-label">${label}</div>
    <div class="card-valor">${valor}</div>
    ${sub ? `<div class="card-sub">${sub}</div>` : '<div class="card-sub"></div>'}
    ${extra}
  </div>`;
}

function barra(pct, cor) {
  return `<div class="barra-wrap">
    <div class="barra-topo"><span>USO</span><span>${pct}%</span></div>
    <div class="barra"><div class="barra-fill" style="width:${pct}%;background:${cor}"></div></div>
  </div>`;
}

// ── LOOP ─────────────────────────────────────────────────
async function atualizar() {
  try {
    const r = await fetch('/api/status');
    const d = await r.json();
    if (d.tempo_atualizacao_seg) TEMPO_ATUALIZACAO_SEG = d.tempo_atualizacao_seg;

    // Uptime init
    if (!inicioRobo && d.inicio) {
      const p = d.inicio.split(/[\\/: ]/);
      inicioRobo = new Date(p[2], p[1]-1, p[0], p[3]||0, p[4]||0, p[5]||0).getTime();
    }

    // Banner
    const cls = classeStatus(d.status);
    document.getElementById('banner').className = 'banner ' + (cls === 'neutro' ? 'alerta' : cls);
    document.getElementById('bannerTexto').textContent = d.status || '—';
    document.getElementById('bannerIcon').textContent = ICONES[cls] || '🔄';
    document.getElementById('pingDot').className = 'ping-dot' + (cls === 'erro' ? ' off' : '');
    document.getElementById('pingLabel').textContent = cls === 'erro' ? 'erro' : 'online';
    document.getElementById('ultimaVerif').textContent = d.ultima_verificacao || '—';

    const cpu = d.sistema.cpu_pct;
    const ram = d.sistema.ram_pct;

    // Countdown reload
    const prox = d.proximo_reload_seg || 0;
    const pMin = String(Math.floor(prox / 60)).padStart(2,'0');
    const pSeg = String(prox % 60).padStart(2,'0');
    const proxPct = TEMPO_ATUALIZACAO_SEG > 0 ? Math.round((1 - prox / TEMPO_ATUALIZACAO_SEG) * 100) : 0;
    const reloadBarExtra = `<div class="barra-wrap">
      <div class="barra-topo"><span>PROGRESSO</span><span>${proxPct}%</span></div>
      <div class="barra"><div class="barra-fill" style="width:${proxPct}%;background:#a78bfa"></div></div>
    </div>`;

    // Sem atualizar
    const semMudar = d.tempo_sem_mudar_min || 0;
    const semCor = semMudar >= 8 ? 'vermelho' : semMudar >= 4 ? 'amarelo' : 'verde';

    // Último erro
    const erroCor = d.ultimo_erro ? 'vermelho' : 'verde';

    // Cards
    document.getElementById('cards').innerHTML =
      card('Linha monitorada',    d.linha || '—',                         '',                                                   'vermelho') +
      card('Última detecção',     d.ultimo_texto || '—',                  d.ultima_atualizacao ? 'em ' + d.ultima_atualizacao : '') +
      card('Modo de atualização', d.modo_atualizacao || '—',              'próximo em ' + pMin + ':' + pSeg,                   'azul', reloadBarExtra) +
      card('Reloads realizados',  String(d.total_reloads),                d.ultimo_reload_horario ? 'último: ' + d.ultimo_reload_horario : 'nenhum ainda', 'roxo') +
      card('Sem atualizar',       semMudar + ' min',                      semMudar === 0 ? 'atualizando normalmente' : 'parado', semCor) +
      card('Último erro',         d.ultimo_erro || 'Nenhum',              '',                                                   erroCor) +
      card('Robô iniciado',       d.inicio || '—',                        '') +
      card('CPU do sistema',      cpu + '%',                              '',                                                   corPct(cpu), barra(cpu, hexPct(cpu))) +
      card('Memória RAM',         d.sistema.ram_usado_mb + ' MB',        d.sistema.ram_total_mb + ' MB total',                 corPct(ram), barra(ram, hexPct(ram)));

    // Gráficos
    push(gCpu, cpu);
    push(gRam, ram);
    push(gReload, proxPct);
    document.getElementById('cpuLive').textContent    = cpu + '%';
    document.getElementById('ramLive').textContent    = ram + '%';
    document.getElementById('reloadLive').textContent = pMin + ':' + pSeg;

    // Logs
    const logsEl = document.getElementById('logs');
    logsEl.innerHTML = d.logs_recentes.map(l => {
      if (l.includes('ERROR'))   return `<span class="log-error">${l}</span>`;
      if (l.includes('WARNING')) return `<span class="log-warning">${l}</span>`;
      return `<span class="log-info">${l}</span>`;
    }).join('\\n');
    logsEl.scrollTop = logsEl.scrollHeight;
    document.getElementById('qtdLogs').textContent = d.logs_recentes.length + ' linhas';

    const agora = new Date().toLocaleTimeString('pt-BR');
    document.getElementById('rodape').textContent = 'última sync: ' + agora;
  } catch(e) {
    document.getElementById('pingDot').className = 'ping-dot off';
    document.getElementById('pingLabel').textContent = 'offline';
    document.getElementById('rodape').textContent = 'sem conexão';
  }
}

atualizar();
setInterval(atualizar, 5000);
</script>
</body>
</html>"""

@app.route("/")
def pagina():
    return HTML_PAGINA

@app.route("/api/status")
def api_status():
    proc = psutil.Process()
    mem_sys = psutil.virtual_memory()
    with estado_lock:
        dados_estado = dict(estado)
        dados_estado["logs_recentes"] = list(estado["logs_recentes"])
    dados_estado["sistema"] = {
        "cpu_pct": psutil.cpu_percent(interval=None),
        "ram_pct": round(mem_sys.percent, 1),
        "ram_usado_mb": round(mem_sys.used / 1024 / 1024),
        "ram_total_mb": round(mem_sys.total / 1024 / 1024),
    }
    dados_estado["tempo_atualizacao_seg"] = TEMPO_ATUALIZACAO_SEGUNDOS
    return jsonify(dados_estado)

def iniciar_servidor_web(porta=5000):
    app.run(host="0.0.0.0", port=porta, debug=False, use_reloader=False)

# ─────────────────────────────────────────
# TELEGRAM
# ─────────────────────────────────────────
def telegram(msg):
    token = os.getenv("Telegram_Token")
    chat_id = os.getenv("Telegram_Chat_ID")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": msg}
    try:
        requests.post(url, data=payload, timeout=10)
    except:
        pass

# ─────────────────────────────────────────
# CREDENCIAIS E CONFIG
# ─────────────────────────────────────────
def credenciais():
    return {
        "login": os.getenv("Login"),
        "senha": os.getenv("senha"),
        "linha": os.getenv("Nome_linha")
    }

dados = credenciais()

TEMPO_ATUALIZACAO_SEGUNDOS = int(os.getenv("TEMPO_ATUALIZACAO_SEGUNDOS", "3600"))
MODO_ATUALIZACAO = os.getenv("MODO_ATUALIZACAO", "F5").strip().upper()
ESPERA_CARREGAMENTO_LINHAS_SEGUNDOS = int(os.getenv("ESPERA_CARREGAMENTO_LINHAS_SEGUNDOS", "5"))
ESPERA_ENTRE_ACOES_IFRAME_SEGUNDOS = int(os.getenv("ESPERA_ENTRE_ACOES_IFRAME_SEGUNDOS", "2"))

# ─────────────────────────────────────────
# FUNÇÕES
# ─────────────────────────────────────────
def clicar_menu(page, tentativas=3):
    for tentativa in range(tentativas):
        try:
            log(f"Tentando abrir menu ({tentativa+1}/{tentativas})")
            page.locator("header i").click(timeout=5000)
            return True
        except:
            sleep(2)

    log("Falha ao clicar no menu.", nivel="warning")
    return False

def abrir_dashboard(page):
    if not clicar_menu(page):
        raise Exception("Menu não abriu")

    sleep(1)
    page.get_by_role("link", name="DASHBOARD ").click()
    sleep(1)
    page.get_by_role("link", name="MANUFATURA ").click()
    sleep(1)
    page.evaluate("""
        loadPageNew('dash.html', 'DASH', 'pageContent',
        'https://datadriven.datawake.com.br:8091/',
        'frameDash', 'OEE-Online');
    """)
    sleep(2)
    clicar_menu(page)


def interacoes_iniciais_iframe(page):
    for tentativa in range(1, 4):
        try:
            iframe = page.frame_locator("#frameDash")
            iframe.locator("button:has(svg.animate-spin)").click(timeout=8000)
            sleep(ESPERA_ENTRE_ACOES_IFRAME_SEGUNDOS + 1)

            iframe.locator("button:has-text('Modo Tela Cheia')").click(timeout=8000)
            sleep(ESPERA_ENTRE_ACOES_IFRAME_SEGUNDOS)

            iframe.locator("button:has(svg.lucide-x)").click(timeout=8000)
            sleep(ESPERA_ENTRE_ACOES_IFRAME_SEGUNDOS + 1)

            page.keyboard.press("F11")
            sleep(2)
            return
        except TimeoutError as te:
            log(f"Timeout iframe (tentativa {tentativa}/2): {te}", nivel="warning")
            sleep(2)


def tentar_abrir_dashboard_com_retry(page, tentativas=3, motivo=""):
    ultimo_erro = None

    for tentativa in range(1, tentativas + 1):
        try:
            log(
                f"Abrindo DASHBOARD (tentativa {tentativa}/{tentativas})"
                + (f" - {motivo}" if motivo else "")
            )
            atualizar_estado(status=f"abrindo dashboard ({motivo})")
            abrir_dashboard(page)
            page.wait_for_selector("#frameDash", timeout=30000)
            sleep(1)

            iframe = page.frame_locator("#frameDash")
            abrir_linha(iframe)
            interacoes_iniciais_iframe(page)
            atualizar_estado(status="ok - dashboard aberto e monitorando")
            return True

        except Exception as e:
            ultimo_erro = e
            log(f"Falha ao abrir DASHBOARD: {e}", nivel="error")
            atualizar_estado(status=f"erro ao abrir dashboard: {e}")
            try:
                page.reload()
                page.wait_for_load_state("networkidle", timeout=60000)
            except:
                pass
            sleep(2)

    telegram(
        "Sistema fora do ar: não foi possível abrir o DASHBOARD "
        f"(linha {dados['linha']}). Motivo: {motivo}. Erro: {str(ultimo_erro)}"
    )
    return False


def abrir_linha(iframe):
    log("Procurando linha...")
    sleep(ESPERA_CARREGAMENTO_LINHAS_SEGUNDOS)

    botoes = iframe.locator("text=Detalhes")
    botoes.first.wait_for(timeout=15000)
    sleep(2)

    count = botoes.count()
    log(f"Total de botões Detalhes: {count}")

    for i in range(count):
        botao = botoes.nth(i)
        container = botao.locator("xpath=ancestor::*[self::div or self::tr][1]")
        texto_linha = container.inner_text()

        if dados["linha"] in texto_linha:
            log(f"Linha encontrada: {dados['linha']} (índice {i})")
            botao.click()
            return

    telegram(f"Linha {dados['linha']} não encontrada")
    raise Exception("Linha não encontrada")


def monitorar_dashboard(page):
    sucesso = tentar_abrir_dashboard_com_retry(page, tentativas=2, motivo="inicial")
    if not sucesso:
        raise Exception("Não foi possível abrir DASHBOARD após retries")

    iframe = page.frame_locator("#frameDash")
    log("Dashboard aberto")
    atualizar_estado(status="dashboard aberto - monitorando")
    telegram(f"Dashboard da linha {dados['linha']} aberto com sucesso")

    # Desativa animações/transições CSS para reduzir consumo de CPU
    css_sem_animacao = "*, *::before, *::after { animation: none !important; transition: none !important; }"
    page.add_style_tag(content=css_sem_animacao)
    frame_obj = page.frame(name="frameDash")
    if frame_obj:
        frame_obj.add_style_tag(content=css_sem_animacao)

    ultima_hora = None
    tempo_sem_mudar = 0
    ultimo_reload = time()

    while True:
        try:
            texto = iframe.get_by_role("button").filter(
                has_text="Última Atualização"
            ).first.inner_text()

            log(f"Detectado: {texto}")

            if ultima_hora is None:
                ultima_hora = texto
            elif texto == ultima_hora:
                tempo_sem_mudar += 1
                log(f"Tempo sem atualizar: {tempo_sem_mudar * 2} minutos", nivel="warning")
                atualizar_estado(
                    status=f"sem atualizar há {tempo_sem_mudar * 2} min",
                    tempo_sem_mudar_min=tempo_sem_mudar * 2,
                )
                if tempo_sem_mudar >= 5:
                    raise TimeoutError("Dashboard congelado")
            else:
                ultima_hora = texto
                tempo_sem_mudar = 0
                log("Dashboard atualizou normalmente")
                atualizar_estado(
                    status="ok - atualizando normalmente",
                    ultimo_texto=texto,
                    ultima_atualizacao=datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
                    tempo_sem_mudar_min=0,
                )

            tempo_decorrido = time() - ultimo_reload
            atualizar_estado(proximo_reload_seg=max(0, int(TEMPO_ATUALIZACAO_SEGUNDOS - tempo_decorrido)))

            if tempo_decorrido > TEMPO_ATUALIZACAO_SEGUNDOS:
                log(f"Tempo de atualização atingido. Modo: {MODO_ATUALIZACAO}")
                atualizar_estado(status=f"reload periódico ({MODO_ATUALIZACAO})")
                try:
                    if MODO_ATUALIZACAO == "F5":
                        page.keyboard.press("F5")
                    else:
                        page.reload()
                    page.wait_for_load_state("networkidle", timeout=60000)
                except Exception as e:
                    log(f"Falha durante atualização: {e}", nivel="error")

                with estado_lock:
                    estado["total_reloads"] += 1
                    estado["ultimo_reload_horario"] = datetime.now().strftime("%H:%M:%S")

                sucesso = tentar_abrir_dashboard_com_retry(
                    page, tentativas=2,
                    motivo=f"atualização periódica ({MODO_ATUALIZACAO})"
                )

                if not sucesso:
                    ultima_hora = None
                    tempo_sem_mudar = 0
                    ultimo_reload = time()
                    sleep(30)
                    continue

                iframe = page.frame_locator("#frameDash")
                ultima_hora = None
                tempo_sem_mudar = 0
                ultimo_reload = time()

        except TimeoutError:
            log("Dashboard travou. Reiniciando...", nivel="error")
            atualizar_estado(status="dashboard travou - reiniciando", ultimo_erro=datetime.now().strftime("%H:%M:%S") + " — Dashboard congelado")
            telegram(f"Dashboard da linha {dados['linha']} travou. Reiniciando.")

            page.reload()
            page.wait_for_load_state("networkidle", timeout=60000)

            with estado_lock:
                estado["total_reloads"] += 1

            sucesso = tentar_abrir_dashboard_com_retry(
                page, tentativas=2, motivo="recuperação pós-travamento"
            )
            if not sucesso:
                raise TimeoutError("DASHBOARD fora do ar após recuperação")

            iframe = page.frame_locator("#frameDash")
            ultima_hora = None
            tempo_sem_mudar = 0
            ultimo_reload = time()

        atualizar_estado(ultima_verificacao=datetime.now().strftime("%H:%M:%S"))
        sleep(120)


# ─────────────────────────────────────────
# EXECUÇÃO
# ─────────────────────────────────────────
def run(playwright):
    limpar_logs_antigos(dias=30)
    atualizar_estado(linha=dados["linha"])

    while True:
        try:
            log("Iniciando navegador")
            atualizar_estado(status="iniciando navegador")
            telegram(f"Robô da linha {dados['linha']} iniciado")

            browser = playwright.chromium.launch(
                headless=False,
                args=[
                    "--start-maximized", "--start-fullscreen", "--kiosk",
                    "--disable-dev-shm-usage",
                    "--disable-extensions",
                    "--disable-background-networking",
                    "--disable-sync",
                    "--disable-translate",
                    "--no-first-run",
                    "--disable-background-timer-throttling",
                    "--disable-renderer-backgrounding",
                    "--disable-backgrounding-occluded-windows",
                    "--disable-ipc-flooding-protection",
                    "--js-flags=--max-old-space-size=512",
                ]
            )

            context = browser.new_context(no_viewport=True)
            page = context.new_page()

            log("Abrindo login")
            atualizar_estado(status="fazendo login")
            page.goto(
                "https://datadriven.datawake.com.br:8057/data-driven/login.html",
                timeout=30000,
                wait_until="domcontentloaded"
            )
            sleep(1)
            sleep(1)

            page.get_by_role("textbox", name="Email:").wait_for(timeout=10000)
            page.get_by_role("textbox", name="Email:").fill(dados["login"])
            page.get_by_role("textbox", name="Senha").fill(dados["senha"])
            page.get_by_role("button", name="Login").click()
            page.wait_for_load_state("networkidle", timeout=30000)

            log("Iniciando monitoramento do dashboard")
            monitorar_dashboard(page)

        except Exception as e:
            log(f"Erro geral: {e}", nivel="error")
            atualizar_estado(status="erro - reiniciando em 10s", ultimo_erro=datetime.now().strftime("%H:%M:%S") + f" — {str(e)[:60]}")
            telegram(f"Robô da linha {dados['linha']} reiniciando\nErro: {str(e)}")

            try:
                browser.close()
            except:
                pass

            log("Reiniciando robô em 10 segundos")
            sleep(10)


if __name__ == "__main__":
    PORTA_WEB = int(os.getenv("PORTA_WEB", "5000"))

    thread_web = threading.Thread(
        target=iniciar_servidor_web,
        args=(PORTA_WEB,),
        daemon=True
    )
    thread_web.start()
    log(f"Interface web disponível em http://localhost:{PORTA_WEB} (ou pelo IP da máquina na rede)")

    with sync_playwright() as playwright:
        run(playwright)
