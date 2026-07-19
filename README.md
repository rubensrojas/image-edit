# SRE Image Editor

Um aplicativo simples para alterar o valor de três dígitos de MT/SRE em várias imagens. O aplicativo preserva o sufixo `EMT` identificado por OCR, os nomes dos arquivos e as imagens originais.

## Como usar

1. Arraste arquivos JPG, JPEG, PNG ou WebP para a janela ou clique em **Selecionar imagens**.
2. Informe o número SRE. O valor será preenchido automaticamente com três dígitos.
3. Escolha uma pasta de destino.
4. Clique em **Editar imagens**. Se o OCR falhar em uma imagem, o aplicativo informará o erro e continuará processando as demais.

### Como abrir uma versão não assinada

- **Windows:** se o SmartScreen aparecer, clique em **Mais informações**, confira o nome do aplicativo e escolha **Executar assim mesmo**.
- **macOS:** arraste o aplicativo para a pasta Aplicativos. Na primeira abertura, clique no aplicativo segurando a tecla Control, escolha **Abrir** e confirme. Use apenas versões recebidas de uma fonte confiável.

## Executar pelo código-fonte

Instale o Python 3.12 e o Tesseract 5. Depois, execute:

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
python app.py
```

A interface de linha de comando continua disponível:

```bash
python edit_sre.py input -o output --sre-number 27
```

## Gerar os instaladores

Instale as dependências de desenvolvimento, prepare o Tesseract da plataforma e gere o aplicativo:

```bash
python -m pip install -r requirements-dev.txt
python scripts/create_icons.py
python scripts/stage_tesseract.py
TESSERACT_BUNDLE_DIR="$PWD/build/tesseract" pyinstaller --clean --noconfirm SREImageEditor.spec
```

No macOS, execute `sh scripts/create_dmg.sh`. No Windows, compile `installer/windows.iss` com o Inno Setup 6. Cada versão deve ser gerada em sua própria plataforma: Windows x64, macOS com Apple Silicon e macOS Intel. O GitHub Actions automatiza as três versões.

## Executar os testes

```bash
QT_QPA_PLATFORM=offscreen python -m pytest -q
```
