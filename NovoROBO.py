from playwright.sync_api import Playwright, sync_playwright, TimeoutError
from time import sleep
import os
from datetime import datetime
from dotenv import load_dotenv
#import pyautogui 

load_dotenv()

def credenciais():
    return {
        "NTH": int(os.getenv("NTH")),
        "login": os.getenv("Login"),
        "senha": os.getenv("senha")
    }

dados = credenciais()
nth_value = dados["NTH"]

def run(playwright: Playwright) -> None:
    print(f"Iniciando navegador às {datetime.now()}")

    browser = playwright.chromium.launch(headless=False, args=["--start-maximized"])
    context = browser.new_context(no_viewport=True)
    page = context.new_page()

    try:
        page.goto("https://datadriven.datawake.com.br:8057/data-driven/login.html", timeout=15000)
    except TimeoutError:
        print("[ERRO] Timeout ao carregar a página de login")
        return

    try:
        page.get_by_role("textbox", name="Email:").fill(dados["login"])
        page.get_by_role("textbox", name="Senha").fill(dados["senha"])
        page.get_by_role("button", name="Login").click()
    except Exception as e:
        print(f"[ERRO] Falha ao preencher ou enviar o login: {e}")
        return

    sleep(5)
    try:
        page.locator("header i").click()
        sleep(1)
        page.get_by_role("link", name="DASHBOARD ").click()
        sleep(1)
        page.get_by_role("link", name="MANUFATURA ").click()
        sleep(1)
        page.evaluate("""
        loadPageNew('dash.html', 'DASH', 'pageContent',
                'https://datadriven.datawake.com.br:8091/',
                'frameDash', 'OEE-Online');""")
        sleep(1)
        page.locator("header i").click()
    except Exception as e:
        print(f"[ERRO] Navegação inicial falhou: {e}")
        return

    sleep(10)

    try:
        iframe = page.frame_locator("#frameDash")

        iframe.locator("button:has(svg.animate-spin)").click(timeout=5000)
        sleep(3)

        iframe.locator("button:has-text('Modo Tela Cheia')").click(timeout=5000)
        sleep(2)

        iframe.locator("button:has(svg.lucide-x)").click(timeout=5000)
        sleep(3)

        linha_mqb = iframe.locator("button:has-text('Detalhes')").nth(nth_value)
        linha_mqb.click(timeout=5000)

        #pyautogui.click(x=1000, y=500)
        #pyautogui.press("f11")

        sleep(2)

    except TimeoutError as te:
        print(f"[ERRO] Timeout ao tentar clicar nos botões dentro do iframe: {te}")
        return
    except Exception as e:
        print(f"[ERRO] Erro ao interagir com o iframe: {e}")
        return

    try:
        while True:
            sleep(60)
    except KeyboardInterrupt:
        print("\n[INFO] Interrompido manualmente pelo usuário.")

    browser.close()

if __name__ == "__main__":
    with sync_playwright() as playwright:
        run(playwright)
