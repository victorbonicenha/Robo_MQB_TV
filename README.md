# 📊 Automação Dashboard OEE (Playwright)

Este projeto automatiza o acesso e a navegação no **Dashboard OEE Protótipo** da plataforma DataDriven, utilizando **Playwright**.

O robô:
- Faz login automático com credenciais armazenadas em um `.env`.
- Navega pelo menu lateral até o dashboard desejado.
- Interage com botões dentro de um `iframe` (refresh, tela cheia, fechar modal).
- Abre os detalhes da linha configurada.
- Ajusta a tela para exibição ideal (F11 e zoom).
- Mantém a página ativa indefinidamente até ser encerrado manualmente.

---

## 📂 Estrutura do Projeto
📁 OEE_Dashboard  
│  
├── 📜 main.py — código principal (automação)  
├── 🔑 .env — credenciais e variáveis de ambiente (não subir no GitHub)  
├── 📦 requirements.txt — dependências do projeto  
└── 📘 README.md — documentação  

---

## ⚙️ Pré-requisitos
- Python **3.9+**  
- Playwright instalado com navegadores  
- PyAutoGUI configurado (precisa de acesso à tela, então não funciona em servidores headless sem display)  
- `.env` com credenciais de acesso à plataforma  

---

## 📥 Instalação e Uso

### 1. Clone este repositório
```bash
git clone https://github.com/seuusuario/OEE_Dashboard_Bot.git
cd OEE_Dashboard_Bot
```
---

2. Crie e ative um ambiente virtual
```bash
# Windows
python -m venv venv
venv\Scripts\activate

# Linux / Mac
python3 -m venv venv
source venv/bin/activate
```

---

3. Instale as dependências
```bash
pip install -r requirements.txt
playwright install
```

---

🔑 Configuração do arquivo .env
Na raiz do projeto, crie um arquivo chamado .env com o seguinte conteúdo:
```bash
Login=seu_email_aqui
senha=sua_senha_aqui
NTH=0   # índice da linha "Detalhes" que deseja abrir
```

---

▶️ Execução
Para rodar o robô:
```bash
python main.py
