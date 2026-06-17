from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from time import sleep, time
from datetime import datetime
from typing import Optional, Callable
import sys
import subprocess
import requests
import json
import random
import socket
import concurrent.futures
import uuid
import ssl
import os
from requests.adapters import HTTPAdapter
from urllib3.poolmanager import PoolManager
import sdnotify

_watchdog = sdnotify.SystemdNotifier()

try:
    import psutil  # opcional, usado para health-check
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

class Logger:
    def __init__(
        self,
        url: str,
        user: str,
        password: str,
        service_name: str = "python-app",
        max_workers: int = 2,
        timeout: int = 5,
        allow_insecure_ssl: bool = False,
        clickhouse_table: str = "logs.logs",
    ):
        self.url = url.rstrip("/")
        self.auth = (user, password)
        self.service_name = service_name
        self.timeout = timeout
        self.verify = not allow_insecure_ssl
        self.clickhouse_table = clickhouse_table
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
        self.uid = str(uuid.uuid4())

    def _payload(
        self,
        tipo: str,
        rotina: Optional[str] = None,
        usuario: Optional[str] = None,
        mensagem: Optional[str] = None,
        cliente: Optional[str] = None,
        tabela: Optional[str] = None,
        topico: Optional[str] = None,
        op: Optional[str] = None,
        unidade_producao: Optional[str] = None,
        id_session: Optional[str] = None,
        ip_user: Optional[str] = None,
        uid: Optional[str] = None,
    ) -> dict:
        return {
            "id": int(time() * 1_000_000) + random.randint(0, 999),
            "id_session": id_session,
            "sistema": self.service_name,
            "rotina": rotina or "Geral",
            "usuario": usuario or "system",
            "tipo": tipo,
            "mensagem": mensagem,
            "cliente": str(cliente) if cliente is not None else None,
            "ip_user": ip_user or self._ip_local(),
            "tabela": tabela,
            "topico": topico,
            "op": op,
            "unidade_producao": str(unidade_producao) if unidade_producao is not None else None,
            "uid": uid or self.uid,
        }

    def _enviar(self, dados: dict):
        try:
            resp = requests.post(
                self.url,
                params={"query": f"INSERT INTO {self.clickhouse_table} FORMAT JSONEachRow"},
                auth=self.auth,
                data=json.dumps(dados),
                timeout=self.timeout,
                verify=self.verify,
            )
            if resp.status_code != 200:
                print(f"[Logger] Erro ClickHouse ({resp.status_code}): {resp.text}", flush=True)
        except Exception as e:
            print(f"[Logger] Falha de conexão: {e}", flush=True)

    def _async(self, dados: dict):
        self.executor.submit(self._enviar, dados)

    def info(self, rotina=None, mensagem=None, **kw):
        self._async(self._payload("INFO",    rotina=rotina, mensagem=mensagem, **kw))

    def erro(self, rotina=None, mensagem=None, **kw):
        self._async(self._payload("ERROR",   rotina=rotina, mensagem=mensagem, **kw))

    def aviso(self, rotina=None, mensagem=None, **kw):
        self._async(self._payload("WARNING", rotina=rotina, mensagem=mensagem, **kw))

    def debug(self, rotina=None, mensagem=None, **kw):
        self._async(self._payload("DEBUG",   rotina=rotina, mensagem=mensagem, **kw))

    def close(self):
        self.executor.shutdown(wait=True)

    @staticmethod
    def _ip_local() -> str:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "desconhecido"

class CustomSSLAdapter(HTTPAdapter):
    """
    Adapter necessário para compatibilidade com servidores legados que
    exigem TLS 1.2 fixo e ciphers em SECLEVEL=1 (sistemas internos antigos
    que não suportam configurações de SSL mais modernas).
    """
    def __init__(self, **kwargs):
        self.ssl_context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode    = ssl.CERT_NONE
        self.ssl_context.options       |= 0x4
        self.ssl_context.set_ciphers("DEFAULT@SECLEVEL=1")
        self.ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2
        self.ssl_context.maximum_version = ssl.TLSVersion.TLSv1_2
        super().__init__(**kwargs)

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        self.poolmanager = PoolManager(
            num_pools=connections, maxsize=maxsize, block=block,
            ssl_context=self.ssl_context, **pool_kwargs,
        )

def _get_session():
    session = requests.Session()
    session.mount("https://", CustomSSLAdapter())
    return session

def _get_token():
    """
    Autentica via OAuth2 (grant_type=password) contra a API interna de
    configuração. Todas as credenciais vêm de variáveis de ambiente —
    nunca hardcode usuário/senha/Basic Auth aqui.
    """
    base_url   = os.environ["CONFIG_API_BASE_URL"]          # ex: https://sua-api.exemplo.com:8058
    api_user   = os.environ["CONFIG_API_USER"]
    api_pass   = os.environ["CONFIG_API_PASSWORD"]
    basic_auth = os.environ["CONFIG_API_BASIC_AUTH"]        # header Authorization: Basic <token> já codificado

    url = f"{base_url}/oauth/token"
    payload = {
        "username": api_user,
        "password": api_pass,
        "grant_type": "password",
    }
    headers = {
        "Content-Type":  "application/x-www-form-urlencoded",
        "Authorization": basic_auth,
    }
    resp = _get_session().post(url, data=payload, headers=headers, verify=False)
    resp.raise_for_status()
    return resp.json()["access_token"]

