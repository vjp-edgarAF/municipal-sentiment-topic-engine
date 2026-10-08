import os
import pymysql
from dotenv import load_dotenv

load_dotenv()


MYSQL_HOST     = os.getenv("MYSQL_HOST", "localhost")
MYSQL_PORT     = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_DB       = os.getenv("MYSQL_DB")
MYSQL_USER     = os.getenv("MYSQL_USER")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD")


class Cursor:
    """Cursor do PyMySQL com a interface do pyodbc que o código já usa:
    parâmetros com '?', passados soltos ou numa lista, e execute()
    a devolver o próprio cursor."""

    def __init__(self, cursor):
        self._cursor = cursor

    def execute(self, query, *params):
        if len(params) == 1 and isinstance(params[0], (list, tuple)):
            params = params[0]

        if params:
            # O PyMySQL usa %s e trata '%' como especial quando há parâmetros
            query = query.replace("%", "%%").replace("?", "%s")
            self._cursor.execute(query, tuple(params))
        else:
            self._cursor.execute(query)
        return self

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def fetchmany(self, size):
        return self._cursor.fetchmany(size)

    @property
    def lastrowid(self):
        return self._cursor.lastrowid

    @property
    def rowcount(self):
        return self._cursor.rowcount

    def close(self):
        self._cursor.close()


class Connection:
    def __init__(self, conn):
        self._conn = conn

    def cursor(self):
        return Cursor(self._conn.cursor())

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


def connect():
    """Abre uma ligação ao MySQL; levanta exceção se falhar."""
    return Connection(pymysql.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DB,
        charset="utf8mb4",
        autocommit=False,
    ))


def get_connection():
    try:
        return connect()

    except pymysql.Error as e:
        print(f"ERRO AO LIGAR À BASE DE DADOS:")
        print(e)
        return None


def test_connection():
    print(f"A ligar a: {MYSQL_HOST}:{MYSQL_PORT} / {MYSQL_DB}")
    conn = get_connection()

    if conn is None:
        print("FALHOU — não foi possível ligar à base de dados.")
        return False

    try:
        cursor = conn.cursor()
        cursor.execute("SELECT VERSION()")
        row = cursor.fetchone()
        print(f"LIGAÇÃO OK")
        print(f"MySQL: {row[0]}")
        return True

    except pymysql.Error as e:
        print(f"ERRO AO TESTAR LIGAÇÃO:")
        print(e)
        return False

    finally:
        conn.close()


if __name__ == "__main__":
    test_connection()
