"""
MIGRAÇÃO SQL SERVER -> MYSQL
============================
Lê a estrutura REAL da base de dados SQL Server (tabelas, colunas, chaves,
índices, restrições) — não o sql/tabelas.sql, que está desatualizado —,
recria-a no MySQL e copia todos os dados.

O SQL Server só é LIDO (apenas SELECTs): nada é alterado nele.

Uso (no servidor, com o venv ativo, a partir da raiz do projeto):

    python database\\migrar_mysql.py esquema     # só mostra o que seria criado
    python database\\migrar_mysql.py migrar      # cria as tabelas e copia os dados
    python database\\migrar_mysql.py verificar   # compara contagens nas duas BDs

Origem:  DB_SERVER, DB_NAME, DB_USER, DB_PASSWORD   (.env)
Destino: MYSQL_HOST, MYSQL_PORT, MYSQL_DB, MYSQL_USER, MYSQL_PASSWORD (.env)
"""
import os
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent.parent
SCHEMA_OUTPUT = BASE_DIR / "sql" / "mysql_schema.sql"

BATCH_SIZE = 1000

# Funções T-SQL que não existem em MySQL: restrições CHECK que as usem
# não são recriadas (fica um aviso).
TSQL_FUNCTIONS = re.compile(
    r"\b(getdate|getutcdate|sysdatetime|len|datalength|isnull|convert|"
    r"charindex|patindex|datediff|dateadd|newid|try_cast|iif)\s*\(",
    re.IGNORECASE,
)

FK_ACTIONS = {
    "NO_ACTION": "NO ACTION",
    "CASCADE":   "CASCADE",
    "SET_NULL":  "SET NULL",
    # InnoDB não suporta SET DEFAULT
    "SET_DEFAULT": "NO ACTION",
}

avisos = []


def aviso(msg):
    avisos.append(msg)
    print(f"  AVISO: {msg}")


# ============================================================
# LIGAÇÕES
# ============================================================

def ligar_sqlserver():
    import pyodbc

    conn_str = (
        f"DRIVER={{ODBC Driver 17 for SQL Server}};"
        f"SERVER={os.getenv('DB_SERVER')};"
        f"DATABASE={os.getenv('DB_NAME')};"
        f"UID={os.getenv('DB_USER')};"
        f"PWD={os.getenv('DB_PASSWORD')};"
        f"TrustServerCertificate=yes;"
        f"ApplicationIntent=ReadOnly;"
    )
    return pyodbc.connect(conn_str)