def _busca_env(token):
    base_url = os.environ["CONFIG_API_BASE_URL"]
    sheet_key = os.environ["CONFIG_SHEET_KEY"]              # chave da planilha técnica com os envs das linhas
    url     = f"{base_url}/v1/technical_sheet/{sheet_key}/data"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    resp    = _get_session().get(url, headers=headers, verify=False)
    resp.raise_for_status()
    return resp.json()

def _parse_env(env_string: str) -> dict:
    resultado = {}
    for linha in env_string.split("\r\n"):
        linha = linha.strip()
        if "=" in linha:
            chave, valor = linha.split("=", 1)
            resultado[chave.strip()] = valor.strip()
    return resultado

def _extract_envs(data: dict) -> dict:
    resultado = {}
    for record in data["content"]:
        uniprod    = None
        env_string = None
        for item in record["data"]:
            attr  = item["attributeModel"]["attribute"]
            valor = item["attributeValuesModel"]["valueString"]
            if attr == "uniprod":
                uniprod = valor
            elif attr == "env_tabelas":
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

LINHA_ALVO = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("LINHA_ALVO_DEFAULT", "LINHA001")

print(f"[boot] Carregando configs da API para linha '{LINHA_ALVO}'...", flush=True)
LINHAS = carregar_envs()

cfg = next((x for x in LINHAS if x["linha"] == LINHA_ALVO), None)
if cfg is None:
    print(f"[boot] Linha '{LINHA_ALVO}' não encontrada nas configs!", flush=True)
    sys.exit(1)

LOGIN         = cfg["Login"]
SENHA         = cfg["senha"]
TOKEN_TG      = cfg["Telegram_Token"]
CHAT_ID       = cfg["Telegram_Chat_ID"]
ESPERA_LINHAS = int(cfg["ESPERA_CARREGAMENTO_LINHAS_SEGUNDOS"])
ESPERA_IFRAME = int(cfg["ESPERA_ENTRE_ACOES_IFRAME_SEGUNDOS"])
MODO_ATT      = cfg["MODO_ATUALIZACAO"].strip().upper()
TEMPO_ATT     = int(cfg["TEMPO_ATUALIZACAO_SEGUNDOS"])
LINHA         = cfg["linha"]

# URL de login do sistema de dashboard — também via env, nunca hardcoded
LOGIN_URL = os.environ["DASHBOARD_LOGIN_URL"]   # ex: https://seu-dominio.com:8057/data-driven/login.html
DASHBOARD_IFRAME_SRC = os.environ["DASHBOARD_IFRAME_SRC"]  # ex: https://seu-dominio.com:8091/

logger = Logger(
    url                = cfg.get("LOG_URL",      os.environ.get("LOG_URL", "")),
    user               = cfg.get("LOG_USER",     os.environ.get("LOG_USER", "")),
    password           = cfg.get("LOG_PASSWORD", os.environ.get("LOG_PASSWORD", "")),
    service_name       = "OEE-Dashboard-Bot",
    allow_insecure_ssl = False,
    clickhouse_table   = os.environ.get("LOG_CLICKHOUSE_TABLE", "logs.logs"),
)

INTERVALO_CHECK_S = 120
MAX_CICLOS_PARADO = 5
TENTATIVAS_PADRAO = 3      # tentativas padrao para qualquer acao na pagina
TIMEOUT_PADRAO_MS = 8000   # timeout padrao por tentativa
ESPERA_RETRY_S    = 2      # espera entre tentativas
MAX_FALHAS_HEARTBEAT = 3

print(f"[boot] Iniciando robô para linha '{LINHA}'", flush=True)


