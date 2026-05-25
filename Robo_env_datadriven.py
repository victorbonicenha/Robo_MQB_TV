from playwright.sync_api import sync_playwright, TimeoutError
from time import sleep, time
from datetime import datetime
import sys
import subprocess
import requests
import json
import ssl
# ─────────────────────────────────────────
# CONFIG LOADER — API Datadriven
# ─────────────────────────────────────────
from requests.adapters import HTTPAdapter
from urllib3.poolmanager import PoolManager

class CustomSSLAdapter(HTTPAdapter):
    def __init__(self, **kwargs):
        self.ssl_context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE
        self.ssl_context.options |= 0x4
        self.ssl_context.set_ciphers('DEFAULT@SECLEVEL=1')
        self.ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2
        self.ssl_context.maximum_version = ssl.TLSVersion.TLSv1_2
        super().__init__(**kwargs)

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        self.poolmanager = PoolManager(
            num_pools=connections, maxsize=maxsize, block=block,
            ssl_context=self.ssl_context, **pool_kwargs
        )

def _get_session():
    session = requests.Session()
    session.mount("https://", CustomSSLAdapter())
    return session

def _get_token():
    url = "https://datadriven.datawake.com.br:8058/oauth/token"
    payload = "username=admin%40datawake.com.br&password=%25H%3F%401PA%26!zAD2&grant_type=password"
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": "Basic cGFyYW5vYTpQYXJhbm9hIyEyMDIy"
    }
    try:
        resp = _get_session().post(url, data=payload, headers=headers, verify=False)
        return resp.json()['access_token']
    except Exception as e:
        print(f"Erro ao obter token: {e}")
        raise

def _busca_env(token):
    key = '0AA74526-433C-4106-8408-9514B25A00C1'
    url = f'https://datadriven.datawake.com.br:8058/v1/technical_sheet/{key}/data'
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}"
    }
    try:
        resp = _get_session().get(url, headers=headers, verify=False)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"Erro ao buscar env: {e}")
        raise

def _parse_env(env_string):
    resultado = {}
    for linha in env_string.split('\r\n'):
        linha = linha.strip()
        if '=' in linha:
            chave, valor = linha.split('=', 1)
            resultado[chave.strip()] = valor.strip()
    return resultado

def _extract_envs(data):
    resultado = {}
    for record in data['content']:
        uniprod = None
        env_string = None
        for item in record['data']:
            attr = item['attributeModel']['attribute']
            valor = item['attributeValuesModel']['valueString']
            if attr == 'uniprod':
                uniprod = valor
            elif attr == 'env_tabelas':
                env_string = valor
        if uniprod and env_string:
            resultado[uniprod] = _parse_env(env_string)
    return dict(sorted(resultado.items()))

def carregar_envs() -> list:
    token = _get_token()
    data  = _busca_env(token)
    envs  = _extract_envs(data)
    lista = []
    for linha, config in envs.items():
        item = {"linha": linha}
        item.update(config)
        lista.append(item)
    return lista

# ─────────────────────────────────────────
# ROBÔ
# ─────────────────────────────────────────
LINHA_ALVO = sys.argv[1] if len(sys.argv) > 1 else "MQL001"
LINHAS = carregar_envs()

cfg = next((x for x in LINHAS if x["linha"] == LINHA_ALVO), None)
if cfg is None:
    print(f"Linha {LINHA_ALVO} não encontrada nas configs!")
    sys.exit(1)

login         = cfg["Login"]
senha         = cfg["senha"]
token_tg      = cfg["Telegram_Token"]
chat_id       = cfg["Telegram_Chat_ID"]
espera        = int(cfg["ESPERA_CARREGAMENTO_LINHAS_SEGUNDOS"])
espera_iframe = int(cfg["ESPERA_ENTRE_ACOES_IFRAME_SEGUNDOS"])
modo_att      = cfg["MODO_ATUALIZACAO"]
tempo_att     = int(cfg["TEMPO_ATUALIZACAO_SEGUNDOS"])

print(f"Iniciando {cfg['linha']}...")

def telegram(msg):
    url = f"https://api.telegram.org/bot{token_tg}/sendMessage"
    try:
        requests.post(url, data={"chat_id": chat_id, "text": msg}, timeout=10)
    except:
        pass

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