def ligar_mysql():
    import pymysql

    for var in ("MYSQL_HOST", "MYSQL_DB", "MYSQL_USER", "MYSQL_PASSWORD"):
        if not os.getenv(var):
            sys.exit(f"ERRO: falta {var} no .env")

    return pymysql.connect(
        host=os.getenv("MYSQL_HOST"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("MYSQL_USER"),
        password=os.getenv("MYSQL_PASSWORD"),
        database=os.getenv("MYSQL_DB"),
        charset="utf8mb4",
        autocommit=False,
    )


# ============================================================
# LEITURA DA ESTRUTURA DO SQL SERVER
# ============================================================

SQL_COLUNAS = """
SELECT s.name, t.name, c.name, TYPE_NAME(c.system_type_id),
       c.max_length, c.precision, c.scale,
       c.is_nullable, c.is_identity, c.is_computed, dc.definition
FROM sys.tables t
JOIN sys.schemas s ON s.schema_id = t.schema_id
JOIN sys.columns c ON c.object_id = t.object_id
LEFT JOIN sys.default_constraints dc ON dc.object_id = c.default_object_id
WHERE t.is_ms_shipped = 0 AND t.name <> 'sysdiagrams'
ORDER BY s.name, t.name, c.column_id
"""

SQL_INDICES = """
SELECT s.name, t.name, i.name, i.is_primary_key, i.is_unique,
       i.type_desc, i.has_filter, col.name, ic.is_descending_key
FROM sys.indexes i
JOIN sys.tables t ON t.object_id = i.object_id
JOIN sys.schemas s ON s.schema_id = t.schema_id
JOIN sys.index_columns ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id
JOIN sys.columns col ON col.object_id = ic.object_id AND col.column_id = ic.column_id
WHERE t.is_ms_shipped = 0 AND t.name <> 'sysdiagrams'
  AND i.type > 0 AND i.is_hypothetical = 0 AND ic.is_included_column = 0
ORDER BY s.name, t.name, i.index_id, ic.key_ordinal
"""

SQL_FKS = """
SELECT fk.name, ps.name, pt.name, pc.name, rs.name, rt.name, rc.name,
       fk.delete_referential_action_desc, fk.update_referential_action_desc
FROM sys.foreign_keys fk
JOIN sys.foreign_key_columns fkc ON fkc.constraint_object_id = fk.object_id
JOIN sys.tables pt  ON pt.object_id = fk.parent_object_id
JOIN sys.schemas ps ON ps.schema_id = pt.schema_id
JOIN sys.columns pc ON pc.object_id = fkc.parent_object_id AND pc.column_id = fkc.parent_column_id
JOIN sys.tables rt  ON rt.object_id = fk.referenced_object_id
JOIN sys.schemas rs ON rs.schema_id = rt.schema_id
JOIN sys.columns rc ON rc.object_id = fkc.referenced_object_id AND rc.column_id = fkc.referenced_column_id
WHERE fk.is_disabled = 0
ORDER BY fk.name, fkc.constraint_column_id
"""

SQL_CHECKS = """
SELECT s.name, t.name, cc.name, cc.definition
FROM sys.check_constraints cc
JOIN sys.tables t ON t.object_id = cc.parent_object_id
JOIN sys.schemas s ON s.schema_id = t.schema_id
WHERE cc.is_disabled = 0
ORDER BY s.name, t.name, cc.name
"""

SQL_OUTROS_OBJETOS = """
SELECT type_desc, COUNT(*)
FROM sys.objects
WHERE is_ms_shipped = 0 AND type IN ('V', 'P', 'FN', 'IF', 'TF', 'TR')
GROUP BY type_desc
"""


def ler_estrutura(src):
    cur = src.cursor()

    tabelas = {}
    for (schema, tabela, coluna, tipo, max_len, prec, escala,
         nullable, identity, computed, default) in cur.execute(SQL_COLUNAS).fetchall():
        chave = (schema, tabela)
        tabelas.setdefault(chave, {"colunas": [], "indices": {}, "checks": []})
        tabelas[chave]["colunas"].append({
            "nome": coluna, "tipo": tipo.lower(), "max_len": max_len,
            "prec": prec, "escala": escala, "nullable": bool(nullable),
            "identity": bool(identity), "computed": bool(computed),
            "default": default,
        })

    for (schema, tabela, indice, is_pk, is_unique, tipo,
         filtro, coluna, desc) in cur.execute(SQL_INDICES).fetchall():
        t = tabelas.get((schema, tabela))
        if t is None:
            continue
        idx = t["indices"].setdefault(indice, {
            "pk": bool(is_pk), "unique": bool(is_unique),
            "tipo": tipo, "filtro": bool(filtro), "colunas": [],
        })
        idx["colunas"].append((coluna, bool(desc)))

    fks = {}
    for (nome, ps, pt, pc, rs, rt, rc, on_delete, on_update) in cur.execute(SQL_FKS).fetchall():
        fk = fks.setdefault(nome, {
            "tabela": (ps, pt), "ref": (rs, rt), "colunas": [], "ref_colunas": [],
            "on_delete": on_delete, "on_update": on_update,
        })
        fk["colunas"].append(pc)
        fk["ref_colunas"].append(rc)

    for (schema, tabela, nome, definicao) in cur.execute(SQL_CHECKS).fetchall():
        t = tabelas.get((schema, tabela))
        if t is not None:
            t["checks"].append((nome, definicao))

    outros = cur.execute(SQL_OUTROS_OBJETOS).fetchall()

    return tabelas, fks, outros


# ============================================================
# TRADUÇÃO PARA MYSQL
# ============================================================

def nome_mysql(schema, tabela, tabelas):
    """dbo.Post -> Post. Se a mesma tabela existir em dois schemas,
    o do schema não-dbo leva prefixo."""
    if schema == "dbo":
        return tabela
    if ("dbo", tabela) in tabelas:
        return f"{schema}_{tabela}"
    return tabela


def q(nome):
    return "`" + nome.replace("`", "``") + "`"


def colunas_indexadas(t):
    cols = set()
    for idx in t["indices"].values():
        for nome, _ in idx["colunas"]:
            cols.add(nome)
    return cols


def tipo_mysql(col, indexada):
    tipo = col["tipo"]
    max_len = col["max_len"]

    if tipo in ("nvarchar", "nchar"):
        n = -1 if max_len == -1 else max_len // 2
    else:
        n = max_len

    if tipo in ("varchar", "nvarchar", "char", "nchar"):
        if n == -1:
            return "LONGTEXT"
        if tipo in ("char", "nchar"):
            return f"CHAR({n})" if n <= 255 else f"VARCHAR({n})"
        # Colunas compridas e não indexadas passam a TEXT para não
        # ultrapassar o limite de 65 535 bytes por linha do MySQL.
        if n > 1000 and not indexada:
            return "TEXT" if n <= 16000 else "MEDIUMTEXT"
        return f"VARCHAR({n})"

    if tipo in ("text", "ntext", "xml", "sql_variant", "hierarchyid",
                "geography", "geometry"):
        return "LONGTEXT"

    simples = {
        "int": "INT", "bigint": "BIGINT", "smallint": "SMALLINT",
        "tinyint": "TINYINT UNSIGNED", "bit": "TINYINT(1)",
        "float": "DOUBLE", "real": "FLOAT",
        "money": "DECIMAL(19,4)", "smallmoney": "DECIMAL(10,4)",
        "date": "DATE", "uniqueidentifier": "CHAR(36)",
        "image": "LONGBLOB", "timestamp": "BINARY(8)",
    }
    if tipo in simples:
        return simples[tipo]

    if tipo in ("decimal", "numeric"):
        return f"DECIMAL({col['prec']},{col['escala']})"

    if tipo == "datetime":
        return "DATETIME(3)"
    if tipo == "smalldatetime":
        return "DATETIME"
    if tipo in ("datetime2", "datetimeoffset"):
        if tipo == "datetimeoffset":
            aviso(f"coluna {col['nome']}: datetimeoffset perde o fuso horário em MySQL")
        return f"DATETIME({min(col['escala'], 6)})"
    if tipo == "time":
        return f"TIME({min(col['escala'], 6)})"

    if tipo in ("varbinary", "binary"):
        if max_len == -1:
            return "LONGBLOB"
        return f"VARBINARY({max_len})" if tipo == "varbinary" else f"BINARY({max_len})"

    aviso(f"tipo {tipo} da coluna {col['nome']} sem correspondência — usado LONGTEXT")
    return "LONGTEXT"


def fsp(tipo_my):
    m = re.match(r"DATETIME\((\d)\)", tipo_my)
    return int(m.group(1)) if m else 0


def default_mysql(col, tipo_my):
    d = col["default"]
    if d is None:
        return None

    v = d.strip()
    while v.startswith("(") and v.endswith(")"):
        v = v[1:-1].strip()

    low = v.lower()
    p = fsp(tipo_my)
    agora = f"CURRENT_TIMESTAMP({p})" if p else "CURRENT_TIMESTAMP"

    if low in ("getdate()", "sysdatetime()", "current_timestamp"):
        return agora if tipo_my.startswith("DATETIME") else None
    if low in ("getutcdate()", "sysutcdatetime()"):
        return f"(UTC_TIMESTAMP({p}))" if tipo_my.startswith("DATETIME") else None
    if low in ("newid()", "newsequentialid()"):
        return "(UUID())"

    blob = tipo_my.endswith("TEXT") or tipo_my.endswith("BLOB")

    if re.fullmatch(r"-?\d+(\.\d+)?", v):
        return f"('{v}')" if blob else v

    m = re.fullmatch(r"N?'(.*)'", v, re.DOTALL)
    if m:
        literal = "'" + m.group(1).replace("\\", "\\\\") + "'"
        return f"({literal})" if blob else literal

    aviso(f"default {d} da coluna {col['nome']} não traduzido — omitido")
    return None


def traduzir_check(definicao):
    if TSQL_FUNCTIONS.search(definicao):
        return None
    return re.sub(r"\[([^\]]+)\]", lambda m: q(m.group(1)), definicao)


def gerar_ddl(tabelas, fks):
    """Devolve (create_tables, depois_da_copia). As tabelas são criadas só
    com a chave primária; índices, chaves estrangeiras e CHECKs entram
    depois da cópia (mais rápido e sem problemas de ordem)."""
    creates = {}
    depois = []

    for (schema, tabela), t in tabelas.items():
        nome = nome_mysql(schema, tabela, tabelas)
        indexadas = colunas_indexadas(t)
        linhas = []

        for col in t["colunas"]:
            if col["computed"]:
                aviso(f"{nome}.{col['nome']} é coluna calculada — copiada como valor normal")
            tipo_my = tipo_mysql(col, col["nome"] in indexadas)
            col["tipo_mysql"] = tipo_my
            linha = f"    {q(col['nome'])} {tipo_my}"
            linha += " NULL" if col["nullable"] else " NOT NULL"
            if col["identity"]:
                linha += " AUTO_INCREMENT"
            else:
                d = default_mysql(col, tipo_my)
                if d is not None:
                    linha += f" DEFAULT {d}"
            linhas.append(linha)

        pk = next((i for i in t["indices"].values() if i["pk"]), None)
        if pk:
            linhas.append("    PRIMARY KEY (" + ", ".join(q(c) for c, _ in pk["colunas"]) + ")")
        else:
            aviso(f"tabela {nome} não tem chave primária")
            ident = next((c for c in t["colunas"] if c["identity"]), None)
            if ident:
                # AUTO_INCREMENT em MySQL tem de ser chave
                linhas.append(f"    KEY ({q(ident['nome'])})")

        creates[nome] = (
            f"CREATE TABLE {q(nome)} (\n" + ",\n".join(linhas) + "\n)"
            " ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci"
        )

        tipos = {c["nome"]: c["tipo_mysql"] for c in t["colunas"]}
        for idx_nome, idx in t["indices"].items():
            if idx["pk"]:
                continue
            if "COLUMNSTORE" in idx["tipo"]:
                aviso(f"índice columnstore {idx_nome} em {nome} ignorado")
                continue
            unique = idx["unique"]
            if idx["filtro"]:
                aviso(f"índice {idx_nome} em {nome} tinha filtro (WHERE) — criado sem filtro")
            partes = []
            for c, desc in idx["colunas"]:
                tipo_c = tipos.get(c, "")
                # Um índice InnoDB tem no máximo 3072 bytes (768 caracteres
                # utf8mb4): nas colunas longas indexa-se só o início.
                m_len = re.match(r"VARCHAR\((\d+)\)", tipo_c)
                prefixo = ""
                if tipo_c.endswith("TEXT"):
                    prefixo = "(255)"
                elif m_len and int(m_len.group(1)) > 768:
                    prefixo = "(768)"
                if prefixo and unique:
                    aviso(f"índice único {idx_nome} em {nome} sobre coluna longa — "
                          f"a unicidade passa a valer só para os primeiros caracteres")
                partes.append(q(c) + prefixo + (" DESC" if desc else ""))
            depois.append(
                f"CREATE {'UNIQUE ' if unique else ''}INDEX {q(idx_nome[:64])} "
                f"ON {q(nome)} ({', '.join(partes)})"
            )

        for chk_nome, definicao in t["checks"]:
            traduzida = traduzir_check(definicao)
            if traduzida is None:
                aviso(f"CHECK {chk_nome} em {nome} usa funções T-SQL — não recriado: {definicao}")
                continue
            depois.append(
                f"ALTER TABLE {q(nome)} ADD CONSTRAINT {q(chk_nome[:64])} CHECK {traduzida}"
            )

    for fk_nome, fk in fks.items():
        if fk["tabela"] not in tabelas or fk["ref"] not in tabelas:
            continue
        tab = nome_mysql(*fk["tabela"], tabelas)
        ref = nome_mysql(*fk["ref"], tabelas)
        on_delete = FK_ACTIONS.get(fk["on_delete"], "NO ACTION")
        on_update = FK_ACTIONS.get(fk["on_update"], "NO ACTION")
        depois.append(
            f"ALTER TABLE {q(tab)} ADD CONSTRAINT {q(fk_nome[:64])} "
            f"FOREIGN KEY ({', '.join(q(c) for c in fk['colunas'])}) "
            f"REFERENCES {q(ref)} ({', '.join(q(c) for c in fk['ref_colunas'])}) "
            f"ON DELETE {on_delete} ON UPDATE {on_update}"
        )

    return creates, depois


def guardar_ddl(creates, depois):
    SCHEMA_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(SCHEMA_OUTPUT, "w", encoding="utf-8") as f:
        f.write(f"-- Gerado por database/migrar_mysql.py em {datetime.now():%Y-%m-%d %H:%M}\n")
        f.write("-- a partir da estrutura real do SQL Server.\n\n")
        for ddl in creates.values():
            f.write(ddl + ";\n\n")
        f.write("-- Índices, chaves estrangeiras e CHECKs (aplicados após a cópia)\n")
        for ddl in depois:
            f.write(ddl + ";\n")
    print(f"\nEstrutura MySQL guardada em: {SCHEMA_OUTPUT}")


# ============================================================
# CÓPIA DOS DADOS
# ============================================================

def expressao_select(col):
    nome = "[" + col["nome"].replace("]", "]]") + "]"
    if col["tipo"] == "datetimeoffset":
        return f"CONVERT(datetime2, {nome})"
    if col["tipo"] in ("sql_variant", "xml", "hierarchyid", "geography", "geometry"):
        return f"CAST({nome} AS NVARCHAR(MAX))"
    return nome


def converter_valor(v):
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, datetime) and v.tzinfo is not None:
        return v.replace(tzinfo=None)
    return v