def log(msg: str):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def telegram(msg: str):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN_TG}/sendMessage",
            data={"chat_id": CHAT_ID, "text": msg},
            timeout=10,
        )
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────────
# RETRY HELPERS — usados por TUDO que clica/preenche na pagina
# ──────────────────────────────────────────────────────────────────────
def acao_com_retry(
    descricao: str,
    funcao: Callable,
    tentativas: int = TENTATIVAS_PADRAO,
    espera_entre: int = ESPERA_RETRY_S,
    obrigatoria: bool = True,
):
    """
    Executa 'funcao' ate 'tentativas' vezes. Loga cada falha.
    Se 'obrigatoria' = True e todas falharem -> raise
    Se 'obrigatoria' = False -> retorna False sem levantar (pra acoes opcionais)
    """
    ultimo_erro = None
    for n in range(1, tentativas + 1):
        try:
            log(f"  -> [{descricao}] tentativa {n}/{tentativas}")
            logger.debug(rotina="acao_com_retry",
                         mensagem=f"{descricao} - tentativa {n}/{tentativas}",
                         unidade_producao=LINHA)
            funcao()
            log(f"  ✓ [{descricao}] OK")
            logger.info(rotina="acao_com_retry",
                        mensagem=f"{descricao} - sucesso na tentativa {n}",
                        unidade_producao=LINHA)
            return True
        except Exception as e:
            ultimo_erro = e
            log(f"  ✗ [{descricao}] falha {n}/{tentativas}: {str(e)[:120]}")
            logger.aviso(rotina="acao_com_retry",
                         mensagem=f"{descricao} falhou tentativa {n}: {e}",
                         unidade_producao=LINHA)
            if n < tentativas:
                sleep(espera_entre)

    msg_final = f"{descricao} falhou após {tentativas} tentativas. Último erro: {ultimo_erro}"
    log(f"  ✗✗ [{descricao}] {msg_final}")
    logger.erro(rotina="acao_com_retry", mensagem=msg_final, unidade_producao=LINHA)
    if obrigatoria:
        raise RuntimeError(msg_final)
    return False


def clicar(page_or_frame, seletor: str, descricao: str,
           tentativas: int = TENTATIVAS_PADRAO, timeout_ms: int = TIMEOUT_PADRAO_MS,
           obrigatoria: bool = True) -> bool:
    """Click resiliente com retry."""
    return acao_com_retry(
        descricao=f"clicar '{descricao}'",
        funcao=lambda: page_or_frame.locator(seletor).first.click(timeout=timeout_ms),
        tentativas=tentativas,
        obrigatoria=obrigatoria,
    )


def clicar_role(page, role: str, name: str, descricao: str,
                tentativas: int = TENTATIVAS_PADRAO, timeout_ms: int = TIMEOUT_PADRAO_MS,
                obrigatoria: bool = True) -> bool:
    """Click via get_by_role com retry."""
    return acao_com_retry(
        descricao=f"clicar role={role} '{descricao}'",
        funcao=lambda: page.get_by_role(role, name=name).first.click(timeout=timeout_ms),
        tentativas=tentativas,
        obrigatoria=obrigatoria,
    )


def preencher_role(page, role: str, name: str, valor: str, descricao: str,
                   tentativas: int = TENTATIVAS_PADRAO, timeout_ms: int = TIMEOUT_PADRAO_MS,
                   obrigatoria: bool = True) -> bool:
    """Fill via get_by_role com retry."""
    def _do():
        loc = page.get_by_role(role, name=name).first
        loc.wait_for(timeout=timeout_ms, state="visible")
        loc.fill(valor)
    return acao_com_retry(
        descricao=f"preencher '{descricao}'",
        funcao=_do,
        tentativas=tentativas,
        obrigatoria=obrigatoria,
    )


def sleep_vigiando_welcome(page, segundos, limite_welcome=120):
    xpath_welcome    = '//*[@id="welcome"]/div'
    welcome_desde    = None
    falhas_dashboard = 0
    fim              = datetime.now().timestamp() + segundos
    ultimo_heartbeat = datetime.now().timestamp()

    while datetime.now().timestamp() < fim:
        agora = datetime.now().timestamp()

        if agora - ultimo_heartbeat >= 60:
            _watchdog.notify("WATCHDOG=1")
            # verifica dashboard inline
            try:
                btn = page.locator("#frameDash").content_frame.locator(
                    "button:has-text('Última Atualização')"
                )
                vivo = btn.is_visible(timeout=5_000)
                if vivo:
                    btn.click(timeout=5_000)
            except Exception:
                vivo = False

            if vivo:
                falhas_dashboard = 0
                log("[heartbeat] Robô ativo — dashboard OK")
                logger.info(rotina="heartbeat",
                            mensagem="Robô ativo. Dashboard confirmado vivo.",
                            unidade_producao=LINHA)
            else:
                falhas_dashboard += 1
                log(f"[heartbeat] Dashboard sem resposta — falha {falhas_dashboard}/{MAX_FALHAS_HEARTBEAT}")
                logger.aviso(rotina="heartbeat",
                             mensagem=f"Dashboard sem resposta. Falha {falhas_dashboard}/{MAX_FALHAS_HEARTBEAT}.",
                             unidade_producao=LINHA)
                if falhas_dashboard >= MAX_FALHAS_HEARTBEAT:
                    msg = f"Linha {LINHA} — dashboard morto por {falhas_dashboard} heartbeats. Reiniciando."
                    telegram(msg)
                    logger.erro(rotina="heartbeat", mensagem=msg, unidade_producao=LINHA)
                    raise RuntimeError(msg)

            ultimo_heartbeat = agora

        try:
            welcome_visivel = page.locator(xpath_welcome).first.is_visible(timeout=1_000)
        except Exception:
            welcome_visivel = False

        if welcome_visivel:
            if welcome_desde is None:
                welcome_desde = agora
                log("[welcome] Tela detectada — iniciando contagem")
                logger.aviso(rotina="sleep_vigiando_welcome",
                             mensagem="Welcome screen detectada.",
                             unidade_producao=LINHA)
            else:
                parado = int(agora - welcome_desde)
                log(f"[welcome] Travada há {parado}s (limite {limite_welcome}s)")
                if parado >= limite_welcome:
                    msg = f"Linha {LINHA} travada na welcome há {parado}s. Reiniciando."
                    telegram(msg)
                    logger.erro(rotina="sleep_vigiando_welcome", mensagem=msg, unidade_producao=LINHA)
                    raise RuntimeError(msg)
        else:
            if welcome_desde is not None:
                log("[welcome] Saiu sozinha — contagem zerada")
                logger.info(rotina="sleep_vigiando_welcome",
                            mensagem="Welcome screen desapareceu.",
                            unidade_producao=LINHA)
                welcome_desde = None

        sleep(10)
