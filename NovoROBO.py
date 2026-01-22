from playwright.sync_api import Playwright, sync_playwright, TimeoutError
from time import sleep
import os
from datetime import datetime
from dotenv import load_dotenv
#import pyautogui 

load_dotenv()

def credenciais():
    return {
        "NTH_1": int(os.getenv("NTH_1")),
        "NTH_2": int(os.getenv("NTH_2")),
        "login": os.getenv("Login"),
        "senha": os.getenv("senha")}

dados = credenciais()

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
        # Loop de retentativas para o clique no 'header i'
        for i in range(3):
            try:
                page.locator("header i").click()
                print(f"[INFO] 'header i' clicado com sucesso na tentativa {i+1}.")
                break # Sai do loop se o clique for bem-sucedido
            except Exception as e:
                print(f"[ERRO] Tentativa {i+1} de 3: Falha ao clicar no 'header i': {e}")
                if i < 2: # Se não for a última tentativa, espera e tenta novamente
                    sleep(2)
                else: # Se a última tentativa falhar, re-lança a exceção para ser capturada pelo bloco externo
                    raise
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

    # Interações iniciais com o iframe, antes do ciclo de dashboards
    try:
        iframe = page.frame_locator("#frameDash")
        iframe.locator("button:has(svg.animate-spin)").click(timeout=5000)
        sleep(3)

        iframe.locator("button:has-text('Modo Tela Cheia')").click(timeout=5000)
        sleep(2)

        iframe.locator("button:has(svg.lucide-x)").click(timeout=5000)
        sleep(3)

        page.keyboard.press("F11")
        sleep(1)
    except TimeoutError as te:
        print(f"[ERRO] Timeout ao interagir com o iframe/F11: {te}")
        return
    except Exception as e:
        print(f"[ERRO] Erro ao interagir com o iframe/F11: {e}")
        return

    def interagir_com_dashboard(page, nth_index):
        iframe = page.frame_locator("#frameDash")
        linha_mqb = iframe.locator("button:has-text('Detalhes')").nth(nth_index)
        linha_mqb.click(timeout=5000)

        sleep(2)

    try:
        current_nth = dados["NTH_1"]
        interagir_com_dashboard(page, current_nth)

        while True:
            print(f"[INFO] Dashboard atual: NTH={current_nth}. Próxima mudança em 30 segundos.")
            sleep(30)
            # Clica no botão antes de mudar para o próximo dashboard
            iframe = page.frame_locator("#frameDash")
            iframe.locator("a").get_by_role("button").click(timeout=5000)
            sleep(5) # Pequena espera após o clique

            if current_nth == dados["NTH_1"]:
                current_nth = dados["NTH_2"]
            else:
                current_nth = dados["NTH_1"]
            print(f"[INFO] Mudando para NTH={current_nth}")
            interagir_com_dashboard(page, current_nth)
    except TimeoutError as te:
        print(f"[ERRO] Timeout ao tentar clicar nos botões dentro do iframe: {te}")
        return
    except Exception as e:
        print(f"[ERRO] Erro ao interagir com o iframe: {e}")
        return

    browser.close()

if __name__ == "__main__":
    with sync_playwright() as playwright:
        run(playwright)
