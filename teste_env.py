import requests
import json
import ssl
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
            num_pools=connections,
            maxsize=maxsize,
            block=block,
            ssl_context=self.ssl_context,
            **pool_kwargs
        )

def get_token():
    url = "https://datadriven.datawake.com.br:8058/oauth/token"
    payload = "username=admin%40datawake.com.br&password=%25H%3F%401PA%26!zAD2&grant_type=password"
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": "Basic cGFyYW5vYTpQYXJhbm9hIyEyMDIy"
    }
    session = requests.Session()
    session.mount("https://", CustomSSLAdapter())
    try:
        response = session.post(url, data=payload, headers=headers, verify=False)
        data = json.loads(response.text)
        return data['access_token']
    except Exception as e:
        print(f"Erro token: {e}")
        return ''

def busca_env(token_datadriven_):
    key = '0AA74526-433C-4106-8408-9514B25A00C1'
    url = (
        f'https://datadriven.datawake.com.br:8058/v1/technical_sheet/{key}/data'
        # f'?sort=asc&page=0&size=100&orderColumnName=env_tabelas'
        # f'&fieldsColumns=env_tabelas%40null%2Cunidade_producao%40null'  # adicionei aqui
        # f'&order=asc&draw=1&columns%5B0%5D%5Bdata%5D=env_tabelas&start=0&length=100'
        # f'&search%5Bvalue%5D=&search%5Bregex%5D=false'
    )
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + token_datadriven_
    }
    session = requests.Session()
    session.mount("https://", CustomSSLAdapter())
    try:
        response = session.get(url, headers=headers, verify=False)
        if response.status_code == 200:
            data_ = response.json()
            print(json.dumps(data_, indent=2))
            return data_
        else:
            print(f"Erro: {response.status_code}")
            print(response.text)
            return None
    except Exception as e:
        print(f"Erro busca env: {e}")
        return None

#token = get_token()
#data = busca_env(token)

def parse_env(env_string):
    resultado = {}
    for linha in env_string.split('\r\n'):
        linha = linha.strip()
        if '=' in linha:
            chave, valor = linha.split('=', 1)
            resultado[chave.strip()] = valor.strip()
    return resultado

def extract_envs(data_):
    resultado = {}
    for record in data_['content']:
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
            resultado[uniprod] = parse_env(env_string)
            
    # Retorna o dicionário principal ordenado de forma alfabética pelos produtos (uniprod)
    return dict(sorted(resultado.items()))

# --- Uso do código ---

token = get_token()
data = busca_env(token)
envs = extract_envs(data)

# Ver tudo na ordem correta:
for linha, valores in envs.items():
    print(f"\n--- {linha} ---")
    
    # O 'sorted()' aqui garante que as chaves internas (Login, senha...) também fiquem organizadas
    for k, v in sorted(valores.items()):
        print(f"  {k} = {v}")


# No final do config_loader.py, troca o bloco de uso por isso:

def carregar_envs() -> list:
    """
    Retorna lista no formato:
    [
        {"linha": "MQL001", "Login": "...", "senha": "...", "Telegram_Token": "...", ...},
        {"linha": "MQL002", ...},
    ]
    """
    token = get_token()
    data  = busca_env(token)
    envs  = extract_envs(data)

    lista = []
    for linha, config in envs.items():
        item = {"linha": linha}
        item.update(config)  # joga todas as chaves do env direto no dict
        lista.append(item)

    return lista