# ──────────────────────────────────────────────────────────────────────
# FLUXO
# ──────────────────────────────────────────────────────────────────────
def clicar_menu(page, tentativas: int = TENTATIVAS_PADRAO) -> bool:
    """Abre/fecha menu lateral via ícone bx-search no header."""
    return acao_com_retry(
        descricao="clicar menu (bx-search)",
        funcao=lambda: page.locator("i").nth(1).click(timeout=TIMEOUT_PADRAO_MS),
        tentativas=tentativas,
        obrigatoria=False,
    )

def abrir_dashboard(page):
    log("[dashboard] Iniciando navegação para OEE Online")
    logger.info(rotina="abrir_dashboard",
                mensagem="Iniciando navegação até dashboard",
                unidade_producao=LINHA)

    # ── 1. Abre menu de busca ────────────────────────────────────────────
    acao_com_retry(
        descricao="abrir menu busca (bx-search)",
        funcao=lambda: page.locator("header i.bx-search").click(timeout=TIMEOUT_PADRAO_MS),
        tentativas=TENTATIVAS_PADRAO,
        obrigatoria=True,
    )

    # ── 2. Preenche busca e valida valor aceito ──────────────────────────
    def _preencher_busca():
        campo = page.get_by_role("textbox", name="Pesquise no menu...")
        campo.wait_for(state="visible", timeout=TIMEOUT_PADRAO_MS)
        campo.click(timeout=TIMEOUT_PADRAO_MS)
        campo.fill("OEE Online")
        valor_atual = campo.input_value()
        if "OEE" not in valor_atual:
            raise RuntimeError(f"Campo não aceitou o valor — atual: '{valor_atual}'")

    acao_com_retry(
        descricao="preencher busca 'OEE Online'",
        funcao=_preencher_busca,
        tentativas=TENTATIVAS_PADRAO,
        obrigatoria=True,
    )

    # ── 3. Clica no link OEE Online ──────────────────────────────────────
    def _clicar_link_oee():
        link = page.get_by_role("link", name="OEE Online", exact=True)
        link.wait_for(state="visible", timeout=TIMEOUT_PADRAO_MS)
        count = page.get_by_role("link", name="OEE Online", exact=True).count()
        if count == 0:
            raise RuntimeError("Link 'OEE Online' não apareceu nos resultados")
        if count > 1:
            log(f"[dashboard] {count} links 'OEE Online' encontrados — clicando no primeiro")
            logger.aviso(rotina="abrir_dashboard",
                         mensagem=f"{count} links 'OEE Online' encontrados",
                         unidade_producao=LINHA)
        link.first.click(timeout=TIMEOUT_PADRAO_MS)

    acao_com_retry(
        descricao="clicar link 'OEE Online'",
        funcao=_clicar_link_oee,
        tentativas=TENTATIVAS_PADRAO,
        obrigatoria=True,
    )

    # ── 4. loadPageNew ───────────────────────────────────────────────────
    acao_com_retry(
        descricao="evaluate loadPageNew",
        funcao=lambda: page.evaluate(
            f"loadPageNew('dash.html','DASH','pageContent',"
            f"'{DASHBOARD_IFRAME_SRC}','frameDash','OEE-Online');"
        ),
        tentativas=TENTATIVAS_PADRAO,
        obrigatoria=True,
    )

    sleep(2)

    # ── 5. Fecha menu lateral (não crítico) ──────────────────────────────
    def _fechar_menu():
        menu_visivel = page.locator("header nav, header .menu, header [role='navigation']").is_visible()
        if not menu_visivel:
            log("[dashboard] Menu já fechou sozinho — pulando")
            return
        page.locator("header i.bx-search").click(timeout=TIMEOUT_PADRAO_MS)

    acao_com_retry(
        descricao="fechar menu lateral",
        funcao=_fechar_menu,
        tentativas=3,
        obrigatoria=False,
    )

    # ── 6. Aguarda iframe e interage ─────────────────────────────────────
    log("[dashboard] Aguardando iframe #frameDash")
    acao_com_retry(
        descricao="aguardar #frameDash visível",
        funcao=lambda: page.wait_for_selector("#frameDash", timeout=30_000),
        tentativas=TENTATIVAS_PADRAO,
        obrigatoria=True,
    )

    iframe = page.frame_locator("#frameDash")
    sleep(ESPERA_IFRAME)

    # ── 6a. Botão de menu interno do iframe ──────────────────────────────
    def _clicar_menu_iframe():
        btn = page.locator("#frameDash").content_frame.locator(".fixed > .inline-flex")
        btn.wait_for(state="visible", timeout=20_000)
        btn.click(timeout=20_000)

    acao_com_retry(
        descricao="clicar menu iframe (.fixed > .inline-flex)",
        funcao=_clicar_menu_iframe,
        tentativas=TENTATIVAS_PADRAO,
        obrigatoria=False,
    )

    sleep(2)

    # ── 6b. Modo Tela Cheia ───────────────────────────────────────────────
    def _clicar_tela_cheia():
        btn = page.locator("#frameDash").content_frame.get_by_role("button", name="Modo Tela Cheia")
        btn.wait_for(state="visible", timeout=20_000)
        btn.click(timeout=20_000)

    acao_com_retry(
        descricao="clicar 'Modo Tela Cheia'",
        funcao=_clicar_tela_cheia,
        tentativas=TENTATIVAS_PADRAO,
        obrigatoria=False,
    )

    sleep(2)

    # ── 6c. Fechar modal (Close) ─────────────────────────────────────────
    def _fechar_modal():
        btn = page.locator("#frameDash").content_frame.get_by_role("button", name="Close")
        btn.wait_for(state="visible", timeout=20_000)
        btn.click(timeout=20_000)

    acao_com_retry(
        descricao="fechar modal iframe (Close)",
        funcao=_fechar_modal,
        tentativas=TENTATIVAS_PADRAO,
        obrigatoria=False,
    )

    sleep(3)

    logger.info(rotina="abrir_dashboard",
                mensagem="Dashboard carregado e interações iniciais concluídas",
                unidade_producao=LINHA)

