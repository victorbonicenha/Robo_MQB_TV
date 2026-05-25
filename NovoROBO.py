from playwright.sync_api import sync_playwright, TimeoutError
from time import sleep, time
import os
from datetime import datetime
from teste_env import carregar_envs
import sys
import subprocess
import requests

LINHA_ALVO = sys.argv[1] if len(sys.argv) > 1 else "MQL001"
LINHAS = carregar_envs()

cfg = next((x for x in LINHAS if x["linha"] == LINHA_ALVO), None)
if cfg is None:
    print(f"Linha {LINHA_ALVO} não encontrada nas configs!")
    sys.exit(1)

# Todas as variáveis disponíveis via cfg:
login      = cfg["Login"]
senha      = cfg["senha"]
token_tg   = cfg["Telegram_Token"]
chat_id    = cfg["Telegram_Chat_ID"]
espera     = int(cfg["ESPERA_CARREGAMENTO_LINHAS_SEGUNDOS"])
espera_iframe = int(cfg["ESPERA_ENTRE_ACOES_IFRAME_SEGUNDOS"])
modo_att   = cfg["MODO_ATUALIZACAO"]
tempo_att  = int(cfg["TEMPO_ATUALIZACAO_SEGUNDOS"])

print(f"Iniciando {cfg['linha']}...")

def telegram(msg):
    token = os.getenv("Telegram_Token")
    chat_id = os.getenv("Telegram_Chat_ID")

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    payload = {
        "chat_id": chat_id,
        "text": msg
    }

    try:
        requests.post(url, data=payload, timeout=10)
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
    telegram(f"Falha ao abrir menu - Linha {cfg['linha']}")
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
            sleep(tempo_att + 1)

            iframe.locator("button:has-text('Modo Tela Cheia')").click(timeout=8000)
            sleep(tempo_att + 1)

            iframe.locator("button:has(svg.lucide-x)").click(timeout=8000)
            sleep(tempo_att + 1)

            page.keyboard.press("F11")
            sleep(2)
            return
        except TimeoutError as te:
            log(f"Timeout ao interagir com o iframe/F11 (tentativa {tentativa}/2): {te}")
            sleep(2)


def tentar_abrir_dashboard_com_retry(page, tentativas=2, motivo=""):
    ultimo_erro = None

    for tentativa in range(1, tentativas + 1):
        try:
            log(
                f"Abrindo DASHBOARD (tentativa {tentativa}/{tentativas})"
                + (f" - {motivo}" if motivo else "")
            )

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
        "Sistema fora do ar: não foi possível abrir o DASHBOARD "
        f"(linha {cfg['linha']}). Motivo: {motivo}. Erro: {str(ultimo_erro)}"
    )
    return False

def abrir_linha(iframe):
    log("Procurando linha...")
    sleep(tempo_att)

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

    telegram(f"Linha {cfg['linha']} não encontrada")
    raise Exception("Linha não encontrada")

def monitorar_dashboard(page):
    sucesso = tentar_abrir_dashboard_com_retry(page, tentativas=2, motivo="inicial")
    if not sucesso:
        raise Exception("Não foi possível abrir DASHBOARD após retries")

    iframe = page.frame_locator("#frameDash")

    log("Dashboard aberto")
    telegram(f"Dashboard da linha {cfg['linha']} aberto com sucesso")

    ultima_hora = None
    tempo_sem_mudar = 0
    ultimo_reload = time()

    while True:
        try:
            # -----------------------------
            # VERIFICA ÚLTIMA ATUALIZAÇÃO
            # -----------------------------
            texto = iframe.get_by_role("button").filter(
                has_text="Última Atualização"
            ).first.inner_text()

            log(f"Detectado: {texto}")

            if ultima_hora is None:
                ultima_hora = texto

            elif texto == ultima_hora:
                tempo_sem_mudar += 1
                log(f"Tempo sem atualizar: {tempo_sem_mudar * 2} minutos")

                if tempo_sem_mudar >= 5:
                    raise TimeoutError("Dashboard congelado")

            else:
                ultima_hora = texto
                tempo_sem_mudar = 0
                log("Dashboard atualizou normalmente")

            # -----------------------------
            # RELOAD A CADA TEMPO CONFIGURADO
            # -----------------------------
            if time() - ultimo_reload > tempo_att:
                log(f"Tempo de atualização atingido ({tempo_att}s). Modo: {modo_att}")

                try:
                    if modo_att == "F5":
                        page.keyboard.press("F5")
                    else:
                        page.reload()

                    page.wait_for_load_state("networkidle", timeout=60000)
                except Exception as e:
                    log(f"Falha durante acionamento da atualização: {e}")

                # Repete TODO o fluxo: clicar_menu → dashboard → linha → interações iframe
                # F11 NÃO é chamado aqui (apenas no início da sessão do browser)
                sucesso = tentar_abrir_dashboard_com_retry(
                    page,
                    tentativas=2,
                    motivo=f"atualização periódica ({modo_att})",
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

                log("Ciclo de atualização concluído. Monitoramento retomado.")
                telegram(f"Dashboard da linha {cfg['linha']} recarregado com sucesso.")

        except TimeoutError:
            log("Dashboard travou. Reiniciando...")
            telegram(f"Dashboard da linha {cfg['linha']} travou por mais de 10 minutos. Reiniciando.")

            page.reload()
            page.wait_for_load_state("networkidle", timeout=60000)

            sucesso = tentar_abrir_dashboard_com_retry(
                page,
                tentativas=2,
                motivo="recuperação pós-travamento",
            )
            if not sucesso:
                raise TimeoutError("DASHBOARD fora do ar após recuperação")

            iframe = page.frame_locator("#frameDash")
            ultima_hora = None
            tempo_sem_mudar = 0
            ultimo_reload = time()

        sleep(120)

def run(playwright):
    while True:
        try:
            log("Iniciando navegador")
            telegram(f"Robô da linha {cfg['linha']} iniciado")
            browser = playwright.chromium.launch(
                headless=False,
                args=[
                    "--start-maximized",
                    "--start-fullscreen",
                    "--kiosk"
                ]
            )
            context = browser.new_context(no_viewport=True)
            page = context.new_page()
            log("Abrindo login")
            page.goto(
                "https://datadriven.datawake.com.br:8057/data-driven/login.html",
                timeout=30000
            )
            sleep(3)

            # Windows usa pyautogui, Linux usa xdotool
            if sys.platform == "win32":
                import pyautogui
                pyautogui.press("f11")
            else:
                subprocess.run(["xdotool", "key", "F11"])

            sleep(3)
            page.get_by_role("textbox", name="Email:").fill(cfg["Login"])   # L maiúsculo
            sleep(1)
            page.get_by_role("textbox", name="Senha").fill(cfg["senha"])
            sleep(1)
            page.get_by_role("button", name="Login").click()
            page.wait_for_load_state("networkidle", timeout=30000)
            sleep(3)
            log("Iniciando monitoramento do dashboard")
            monitorar_dashboard(page)
        except Exception as e:
            log(f"Erro geral: {e}")
            telegram(f"Robô da linha {cfg['linha']} reiniciando\nErro: {str(e)}")
            try:
                browser.close()
            except:
                pass
            log("Reiniciando robô em 10 segundos")
            sleep(10)

if __name__ == "__main__":
    with sync_playwright() as playwright:
        run(playwright)
