from fastapi import APIRouter, Query
from typing import Optional
from api.database import get_connection

router = APIRouter(prefix="/entidades", tags=["Entidades"])

@router.get("/mais-frequentes")
def get_mais_frequentes(
    limite: int = Query(10),
    tipo: Optional[str] = Query(None, description="PER, ORG, LOC ou MISC"),
    fonte: Optional[str] = Query(None),
):
    conn   = get_connection()
    cursor = conn.cursor()

    query = """
        SELECT ne.Entity_Text, ne.Entity_Label, COUNT(*) as Total
        FROM NamedEntity ne
        JOIN TextDocument td ON ne.TextDocument_ID = td.TextDocument_ID
        JOIN Post p ON td.Post_ID = p.Post_ID
        JOIN SocialNetwork sn ON p.SNetwork_ID = sn.SNetwork_ID
        WHERE 1=1
    """
    params = []

    if tipo:
        query += " AND ne.Entity_Label = ?"
        params.append(tipo.upper())
    if fonte:
        from api.database import FONTE_MAP
        query += " AND LOWER(sn.SNetwork_Name) = ?"
        params.append(FONTE_MAP.get(fonte.lower(), fonte.lower()))

    query += " GROUP BY ne.Entity_Text, ne.Entity_Label ORDER BY Total DESC LIMIT ?"
    params.append(limite)

    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()

    return [{"entidade": r[0], "tipo": r[1], "total": r[2]} for r in rows]


@router.get("/distribuicao-tipo")
def get_distribuicao_tipo(fonte: Optional[str] = Query(None)):
    conn   = get_connection()
    cursor = conn.cursor()

    query = """
        SELECT ne.Entity_Label, COUNT(*) as Total
        FROM NamedEntity ne
        JOIN TextDocument td ON ne.TextDocument_ID = td.TextDocument_ID
        JOIN Post p ON td.Post_ID = p.Post_ID
        JOIN SocialNetwork sn ON p.SNetwork_ID = sn.SNetwork_ID
        WHERE 1=1
    """
    params = []

    if fonte:
        from api.database import FONTE_MAP
        query += " AND LOWER(sn.SNetwork_Name) = ?"
        params.append(FONTE_MAP.get(fonte.lower(), fonte.lower()))

    query += " GROUP BY ne.Entity_Label ORDER BY Total DESC"

    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()

    total = sum(r[1] for r in rows)
    return [
        {
            "tipo": r[0],
            "total": r[1],
            "percentagem": round(r[1] / total * 100, 1) if total > 0 else 0,
        }
        for r in rows
    ]


@router.get("/por-topico")
def get_entidades_por_topico(
    topico_id: int = Query(...),
    limite: int = Query(10),
):
    conn   = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT ne.Entity_Text, ne.Entity_Label, COUNT(*) as Total
        FROM NamedEntity ne
        JOIN TextDocument td ON ne.TextDocument_ID = td.TextDocument_ID
        JOIN TopicAssignment ta ON td.TextDocument_ID = ta.TextDocument_ID
        WHERE ta.Topic_ID = ?
        GROUP BY ne.Entity_Text, ne.Entity_Label
        ORDER BY Total DESC
        LIMIT ?
    """, topico_id, limite)
    rows = cursor.fetchall()
    conn.close()

    return [{"entidade": r[0], "tipo": r[1], "total": r[2]} for r in rows]