def abrir_linha(iframe):
    sleep(10)
    # Aguarda lista carregar (com retry)
    def _aguarda_lista():
        iframe.locator("text=Detalhes").first.wait_for(timeout=15_000)

    acao_com_retry(
        descricao="aguardar lista 'Detalhes'",
        funcao=_aguarda_lista,
        tentativas=TENTATIVAS_PADRAO,
    )
    sleep(2)

    botoes = iframe.locator("text=Detalhes")
    count  = botoes.count()
    log(f"[linha] {count} botões 'Detalhes' encontrados")
    logger.debug(rotina="abrir_linha",
                 mensagem=f"{count} botões 'Detalhes' encontrados",
                 unidade_producao=LINHA)

    if count == 0:
        raise RuntimeError("Nenhum botao Detalhes localizado")

    # Procura a linha alvo
    indice_alvo = -1
    for i in range(count):
        try:
            container = botoes.nth(i).locator("xpath=ancestor::*[self::div or self::tr][1]")
            if LINHA in container.inner_text(timeout=3000):
                indice_alvo = i
                break
        except Exception:
            continue

    if indice_alvo < 0:
        msg = f"Linha '{LINHA}' não encontrada na lista do dashboard"
        log(f"[linha] ERRO: {msg}")
        telegram(msg)
        logger.erro(rotina="abrir_linha", mensagem=msg, unidade_producao=LINHA)
        raise RuntimeError("Linha não encontrada")

    log(f"[linha] '{LINHA}' encontrada no índice {indice_alvo}. Clicando.")
    logger.info(rotina="abrir_linha",
                mensagem=f"Linha '{LINHA}' encontrada (índice {indice_alvo}). Clicando em Detalhes.",
                unidade_producao=LINHA)

    # Click com retry
    acao_com_retry(
        descricao=f"clicar Detalhes da linha {LINHA}",
        funcao=lambda: botoes.nth(indice_alvo).click(timeout=50_000),
        tentativas=TENTATIVAS_PADRAO,
    )


