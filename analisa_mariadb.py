"""Levantamento de bancos e tabelas do MySQL/MariaDB.

Lê as credenciais de inicio.py (formato "chave: valor"), imprime os resumos
no terminal e exporta a lista completa de tabelas para tabelas_mariadb.csv.

Uso:
    python analisa_mariadb.py
"""

import csv
import os
import shutil
from decimal import Decimal
from pathlib import Path

import pymysql

PASTA = Path(__file__).resolve().parent
ARQ_CREDENCIAIS = PASTA / "inicio.py"
ARQ_CSV = PASTA / "tabelas_mariadb.csv"
BANCOS_SISTEMA = ("information_schema", "mysql", "performance_schema", "sys")


def ler_credenciais(caminho):
    cred = {}
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        if ":" in linha:
            chave, valor = linha.split(":", 1)
            cred[chave.strip().lower()] = valor.strip()
    return cred


def conectar(cred):
    return pymysql.connect(
        host=cred.get("host", "localhost"),
        port=int(cred.get("porta", 3306)),
        user=cred.get("usuario", "root"),
        password=cred.get("senha", ""),
        charset="utf8mb4",
    )


def consultar(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        colunas = [d[0] for d in cur.description]
        return colunas, cur.fetchall()


def fmt(valor):
    if valor is None:
        return "NULL"
    if isinstance(valor, Decimal) and valor == valor.to_integral_value() and valor.as_tuple().exponent == 0:
        valor = int(valor)
    if isinstance(valor, float) or hasattr(valor, "as_tuple"):  # float/Decimal
        return f"{float(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    if isinstance(valor, int):
        return f"{valor:,}".replace(",", ".")
    return str(valor)


def valor_csv(valor):
    # Vírgula decimal para abrir direto no Excel em pt-BR
    if valor is None:
        return ""
    if isinstance(valor, (float, Decimal)):
        return str(valor).replace(".", ",")
    return valor


def imprimir_tabela(titulo, colunas, linhas):
    print(f"\n=== {titulo} ===")
    texto = [[fmt(v) for v in linha] for linha in linhas]
    larguras = [max([len(c)] + [len(l[i]) for l in texto]) for i, c in enumerate(colunas)]
    sep = "+" + "+".join("-" * (w + 2) for w in larguras) + "+"
    print(sep)
    print("| " + " | ".join(c.ljust(w) for c, w in zip(colunas, larguras)) + " |")
    print(sep)
    for linha, original in zip(texto, linhas):
        celulas = [
            v.rjust(w) if isinstance(o, (int, float)) or hasattr(o, "as_tuple") else v.ljust(w)
            for v, w, o in zip(linha, larguras, original)
        ]
        print("| " + " | ".join(celulas) + " |")
    print(sep)


def tamanho_pasta(caminho):
    total = 0
    for raiz, _, arquivos in os.walk(caminho):
        for nome in arquivos:
            try:
                total += os.path.getsize(os.path.join(raiz, nome))
            except OSError:
                pass
    return total


def main():
    conn = conectar(ler_credenciais(ARQ_CREDENCIAIS))
    try:
        _, [(versao, datadir, file_per_table)] = consultar(
            conn, "SELECT VERSION(), @@datadir, @@innodb_file_per_table"
        )
        imprimir_tabela(
            "Servidor",
            ["versao", "datadir", "file_per_table"],
            [(versao, datadir, file_per_table)],
        )

        imprimir_tabela("Resumo por banco", *consultar(conn, """
            SELECT IFNULL(table_schema, 'TOTAL') banco, COUNT(*) tabelas,
                   SUM(table_rows) linhas_aprox,
                   ROUND(SUM(data_length)/1048576, 2) dados_MB,
                   ROUND(SUM(index_length)/1048576, 2) indices_MB,
                   ROUND(SUM(data_length + index_length)/1048576, 2) total_MB,
                   ROUND(SUM(data_free)/1048576, 2) livre_MB
            FROM information_schema.tables
            WHERE table_type = 'BASE TABLE'
            GROUP BY table_schema WITH ROLLUP"""))

        imprimir_tabela("Objetos por tipo", *consultar(conn, """
            SELECT table_schema banco, table_type tipo, COUNT(*) qtd
            FROM information_schema.tables
            GROUP BY table_schema, table_type
            ORDER BY table_schema, table_type"""))

        imprimir_tabela("Por engine", *consultar(conn, """
            SELECT engine, COUNT(*) tabelas,
                   ROUND(SUM(data_length + index_length)/1073741824, 2) total_GB
            FROM information_schema.tables
            WHERE table_type = 'BASE TABLE'
            GROUP BY engine"""))

        imprimir_tabela("Tabelas vazias (table_rows = 0) por banco", *consultar(conn, """
            SELECT table_schema banco, CAST(SUM(table_rows = 0) AS UNSIGNED) vazias,
                   COUNT(*) total
            FROM information_schema.tables
            WHERE table_type = 'BASE TABLE'
              AND table_schema NOT IN %s
            GROUP BY table_schema""", (BANCOS_SISTEMA,)))

        imprimir_tabela("Bancos sem nenhuma tabela", *consultar(conn, """
            SELECT s.schema_name banco
            FROM information_schema.schemata s
            LEFT JOIN information_schema.tables t ON t.table_schema = s.schema_name
            WHERE t.table_name IS NULL
            ORDER BY s.schema_name"""))

        imprimir_tabela("30 maiores tabelas", *consultar(conn, """
            SELECT table_schema banco, table_name tabela, engine,
                   table_rows linhas_aprox,
                   ROUND(data_length/1048576, 1) dados_MB,
                   ROUND(index_length/1048576, 1) idx_MB,
                   ROUND((data_length + index_length)/1048576, 1) total_MB
            FROM information_schema.tables
            WHERE table_type = 'BASE TABLE'
            ORDER BY data_length + index_length DESC
            LIMIT 30"""))

        # Espaço real em disco (só funciona com o servidor na mesma máquina)
        pasta_dados = Path(datadir)
        if pasta_dados.is_dir():
            por_banco = sorted(
                ((p.name, tamanho_pasta(p) / 1024**3) for p in pasta_dados.iterdir() if p.is_dir()),
                key=lambda x: x[1], reverse=True,
            )
            imprimir_tabela("Disco real por banco (datadir)", ["banco", "GB"], por_banco)
            print(f"Total datadir: {fmt(tamanho_pasta(pasta_dados) / 1024**3)} GB")

            arquivos = sorted(
                (p for p in pasta_dados.iterdir() if p.is_file()),
                key=lambda p: p.stat().st_size, reverse=True,
            )[:8]
            imprimir_tabela(
                "Maiores arquivos na raiz do datadir",
                ["arquivo", "MB"],
                [(p.name, p.stat().st_size / 1024**2) for p in arquivos],
            )

            disco = shutil.disk_usage(pasta_dados)
            imprimir_tabela(
                f"Unidade {pasta_dados.anchor}",
                ["usado_GB", "livre_GB", "total_GB"],
                [(disco.used / 1024**3, disco.free / 1024**3, disco.total / 1024**3)],
            )
        else:
            print(f"\n(datadir {datadir} não acessível localmente; tamanho em disco ignorado)")

        # Lista completa para o CSV
        colunas, linhas = consultar(conn, """
            SELECT table_schema banco, table_name tabela, table_type tipo, engine,
                   row_format, table_rows linhas_aprox,
                   ROUND(avg_row_length) bytes_media_linha,
                   ROUND(data_length/1048576, 2) dados_MB,
                   ROUND(index_length/1048576, 2) indices_MB,
                   ROUND((data_length + index_length)/1048576, 2) total_MB,
                   ROUND(data_free/1048576, 2) livre_MB,
                   auto_increment, table_collation, create_time, update_time
            FROM information_schema.tables
            WHERE table_schema <> 'information_schema'
            ORDER BY table_schema, table_name""")
        with open(ARQ_CSV, "w", newline="", encoding="utf-8-sig") as f:
            escritor = csv.writer(f, delimiter=";")
            escritor.writerow(colunas)
            escritor.writerows([valor_csv(v) for v in linha] for linha in linhas)
        print(f"\nCSV gerado: {ARQ_CSV} ({len(linhas)} objetos)")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
