# SRE Image Editor

Um aplicativo desktop para editar a terceira linha da legenda de várias imagens. Por padrão, ele altera apenas o número MT/SRE e preserva o sufixo `EMT` identificado por OCR. O modo **Editar linha completa** permite substituir toda a terceira linha pelo mesmo texto.

## Como usar

1. Arraste arquivos JPG, JPEG, PNG ou WebP para a janela ou clique em **Selecionar imagens** e escolha **Continuar**.
2. Informe o número SRE ou ative **Editar linha completa**. Nesse modo, confira e ajuste o texto detectado na primeira imagem.
3. Compare as imagens **Antes** e **Depois** e escolha **Aplicar edição em todas as imagens**.
4. Acompanhe o progresso do lote. Você pode cancelar com segurança enquanto ele é processado.
5. Quando todas as imagens estiverem prontas, salve-as em uma pasta ou em um arquivo ZIP.

Os originais nunca são alterados. Se qualquer imagem falhar, o lote inteiro é interrompido e a tela de resultado indica quais arquivos precisam ser revisados.

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

A interface de linha de comando continua disponível para a edição numérica:

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

No macOS, execute `sh scripts/create_dmg.sh`. No Windows, compile `installer/windows.iss` com o Inno Setup 6. Cada versão deve ser gerada em sua própria plataforma: Windows x64 ou macOS com Apple Silicon. Na aba Actions do GitHub, os workflows manuais **Build Windows installer** e **Build macOS Apple Silicon installer** geram cada instalador separadamente. Macs Intel não são suportados.

## Executar os testes

```bash
QT_QPA_PLATFORM=offscreen python -m pytest -q
```
