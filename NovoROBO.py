from playwright.sync_api import sync_playwright, TimeoutError
from time import sleep, time
import os
from datetime import datetime
from dotenv import load_dotenv
import pyautogui
import requests

load_dotenv()


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


def credenciais():
    return {
        "login": os.getenv("Login"),
        "senha": os.getenv("senha"),
        "linha": os.getenv("Nome_linha")
    }


dados = credenciais()

TEMPO_ATUALIZACAO_SEGUNDOS = int(os.getenv("TEMPO_ATUALIZACAO_SEGUNDOS", "3600"))
MODO_ATUALIZACAO = os.getenv("MODO_ATUALIZACAO", "F5").strip().upper()  # "F5" ou "REINICIAR"
ESPERA_CARREGAMENTO_LINHAS_SEGUNDOS = int(os.getenv("ESPERA_CARREGAMENTO_LINHAS_SEGUNDOS"))
ESPERA_ENTRE_ACOES_IFRAME_SEGUNDOS = int(os.getenv("ESPERA_ENTRE_ACOES_IFRAME_SEGUNDOS"))


def clicar_menu(page, tentativas=3):

    for tentativa in range(tentativas):
        try:
            log(f"Tentando abrir menu ({tentativa+1}/{tentativas})")

            page.locator("header i").click(timeout=5000)

            return True

        except:
            sleep(2)

    log("Falha ao clicar no menu.")

    telegram(f"Falha ao abrir menu - Linha {dados['linha']}")

    return False


def abrir_dashboard(page):

    if not clicar_menu(page):
        raise Exception("Menu não abriu")

    sleep(1)

    page.get_by_role("link", name="DASHBOARD ").click()

    sleep(1)

    page.get_by_role("link", name="MANUFATURA ").click()

    sleep(1)

    page.evaluate("""
        loadPageNew('dash.html', 'DASH', 'pageContent',
        'https://datadriven.datawake.com.br:8091/',
        'frameDash', 'OEE-Online');
    """)

    sleep(2)

    clicar_menu(page)


def interacoes_iniciais_iframe(page):
    # Interações iniciais com o iframe, antes do ciclo de dashboards
    for tentativa in range(1, 3):
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
            log(f"Timeout ao interagir com o iframe/F11 (tentativa {tentativa}/2): {te}")
            sleep(2)


def tentar_abrir_dashboard_com_retry(page, tentativas=2, motivo=""):
    """
    Tenta abrir DASHBOARD + selecionar a linha.
    Se falhar nas tentativas, avisa via Telegram.
    """
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

            # tenta recuperar a página antes de repetir
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

        # pega o container da linha inteira
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

    telegram(f"Dashboard da linha {dados['linha']} aberto com sucesso")

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

            if time() - ultimo_reload > TEMPO_ATUALIZACAO_SEGUNDOS:

                log(f"Tempo de atualização atingido. Modo: {MODO_ATUALIZACAO}")

                try:
                    if MODO_ATUALIZACAO == "F5":
                        page.keyboard.press("F5")
                    else:
                        page.reload()

                    page.wait_for_load_state("networkidle", timeout=60000)
                except Exception as e:
                    log(f"Falha durante acionamento da atualização: {e}")

                sucesso = tentar_abrir_dashboard_com_retry(
                    page,
                    tentativas=2,
                    motivo=f"atualização periódica ({MODO_ATUALIZACAO})",
                )

                if not sucesso:
                    # evita loop apertado; tenta de novo no próximo ciclo
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

            log("Dashboard travou. Reiniciando...")

            telegram(f"Dashboard da linha {dados['linha']} travou por mais de 10 minutos. Reiniciando.")

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

            telegram(f"Robô da linha {dados['linha']} iniciado")

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
                timeout=30000)

            sleep(3)

            pyautogui.press("f11")

            sleep(3)

            page.get_by_role("textbox", name="Email:").fill(dados["login"])
            sleep(3)

            page.get_by_role("textbox", name="Senha").fill(dados["senha"])
            sleep(3)

            page.get_by_role("button", name="Login").click()
            sleep(3)
            page.wait_for_load_state("networkidle")

            sleep(5)

            log("Iniciando monitoramento do dashboard")
            sleep(3)
            monitorar_dashboard(page)
            sleep(3)

        except Exception as e:

            log(f"Erro geral: {e}")

            telegram(
                f"Robô da linha {dados['linha']} reiniciando\nErro: {str(e)}"
            )

            try:
                browser.close()
            except:
                pass

            log("Reiniciando robô em 10 segundos")

            sleep(10)


if __name__ == "__main__":

    with sync_playwright() as playwright:

        run(playwright)