def copiar_tabela(src, dst, schema, tabela, t, nome):
    colunas = t["colunas"]
    select = ", ".join(expressao_select(c) for c in colunas)

    pk = next((i for i in t["indices"].values() if i["pk"]), None)
    ordem = ""
    if pk:
        ordem = " ORDER BY " + ", ".join("[" + c + "]" for c, _ in pk["colunas"])

    insert = (
        f"INSERT INTO {q(nome)} ({', '.join(q(c['nome']) for c in colunas)}) "
        f"VALUES ({', '.join(['%s'] * len(colunas))})"
    )

    cur_src = src.cursor()
    cur_src.execute(f"SELECT {select} FROM [{schema}].[{tabela}]{ordem}")
    cur_dst = dst.cursor()

    total = 0
    while True:
        linhas = cur_src.fetchmany(BATCH_SIZE)
        if not linhas:
            break
        cur_dst.executemany(insert, [tuple(converter_valor(v) for v in linha) for linha in linhas])
        dst.commit()
        total += len(linhas)
        print(f"\r  {nome}: {total} linhas", end="", flush=True)

    print(f"\r  {nome}: {total} linhas copiadas")
    return total


# ============================================================
# COMANDOS
# ============================================================

def mostrar_outros_objetos(outros):
    if outros:
        print("\nOutros objetos no SQL Server (NÃO são migrados automaticamente):")
        for tipo, n in outros:
            print(f"  - {tipo}: {n}")
            aviso(f"existem {n} objeto(s) do tipo {tipo} que é preciso migrar à mão")