def tentar_abrir_dashboard_com_retry(page, motivo: str = "") -> bool:
    """
    Abre o dashboard, seleciona a linha e ativa F11.
    Tenta 3 vezes o fluxo completo. Retorna True/False.
    """
    log(f"[retry] Preparando dashboard — {motivo or 'sem motivo especificado'}")
    logger.info(rotina="tentar_abrir_dashboard_com_retry",
                mensagem=f"Iniciando preparação. Motivo: {motivo or 'N/A'}",
                unidade_producao=LINHA)

    ultimo_erro = None
    for tentativa in range(1, 4):
        try:
            log(f"[retry] Tentativa {tentativa}/3")
            logger.debug(rotina="tentar_abrir_dashboard_com_retry",
                         mensagem=f"Tentativa {tentativa}/3 iniciada",
                         unidade_producao=LINHA)

            abrir_dashboard(page)

            # aguarda iframe com retry
            acao_com_retry(
                descricao="aguardar #frameDash",
                funcao=lambda: page.wait_for_selector("#frameDash", timeout=30_000),
                tentativas=3,
            )
            sleep(1)

            iframe = page.frame_locator("#frameDash")
            abrir_linha(iframe)
            sleep(2)

            # F11 - nao critico
            try:
                page.keyboard.press("F11")
                logger.debug(rotina="tentar_abrir_dashboard_com_retry",
                             mensagem="F11 pressionado",
                             unidade_producao=LINHA)
            except Exception as e:
                log(f"[retry] F11 falhou (nao critico): {e}")

            log("[retry] Dashboard preparado com sucesso")
            logger.info(rotina="tentar_abrir_dashboard_com_retry",
                        mensagem="Dashboard aberto e linha selecionada com sucesso",
                        unidade_producao=LINHA)
            return True

        except Exception as e:
            ultimo_erro = e
            log(f"[retry] Falha na tentativa {tentativa}/3: {e}")
            logger.erro(rotina="tentar_abrir_dashboard_com_retry",
                        mensagem=f"Falha na tentativa {tentativa}/3: {e}",
                        unidade_producao=LINHA)
            try:
                page.reload()
                page.wait_for_load_state("networkidle", timeout=90_000)
                logger.debug(rotina="tentar_abrir_dashboard_com_retry",
                             mensagem="Página recarregada após falha",
                             unidade_producao=LINHA)
            except Exception as re_err:
                logger.aviso(rotina="tentar_abrir_dashboard_com_retry",
                             mensagem=f"Falha ao recarregar após erro: {re_err}",
                             unidade_producao=LINHA)
            sleep(2)

    msg = (
        f"Sistema fora do ar — linha {LINHA}\n"
        f"Motivo: {motivo}\n"
        f"Erro: {str(ultimo_erro)}"
    )
    telegram(msg)
    logger.erro(rotina="tentar_abrir_dashboard_com_retry",
                mensagem=f"Todas as tentativas esgotadas. Telegram enviado. Último erro: {ultimo_erro}",
                unidade_producao=LINHA)
    return False


FALHAS_DASHBOARD_MAX = 3  # constante no topo do arquivo


def _dashboard_esta_vivo(page) -> bool:
    """
    Verifica se o botão 'Última Atualização' está visível no iframe.
    Retorna True se visível, False caso contrário.
    """
    try:
        btn = page.locator("#frameDash").content_frame.locator(
            "button:has-text('Última Atualização:')"
        )
        return btn.first.is_visible(timeout=3_000)
    except Exception:
        return False


def _tentar_clicar_ultima_atualizacao(page) -> bool:
    """
    Tenta clicar no botão 'Última Atualização' do iframe.
    Retorna True se clicou, False se não encontrou/falhou.
    """
    try:
        btn = page.locator("#frameDash").content_frame.locator(
            "button:has-text('Última Atualização:')"
        )
        btn.first.wait_for(state="visible", timeout=5_000)
        btn.first.click(timeout=5_000)
        log("[health] Clique em 'Última Atualização' OK")
        logger.info(rotina="health_check",
                    mensagem="Clique em 'Última Atualização' realizado com sucesso",
                    unidade_producao=LINHA)
        return True
    except Exception as e:
        log(f"[health] Falha ao clicar 'Última Atualização': {e}")
        logger.aviso(rotina="health_check",
                     mensagem=f"Falha ao clicar 'Última Atualização': {e}",
                     unidade_producao=LINHA)
        return False


