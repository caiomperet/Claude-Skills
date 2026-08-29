"""
Gerador da Curva de Koch
========================
Gera e plota a curva de Koch (ou o floco de neve de Koch) para um numero de
iteracoes definido pelo usuario.

A curva parte de um segmento de reta. A cada iteracao, todo segmento e
dividido em quatro partes de 1/3 do comprimento original, com o terco do meio
substituido por dois lados de um triangulo equilatero:

    ____        __/\__

Setup
-----
1. Instale as dependencias:
       pip install -r requirements.txt
2. Rode:
       python koch_curve.py

Uso
---
    python koch_curve.py                 # pergunta o numero de iteracoes
    python koch_curve.py 5               # 5 iteracoes, abre a janela do grafico
    python koch_curve.py 4 --floco       # floco de neve (3 curvas em triangulo)
    python koch_curve.py 6 --salvar koch.png
    python koch_curve.py 4 --cor "#c0392b" --espessura 1.5 --fundo white

Cada iteracao multiplica o numero de segmentos por 4, entao o custo cresce
rapido: 8 iteracoes ja sao 65.536 segmentos. O limite pratico fica em torno
de 10 iteracoes.
"""

import argparse
import sys

import matplotlib.pyplot as plt
import numpy as np

MAX_ITERACOES = 12


def koch(pontos, iteracoes):
    """Aplica a regra de Koch a uma polilinha.

    `pontos` e um array (N, 2) com os vertices da polilinha inicial. Retorna um
    array (4^iteracoes * (N-1) + 1, 2) com os vertices da curva resultante.
    """
    pontos = np.asarray(pontos, dtype=float)

    for _ in range(iteracoes):
        inicio = pontos[:-1]
        fim = pontos[1:]
        delta = fim - inicio

        # Os dois pontos que dividem cada segmento em tercos.
        a = inicio + delta / 3.0
        c = inicio + 2.0 * delta / 3.0

        # O pico do triangulo equilatero: gira (delta / 3) em +60 graus (anti-
        # horario) e aplica a partir de `a`. Isso deixa o bico para cima na
        # curva simples e para fora nos lados do floco.
        dx, dy = delta[:, 0] / 3.0, delta[:, 1] / 3.0
        cos60, sen60 = 0.5, np.sqrt(3.0) / 2.0
        b = np.column_stack((
            a[:, 0] + dx * cos60 - dy * sen60,
            a[:, 1] + dx * sen60 + dy * cos60,
        ))

        # Intercala inicio, a, b, c de cada segmento e fecha com o ultimo ponto.
        novos = np.empty((len(inicio) * 4 + 1, 2))
        novos[0:-1:4] = inicio
        novos[1:-1:4] = a
        novos[2:-1:4] = b
        novos[3:-1:4] = c
        novos[-1] = pontos[-1]
        pontos = novos

    return pontos


def curva_de_koch(iteracoes):
    """Curva de Koch sobre o segmento (0, 0) - (1, 0)."""
    return koch([[0.0, 0.0], [1.0, 0.0]], iteracoes)


def floco_de_koch(iteracoes):
    """Floco de neve de Koch: a curva aplicada aos tres lados de um triangulo."""
    altura = np.sqrt(3.0) / 2.0
    triangulo = [
        [0.0, 0.0],
        [0.5, altura],
        [1.0, 0.0],
        [0.0, 0.0],
    ]
    return koch(triangulo, iteracoes)


def plotar(pontos, iteracoes, floco=False, cor="#1f77b4", espessura=1.0,
           fundo="#ffffff", salvar=None, mostrar=True):
    """Desenha a curva com proporcao 1:1, sem eixos."""
    fig, ax = plt.subplots(figsize=(10, 6), facecolor=fundo)
    ax.set_facecolor(fundo)

    ax.plot(pontos[:, 0], pontos[:, 1], color=cor, linewidth=espessura,
            solid_joinstyle="miter", solid_capstyle="butt")

    ax.set_aspect("equal")
    ax.axis("off")
    ax.margins(0.05)

    nome = "Floco de neve de Koch" if floco else "Curva de Koch"
    segmentos = len(pontos) - 1
    ax.set_title(f"{nome} - {iteracoes} iteracoes ({segmentos:,} segmentos)".replace(",", "."),
                 color="#333333", fontsize=13, pad=16)

    fig.tight_layout()

    if salvar:
        fig.savefig(salvar, dpi=200, facecolor=fundo, bbox_inches="tight")
        print(f"Grafico salvo em: {salvar}")

    if mostrar:
        plt.show()
    else:
        plt.close(fig)

    return fig


def perguntar_iteracoes():
    """Pede o numero de iteracoes no terminal ate receber um valor valido."""
    while True:
        try:
            resposta = input(f"Numero de iteracoes (0 a {MAX_ITERACOES}): ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(0)
        try:
            return validar_iteracoes(int(resposta))
        except ValueError as erro:
            print(f"  {erro}")


def validar_iteracoes(valor):
    if valor < 0:
        raise ValueError("O numero de iteracoes nao pode ser negativo.")
    if valor > MAX_ITERACOES:
        raise ValueError(
            f"Maximo de {MAX_ITERACOES} iteracoes "
            f"({4 ** MAX_ITERACOES:,} segmentos ja e pesado demais para plotar).".replace(",", ".")
        )
    return valor


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Plota a curva de Koch para um numero de iteracoes definido.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Exemplo: python koch_curve.py 5 --floco --salvar floco.png",
    )
    parser.add_argument("iteracoes", nargs="?", type=int,
                        help=f"quantidade de iteracoes (0 a {MAX_ITERACOES}); "
                             "se omitido, e perguntado no terminal")
    parser.add_argument("--floco", action="store_true",
                        help="desenha o floco de neve de Koch (curva nos 3 lados de um triangulo)")
    parser.add_argument("--salvar", metavar="ARQUIVO",
                        help="salva a imagem no arquivo indicado (png, pdf, svg...)")
    parser.add_argument("--cor", default="#1f77b4", help="cor da linha (padrao: #1f77b4)")
    parser.add_argument("--espessura", type=float, default=1.0,
                        help="espessura da linha (padrao: 1.0)")
    parser.add_argument("--fundo", default="#ffffff", help="cor do fundo (padrao: branco)")
    parser.add_argument("--sem-janela", action="store_true",
                        help="nao abre a janela do grafico (util junto com --salvar)")
    args = parser.parse_args(argv)

    if args.iteracoes is None:
        iteracoes = perguntar_iteracoes()
    else:
        try:
            iteracoes = validar_iteracoes(args.iteracoes)
        except ValueError as erro:
            parser.error(str(erro))

    pontos = floco_de_koch(iteracoes) if args.floco else curva_de_koch(iteracoes)
    plotar(pontos, iteracoes, floco=args.floco, cor=args.cor,
           espessura=args.espessura, fundo=args.fundo, salvar=args.salvar,
           mostrar=not args.sem_janela)


if __name__ == "__main__":
    main()