def cmd_esquema():
    print("A ler a estrutura do SQL Server...")
    src = ligar_sqlserver()
    tabelas, fks, outros = ler_estrutura(src)
    src.close()

    print(f"{len(tabelas)} tabelas, {len(fks)} chaves estrangeiras\n")
    creates, depois = gerar_ddl(tabelas, fks)
    for ddl in creates.values():
        print(ddl + ";\n")
    for ddl in depois:
        print(ddl + ";")
    mostrar_outros_objetos(outros)
    guardar_ddl(creates, depois)


def cmd_migrar(substituir):
    print("A ler a estrutura do SQL Server...")
    src = ligar_sqlserver()
    tabelas, fks, outros = ler_estrutura(src)
    creates, depois = gerar_ddl(tabelas, fks)
    guardar_ddl(creates, depois)

    dst = ligar_mysql()
    cur = dst.cursor()

    # Proteção: nunca apagar dados que já estejam no MySQL (por exemplo,
    # depois de a API passar a escrever nele) sem pedido explícito.
    cur.execute("SHOW TABLES")
    existentes = {r[0].lower() for r in cur.fetchall()}
    a_criar = {n.lower() for n in creates}
    com_dados = []
    for nome in sorted(existentes & a_criar):
        cur.execute(f"SELECT EXISTS(SELECT 1 FROM {q(nome)})")
        if cur.fetchone()[0]:
            com_dados.append(nome)
    if com_dados and not substituir:
        sys.exit(
            "\nERRO: estas tabelas já têm dados no MySQL: " + ", ".join(com_dados) +
            "\nNada foi alterado. Para as apagar e copiar de novo do SQL Server,"
            "\ncorre outra vez com:  python database\\migrar_mysql.py migrar --substituir"
        )

    cur.execute("SET SESSION sql_mode = 'STRICT_TRANS_TABLES,NO_AUTO_VALUE_ON_ZERO,NO_ENGINE_SUBSTITUTION'")
    cur.execute("SET FOREIGN_KEY_CHECKS = 0")
    cur.execute("SET UNIQUE_CHECKS = 0")

    print("\nA criar as tabelas no MySQL...")
    for nome, ddl in creates.items():
        cur.execute(f"DROP TABLE IF EXISTS {q(nome)}")
        cur.execute(ddl)
        print(f"  OK: {nome}")

    print("\nA copiar os dados...")
    for (schema, tabela), t in tabelas.items():
        copiar_tabela(src, dst, schema, tabela, t, nome_mysql(schema, tabela, tabelas))

    print("\nA criar índices, chaves estrangeiras e restrições...")
    cur.execute("SET UNIQUE_CHECKS = 1")
    cur.execute("SET FOREIGN_KEY_CHECKS = 1")
    for ddl in depois:
        try:
            cur.execute(ddl)
        except Exception as e:
            aviso(f"falhou: {ddl}\n         -> {e}")
    dst.commit()

    src.close()
    dst.close()

    mostrar_outros_objetos(outros)
    print("\nMIGRAÇÃO CONCLUÍDA")
    cmd_verificar()