def monitorar_dashboard(page):
    log("[monitor] Abrindo dashboard pela primeira vez")
    logger.info(rotina="monitorar_dashboard",
                mensagem="Preparação inicial do dashboard",
                unidade_producao=LINHA)

    sucesso = tentar_abrir_dashboard_com_retry(page, motivo="inicial")
    if not sucesso:
        logger.erro(rotina="monitorar_dashboard",
                    mensagem="Dashboard inacessível na inicialização — abortando ciclo",
                    unidade_producao=LINHA)
        raise RuntimeError("Não foi possível abrir DASHBOARD após retries")

    log("[monitor] Dashboard aberto. Aguardando ciclos de atualização.")
    telegram(f"Dashboard da linha {LINHA} aberto com sucesso")
    logger.info(rotina="monitorar_dashboard",
                mensagem="Dashboard aberto. Loop de atualização iniciado.",
                unidade_producao=LINHA)

    falhas_health = 0  # contador de heartbeats sem dashboard visível

    while True:
        log(f"[monitor] Aguardando {TEMPO_ATT}s até próximo reload (vigiando welcome)...")
        logger.info(rotina="monitorar_dashboard",
                    mensagem=f"Iniciando espera de {TEMPO_ATT}s antes do reload.",
                    unidade_producao=LINHA)

        # ── Sleep vigiado com health-check a cada minuto ─────────────────
        xpath_welcome  = '//*[@id="welcome"]/div'
        welcome_desde  = None
        fim            = datetime.now().timestamp() + TEMPO_ATT
        ultimo_hb      = datetime.now().timestamp()

        while datetime.now().timestamp() < fim:
            agora = datetime.now().timestamp()

            # ── Heartbeat + health-check a cada 60s ──────────────────────
            if agora - ultimo_hb >= 60:
                ultimo_hb = agora
                _watchdog.notify("WATCHDOG=1")

                vivo = _dashboard_esta_vivo(page)

                if vivo:
                    # dashboard visível — clica pra confirmar interatividade
                    clicou = _tentar_clicar_ultima_atualizacao(page)
                    if clicou:
                        falhas_health = 0
                        logger.info(rotina="heartbeat",
                                    mensagem="Dashboard ativo — botão clicado com sucesso.",
                                    unidade_producao=LINHA)
                    else:
                        falhas_health += 1
                        logger.aviso(rotina="heartbeat",
                                     mensagem=f"Dashboard visível mas clique falhou. Falhas: {falhas_health}/{FALHAS_DASHBOARD_MAX}",
                                     unidade_producao=LINHA)
                else:
                    falhas_health += 1
                    log(f"[health] Dashboard NÃO visível. Falhas: {falhas_health}/{FALHAS_DASHBOARD_MAX}")
                    logger.aviso(rotina="heartbeat",
                                 mensagem=f"Dashboard não visível no health-check. Falhas: {falhas_health}/{FALHAS_DASHBOARD_MAX}",
                                 unidade_producao=LINHA)

                # ── 3 falhas consecutivas → reinicia imediatamente ────────
                if falhas_health >= FALHAS_DASHBOARD_MAX:
                    msg = f"Linha {LINHA} — dashboard morto por {falhas_health} health-checks consecutivos. Reiniciando."
                    log(f"[health] ✗✗ {msg}")
                    telegram(msg)
                    logger.erro(rotina="heartbeat",
                                mensagem=msg,
                                unidade_producao=LINHA)
                    raise RuntimeError(msg)  # sobe para run() → reinicia tudo

            # ── Vigia welcome screen ──────────────────────────────────────
            try:
                visivel = page.locator(xpath_welcome).first.is_visible(timeout=1_000)
            except Exception:
                visivel = False

            if visivel:
                if welcome_desde is None:
                    welcome_desde = datetime.now().timestamp()
                    log("[monitor] Welcome apareceu — iniciando contagem")
                else:
                    parado = int(datetime.now().timestamp() - welcome_desde)
                    log(f"[monitor] Welcome travada há {parado}s (limite 120s)")
                    if parado >= 120:
                        msg = f"Linha {LINHA} travada na welcome há {parado}s. Reiniciando."
                        telegram(msg)
                        logger.erro(rotina="monitorar_dashboard",
                                    mensagem=msg,
                                    unidade_producao=LINHA)
                        raise RuntimeError(msg)
            else:
                if welcome_desde is not None:
                    log("[monitor] Welcome saiu sozinha — contagem zerada")
                    welcome_desde = None

            sleep(10)

        # ── Reload periódico ──────────────────────────────────────────────
        log(f"[monitor] Iniciando reload. Modo: {MODO_ATT}")
        logger.info(rotina="monitorar_dashboard",
                    mensagem=f"Reload periódico acionado. Modo: {MODO_ATT}",
                    unidade_producao=LINHA)

        try:
            if MODO_ATT == "F5":
                page.keyboard.press("F5")
            else:
                page.reload()
            page.wait_for_load_state("networkidle", timeout=90_000)
        except Exception as e:
            log(f"[monitor] Falha durante reload: {e}")
            logger.erro(rotina="monitorar_dashboard",
                        mensagem=f"Falha durante reload periódico: {e}",
                        unidade_producao=LINHA)

        sucesso = tentar_abrir_dashboard_com_retry(
            page, motivo=f"atualização periódica ({MODO_ATT})"
        )

        if not sucesso:
            log("[monitor] Falha no reload periódico. Aguardando 30s.")
            logger.aviso(rotina="monitorar_dashboard",
                         mensagem="Falha no reload periódico. Aguardando 30s.",
                         unidade_producao=LINHA)
            sleep(30)
            continue

        # reload bem-sucedido → zera contador de falhas
        falhas_health = 0
        log("[monitor] Ciclo de atualização concluído. Monitoramento retomado.")
        logger.info(rotina="monitorar_dashboard",
                    mensagem="Reload periódico concluído com sucesso.",
                    unidade_producao=LINHA)

