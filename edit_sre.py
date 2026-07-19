#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from sre_processor import collect_files, collision_safe_path, edit_image


def main() -> None:
    parser = argparse.ArgumentParser(description="Edite rótulos SRE em uma imagem ou pasta.")
    parser.add_argument("input", help="Arquivo de imagem ou pasta")
    parser.add_argument("-o", "--output", default="output", help="Pasta de destino")
    parser.add_argument(
        "-n",
        "--sre-number",
        type=int,
        default=10,
        choices=range(1000),
        metavar="000-999",
        help="Número SRE de três dígitos (padrão: 010)",
    )
    args = parser.parse_args()

    files = collect_files(Path(args.input))
    if not files:
        raise RuntimeError(f"Nenhuma imagem compatível encontrada em {args.input}")

    output_dir = Path(args.output)
    for file in files:
        output_path = collision_safe_path(output_dir, file.name)
        print(f"Editando {file.name} -> {output_path}")
        edit_image(file, output_path, args.sre_number)
    print("Concluído.")


if __name__ == "__main__":
    main()
