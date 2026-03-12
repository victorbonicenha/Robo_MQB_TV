from playwright.sync_api import sync_playwright, TimeoutError
from time import sleep
import os
from datetime import datetime
from dotenv import load_dotenv
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

def abrir_linha(iframe):

    log("Procurando linha...")

    botoes = iframe.locator("text=Detalhes")

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

    iframe = page.frame_locator("#frameDash")

    abrir_linha(iframe)

    log("Dashboard aberto")

    telegram(f"Dashboard da linha {dados['linha']} aberto com sucesso")

    while True:

        try:

            iframe.locator("button:has-text('Detalhes')").first.wait_for(timeout=15000)

            log("Dashboard OK")

        except TimeoutError:

            log("Dashboard travou ou servidor caiu. Recarregando...")

            telegram(f"Dashboard da linha {dados['linha']} caiu. Tentando reiniciar.")

            page.reload()

            page.wait_for_load_state("networkidle")

            abrir_dashboard(page)

            iframe = page.frame_locator("#frameDash")

            abrir_linha(iframe)

            telegram(f"Dashboard da linha {dados['linha']} recuperado")

        sleep(30)


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

            sleep(2)

            page.bring_to_front()
            page.keyboard.press("F11")

            sleep(1)

            page.get_by_role("textbox", name="Email:").fill(dados["login"])
            sleep(3)

            page.get_by_role("textbox", name="Senha").fill(dados["senha"])
            sleep(3)

            page.get_by_role("button", name="Login").click()
            sleep(3)
            page.wait_for_load_state("networkidle")

            sleep(5)

            log("Abrindo dashboard")
            sleep(3)
            abrir_dashboard(page)
            sleep(3)
            page.wait_for_selector("#frameDash", timeout=30000)
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
