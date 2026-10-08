from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from api.database import get_connection

router = APIRouter(prefix="/regras", tags=["Regras"])


class RegraUpdate(BaseModel):
    estado: str  # "ativa" ou "inativa"


class RegraCreate(BaseModel):
    descricao: str


@router.get("/")
def get_regras():
    """Lista todas as regras automáticas com estado."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT Regra_ID, Descricao, Estado, CriadaEm
        FROM Regras
        ORDER BY Regra_ID ASC
    """)
    rows = cursor.fetchall()
    conn.close()

    return [
        {
            "id": r[0],
            "descricao": r[1],
            "estado": r[2],
            "criada_em": str(r[3]) if r[3] else None,
        }
        for r in rows
    ]


@router.post("/")
def criar_regra(body: RegraCreate):
    """Cria uma nova regra automática."""
    conn = get_connection()
    cursor = conn.cursor()

    # Verificar duplicados
    cursor.execute("SELECT Regra_ID FROM Regras WHERE Descricao = ?", body.descricao)
    if cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="Já existe uma regra com esta descrição.")

    cursor.execute("""
        INSERT INTO Regras (Descricao, Estado)
        VALUES (?, 'ativa')
    """, body.descricao)
    new_id = cursor.lastrowid
    conn.commit()
    conn.close()

    return {"id": new_id, "descricao": body.descricao, "estado": "ativa"}


@router.put("/{regra_id}")
def atualizar_regra(regra_id: int, body: RegraUpdate):
    """Ativa ou desativa uma regra automática."""
    if body.estado not in ["ativa", "inativa"]:
        raise HTTPException(status_code=400, detail="Estado deve ser 'ativa' ou 'inativa'.")

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT Regra_ID FROM Regras WHERE Regra_ID = ?", regra_id)
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Regra não encontrada.")

    cursor.execute("""
        UPDATE Regras
        SET Estado = ?, AtualizadaEm = NOW()
        WHERE Regra_ID = ?
    """, body.estado, regra_id)
    conn.commit()
    conn.close()

    return {"message": f"Regra {regra_id} atualizada para '{body.estado}'."}


@router.delete("/{regra_id}")
def remover_regra(regra_id: int):
    """Remove uma regra automática."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT Regra_ID FROM Regras WHERE Regra_ID = ?", regra_id)
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Regra não encontrada.")

    cursor.execute("DELETE FROM Regras WHERE Regra_ID = ?", regra_id)
    conn.commit()
    conn.close()

    return {"message": f"Regra {regra_id} removida com sucesso."}