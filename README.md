# 📸 Comparador de Fotos com IA

Aplicativo desktop rápido e inteligente para detecção de duplicatas e fotos semelhantes utilizando Inteligência Artificial (OpenCLIP ViT-B-32), pHash perceptual e SHA-256 exato.

---

## ✨ Funcionalidades Principais

- **🚀 Análise Progressiva em Camadas**:
  - **Camada 1 (Exato)**: SHA-256 instantâneo para cópias idênticas byte-a-byte.
  - **Camada 2 (Visual Rápido)**: pHash (Perceptual Hash) com árvore BK para variações de resolução e compressão.
  - **Camada 3 (IA Semântica)**: OpenCLIP ViT-B-32 com aceleração CUDA/GPU e busca KNN para fotos tiradas em sequência, ângulos parecidos ou iluminação diferente.
- **🖼️ Classificação de Memes e Fotos Web**: Detector Zero-Shot para isolar memes, capturas de tela e figurinhas de redes sociais.
- **🖥️ Comparador em Tela Cheia**: Visualização lado a lado ocupando 100% da tela, com atalhos de teclado ágeis (`1`, `2`, `3`, `4`, `*`, `Page Up`, `Page Down`).
- **🛡️ 100% Seguro**: Exclusão via Lixeira do Windows (`send2trash`), prevenindo perdas acidentais de arquivos.
- **🔄 Auto-Updater Integrado**: Verifica e instala novas versões automaticamente através do GitHub Releases.
- **⚡ Instalador de IA Sob Demanda**: Executável inicial leve que instala os pacotes de GPU/IA sob demanda no primeiro uso com barra de progresso.

---

## 🛠️ Como Executar

### 1. Executável Pré-compilado (Recomendado)
Baixe o executável mais recente na aba [Releases](https://github.com/guilhermebsouza1982/comparador_fotos/releases).

### 2. A partir do Código-Fonte (Python 3.10+)

```bash
# Clone o repositório
git clone https://github.com/guilhermebsouza1982/comparador_fotos.git
cd comparador_fotos

# Crie e ative um ambiente virtual (opcional)
python -m venv venv
venv\Scripts\activate

# Instale as dependências
pip install -r requirements.txt

# Para suporte a GPU / IA (CUDA 12.4):
pip install -r requirements-cuda.txt

# Inicie o aplicativo
python comparador_fotos.py
```

---

## 🧪 Testes de Regressão

Execute a suíte completa de testes de estabilidade e segurança:

```bash
python -m harness.test_suite
```

---

## 📄 Licença
Distribuído sob a licença MIT.
