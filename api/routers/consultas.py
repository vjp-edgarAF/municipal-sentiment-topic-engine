from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from api.database import get_connection

router = APIRouter(prefix="/consultas", tags=["Consultas"])


class ConsultaCreate(BaseModel):
    nome: str


class ConsultaUpdate(BaseModel):
    estado: str  # "ativa" ou "inativa"


@router.get("/")
def get_consultas():
    """Lista todas as consultas com número de resultados calculado dinamicamente."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT Consulta_ID, Nome, Estado, CriadaEm FROM Consultas ORDER BY CriadaEm ASC")
    consultas = cursor.fetchall()

    resultado = []
    for c in consultas:
        consulta_id, nome, estado, criada_em = c

        # Calcular número de resultados dinamicamente
        cursor.execute("""
            SELECT COUNT(*) FROM Post
            WHERE Title LIKE ? OR Content LIKE ?
        """, f"%{nome}%", f"%{nome}%")
        total = cursor.fetchone()[0]

        resultado.append({
            "id": consulta_id,
            "nome": nome,
            "estado": estado,
            "total_resultados": total,
            "criada_em": str(criada_em) if criada_em else None,
        })

    conn.close()
    return resultado


@router.post("/")
def criar_consulta(body: ConsultaCreate):
    """Cria uma nova consulta de monitorização."""
    conn = get_connection()
    cursor = conn.cursor()

    # Verificar se já existe
    cursor.execute("SELECT Consulta_ID FROM Consultas WHERE Nome = ?", body.nome)
    if cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="Já existe uma consulta com este nome.")

    cursor.execute("""
        INSERT INTO Consultas (Nome, Estado)
        VALUES (?, 'ativa')
    """, body.nome)
    new_id = cursor.lastrowid
    conn.commit()
    conn.close()

    return {"id": new_id, "nome": body.nome, "estado": "ativa", "total_resultados": 0}


@router.put("/{consulta_id}")
def atualizar_consulta(consulta_id: int, body: ConsultaUpdate):
    """Ativa ou pausa uma consulta."""
    if body.estado not in ["ativa", "inativa"]:
        raise HTTPException(status_code=400, detail="Estado deve ser 'ativa' ou 'inativa'.")

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT Consulta_ID FROM Consultas WHERE Consulta_ID = ?", consulta_id)
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Consulta não encontrada.")

    cursor.execute("""
        UPDATE Consultas
        SET Estado = ?, AtualizadaEm = NOW()
        WHERE Consulta_ID = ?
    """, body.estado, consulta_id)
    conn.commit()
    conn.close()

    return {"message": f"Consulta {consulta_id} atualizada para '{body.estado}'."}


@router.delete("/{consulta_id}")
def remover_consulta(consulta_id: int):
    """Remove uma consulta de monitorização."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT Consulta_ID FROM Consultas WHERE Consulta_ID = ?", consulta_id)
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Consulta não encontrada.")

    cursor.execute("DELETE FROM Consultas WHERE Consulta_ID = ?", consulta_id)
    conn.commit()
    conn.close()

    return {"message": f"Consulta {consulta_id} removida com sucesso."}