def cmd_verificar():
    print("\nA comparar SQL Server e MySQL...")
    src = ligar_sqlserver()
    tabelas, _, _ = ler_estrutura(src)
    dst = ligar_mysql()
    cs, cd = src.cursor(), dst.cursor()

    diferencas = 0
    print(f"  {'Tabela':<32}{'SQL Server':>12}{'MySQL':>12}")
    for (schema, tabela), t in tabelas.items():
        nome = nome_mysql(schema, tabela, tabelas)
        n_src = cs.execute(f"SELECT COUNT_BIG(*) FROM [{schema}].[{tabela}]").fetchone()[0]
        try:
            cd.execute(f"SELECT COUNT(*) FROM {q(nome)}")
            n_dst = cd.fetchone()[0]
        except Exception:
            n_dst = None
        marca = "" if n_src == n_dst else "   <-- DIFERENTE"
        if marca:
            diferencas += 1
        print(f"  {nome:<32}{n_src:>12}{str(n_dst):>12}{marca}")

    src.close()
    dst.close()

    if avisos:
        print(f"\n{len(avisos)} aviso(s) — ver acima.")
    if diferencas:
        print(f"\n{diferencas} tabela(s) com contagens diferentes.")
        sys.exit(1)
    print("\nTodas as tabelas têm o mesmo número de linhas nas duas bases de dados.")


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("esquema", "migrar", "verificar"):
        print(__doc__)
        sys.exit(1)

    comando = sys.argv[1]
    if comando == "esquema":
        cmd_esquema()
    elif comando == "migrar":
        cmd_migrar(substituir="--substituir" in sys.argv)
    else:
        cmd_verificar()


if __name__ == "__main__":
    main()