def clicar_menu(page, tentativas=3):
    for tentativa in range(tentativas):
        try:
            log(f"Tentando abrir menu ({tentativa+1}/{tentativas})")
            page.locator("header i").click(timeout=5000)
            return True
        except:
            sleep(2)
    log("Falha ao clicar no menu.")
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
    for tentativa in range(1, 3):
        try:
            iframe = page.frame_locator("#frameDash")
            iframe.locator("button:has(svg.animate-spin)").click(timeout=8000)
            sleep(espera_iframe)
            iframe.locator("button:has-text('Modo Tela Cheia')").click(timeout=8000)
            sleep(espera_iframe)
            iframe.locator("button:has(svg.lucide-x)").click(timeout=8000)
            sleep(espera_iframe)
            page.keyboard.press("F11")
            sleep(2)
            return
        except TimeoutError as te:
            log(f"Timeout iframe/F11 (tentativa {tentativa}/2): {te}")
            sleep(2)

def tentar_abrir_dashboard_com_retry(page, tentativas=2, motivo=""):
    ultimo_erro = None
    for tentativa in range(1, tentativas + 1):
        try:
            log(f"Abrindo DASHBOARD (tentativa {tentativa}/{tentativas})" + (f" - {motivo}" if motivo else ""))
            abrir_dashboard(page)
            page.wait_for_selector("#frameDash", timeout=30000)
            sleep(1)
            iframe = page.frame_locator("#frameDash")
            abrir_linha(iframe)
            interacoes_iniciais_iframe(page)
            return True
        except Exception as e:
            ultimo_erro = e
            log(f"Falha ao abrir DASHBOARD: {e}")
            try:
                page.reload()
                page.wait_for_load_state("networkidle", timeout=60000)
            except:
                pass
            sleep(2)

    telegram(
        f"Sistema fora do ar - linha {cfg['linha']}\n"
        f"Motivo: {motivo}\n"
        f"Erro: {str(ultimo_erro)}"
    )
    return False

def abrir_linha(iframe):
    log("Procurando linha...")
    sleep(espera)
    botoes = iframe.locator("text=Detalhes")
    botoes.first.wait_for(timeout=15000)
    sleep(2)
    count = botoes.count()
    log(f"Total de botões Detalhes: {count}")
    for i in range(count):
        botao = botoes.nth(i)
        container = botao.locator("xpath=ancestor::*[self::div or self::tr][1]")
        texto_linha = container.inner_text()
        if cfg["linha"] in texto_linha:
            log(f"Linha encontrada: {cfg['linha']} (índice {i})")
            botao.click()
            return

    telegram(f"Linha {cfg['linha']} não encontrada na lista do dashboard")
    raise Exception("Linha não encontrada")

def monitorar_dashboard(page):
    sucesso = tentar_abrir_dashboard_com_retry(page, tentativas=2, motivo="inicial")
    if not sucesso:
        raise Exception("Não foi possível abrir DASHBOARD após retries")

    log("Dashboard aberto. Aguardando ciclo de atualização...")

    while True:
        log(f"Aguardando {tempo_att}s até próximo reload...")
        sleep(tempo_att)

        log(f"Recarregando. Modo: {modo_att}")
        try:
            if modo_att == "F5":
                page.keyboard.press("F5")
            else:
                page.reload()
            page.wait_for_load_state("networkidle", timeout=60000)
        except Exception as e:
            log(f"Falha durante atualização: {e}")

        sucesso = tentar_abrir_dashboard_com_retry(
            page, tentativas=2,
            motivo=f"atualização periódica ({modo_att})"
        )
        if not sucesso:
            sleep(30)
            continue

        log("Ciclo de atualização concluído. Monitoramento retomado.")

def run(playwright):
    while True:
        try:
            log("Iniciando navegador")
            browser = playwright.chromium.launch(
                headless=False,
                args=["--start-maximized", "--start-fullscreen", "--kiosk"]
            )
            context = browser.new_context(no_viewport=True)
            page = context.new_page()
            log("Abrindo login")
            page.goto(
                "https://datadriven.datawake.com.br:8057/data-driven/login.html",
                timeout=30000
            )
            sleep(3)
            if sys.platform == "win32":
                import pyautogui
                pyautogui.press("f11")
            else:
                subprocess.run(["xdotool", "key", "F11"])
            sleep(3)
            page.get_by_role("textbox", name="Email:").fill(cfg["Login"])
            sleep(1)
            page.get_by_role("textbox", name="Senha").fill(cfg["senha"])
            sleep(1)
            page.get_by_role("button", name="Login").click()
            page.wait_for_load_state("networkidle", timeout=30000)
            sleep(3)
            log("Iniciando monitoramento do dashboard")
            monitorar_dashboard(page)

        except Exception as e:
            telegram(f"Robô linha {cfg['linha']} caiu\nErro: {str(e)}\nReiniciando em 10s...")
            log(f"Erro geral: {e}")
            try:
                browser.close()
            except:
                pass
            sleep(10)

if __name__ == "__main__":
    with sync_playwright() as playwright:
        run(playwright)