def run():
    logger.info(rotina="run",
                mensagem=f"Processo iniciado para linha '{LINHA}'",
                unidade_producao=LINHA)

    ciclos_consecutivos_rapidos = 0
    ultimo_inicio_ciclo = None

    while True:
        playwright = None
        browser    = None

        try:
            agora = time()

            # ── Detecta reinicializações muito rápidas (crash loop) ──────
            if ultimo_inicio_ciclo and (agora - ultimo_inicio_ciclo) < 30:
                ciclos_consecutivos_rapidos += 1
                if ciclos_consecutivos_rapidos >= 5:
                    espera = min(60 * ciclos_consecutivos_rapidos, 300)
                    log(f"[run] Crash loop detectado ({ciclos_consecutivos_rapidos}x rápidos) — aguardando {espera}s")
                    logger.erro(rotina="run",
                                mensagem=f"Crash loop: {ciclos_consecutivos_rapidos} reinícios rápidos. Backoff {espera}s.",
                                unidade_producao=LINHA)
                    telegram(f"Linha {LINHA} em crash loop ({ciclos_consecutivos_rapidos}x). Aguardando {espera}s.")
                    sleep(espera)
            else:
                ciclos_consecutivos_rapidos = 0

            ultimo_inicio_ciclo = time()

            log("═" * 55)
            log(f"[run] Novo ciclo completo — linha '{LINHA}'")
            telegram(f"Robô linha {LINHA} iniciando")
            logger.info(rotina="run",
                        mensagem="Novo ciclo iniciado. Abrindo Playwright e navegador.",
                        unidade_producao=LINHA)

            playwright = sync_playwright().start()
            logger.debug(rotina="run", mensagem="Playwright iniciado", unidade_producao=LINHA)

            browser = playwright.chromium.launch(
                headless=False,
                args=[
                    "--start-maximized",
                    "--start-fullscreen",
                    "--kiosk",
                    "--disable-dev-shm-usage",
                    "--disable-extensions",
                    "--disable-background-networking",
                    "--disable-background-timer-throttling",
                    "--disable-renderer-backgrounding",
                    "--disable-features=TranslateUI,BlinkGenPropertyTrees",
                    "--disable-ipc-flooding-protection",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disable-default-apps",
                    "--disable-sync",
                    "--metrics-recording-only",
                    "--mute-audio",
                    "--disk-cache-size=33554432",
                    "--media-cache-size=33554432",
                ],
            )
            context = browser.new_context(no_viewport=True)
            page    = context.new_page()
            logger.debug(rotina="run", mensagem="Navegador e página criados", unidade_producao=LINHA)

            # ── Goto com retry ───────────────────────────────────────────
            acao_com_retry(
                descricao="page.goto login",
                funcao=lambda: page.goto(LOGIN_URL, timeout=90_000),
                tentativas=TENTATIVAS_PADRAO,
            )
            sleep(3)

            try:
                subprocess.run(["xdotool", "key", "F11"], timeout=20)
                logger.debug(rotina="run", mensagem="xdotool F11 executado", unidade_producao=LINHA)
            except Exception as e:
                log(f"[run] xdotool F11 falhou (nao critico): {e}")
            sleep(3)

            log("[run] Preenchendo credenciais")
            preencher_role(page, "textbox", "Email:", LOGIN, "campo Email")
            sleep(5)
            preencher_role(page, "textbox", "Senha",  SENHA,  "campo Senha")
            sleep(3)
            clicar_role(page, "button", "Login", "botao Login")

            try:
                page.wait_for_load_state("networkidle", timeout=90_000)
            except Exception as e:
                log(f"[run] networkidle pos-login nao atingido: {e}")
            sleep(3)

            log("[run] Login realizado. Iniciando monitoramento.")
            logger.info(rotina="run",
                        mensagem="Login realizado com sucesso. Iniciando monitoramento.",
                        unidade_producao=LINHA)

            # ── Ciclo principal — só sai daqui por exceção ───────────────
            monitorar_dashboard(page)

        except Exception as e:
            log(f"[run] Erro fatal: {e}")
            telegram(f"Robô linha {LINHA} caiu\nErro: {str(e)}\nReiniciando em 10s...")
            logger.erro(rotina="run",
                        mensagem=f"Erro fatal no ciclo — reiniciando. Erro: {e}",
                        unidade_producao=LINHA)

        finally:
            for obj, nome in [(browser, "browser"), (playwright, "playwright")]:
                if obj is not None:
                    try:
                        obj.stop() if nome == "playwright" else obj.close()
                        log(f"[run] {nome.capitalize()} fechado")
                        logger.debug(rotina="run",
                                     mensagem=f"{nome.capitalize()} encerrado no finally",
                                     unidade_producao=LINHA)
                    except Exception as fe:
                        log(f"[run] Falha ao fechar {nome}: {fe}")
                        logger.aviso(rotina="run",
                                     mensagem=f"Falha ao fechar {nome}: {fe}",
                                     unidade_producao=LINHA)

            log("[run] Reiniciando em 10 segundos...")
            sleep(10)

if __name__ == "__main__":
    try:
        run()
    finally:
        logger.close()
