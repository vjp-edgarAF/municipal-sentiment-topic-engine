from fastapi import APIRouter, Query
from typing import Optional
from api.database import get_connection

router = APIRouter(prefix="/sentimentos", tags=["Sentimentos"])

@router.get("/distribuicao")
def get_distribuicao(
    fonte: Optional[str] = Query(None, description="Filtrar por fonte: news, reddit, bluesky, youtube"),
    data_inicio: Optional[str] = Query(None, description="Data início (YYYY-MM-DD)"),
    data_fim: Optional[str] = Query(None, description="Data fim (YYYY-MM-DD)"),
):
    """Distribuição de sentimentos — positivo, negativo, neutro."""
    conn   = get_connection()
    cursor = conn.cursor()

    query = """
        SELECT sa.Sentiment_Label, COUNT(*) as Total
        FROM SentimentAnalysis sa
        JOIN TextDocument td ON sa.TextDocument_ID = td.TextDocument_ID
        JOIN Post p ON td.Post_ID = p.Post_ID
        JOIN SocialNetwork sn ON p.SNetwork_ID = sn.SNetwork_ID
        WHERE 1=1
    """
    params = []

    if fonte:
        from api.database import FONTE_MAP
        query += " AND LOWER(sn.SNetwork_Name) = ?"
        params.append(FONTE_MAP.get(fonte.lower(), fonte.lower()))
    if data_inicio:
        query += " AND p.CreatedAt >= ?"
        params.append(data_inicio)
    if data_fim:
        query += " AND p.CreatedAt <= ?"
        params.append(data_fim)

    query += " GROUP BY sa.Sentiment_Label ORDER BY Total DESC"

    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()

    total = sum(r[1] for r in rows)
    return [
        {
            "sentimento": r[0],
            "total": r[1],
            "percentagem": round(r[1] / total * 100, 1) if total > 0 else 0,
        }
        for r in rows
    ]


@router.get("/por-fonte")
def get_por_fonte(
    data_inicio: Optional[str] = Query(None),
    data_fim: Optional[str] = Query(None),
):
    """Distribuição de sentimentos por fonte."""
    conn   = get_connection()
    cursor = conn.cursor()

    query = """
        SELECT sn.SNetwork_Name, sa.Sentiment_Label, COUNT(*) as Total
        FROM SentimentAnalysis sa
        JOIN TextDocument td ON sa.TextDocument_ID = td.TextDocument_ID
        JOIN Post p ON td.Post_ID = p.Post_ID
        JOIN SocialNetwork sn ON p.SNetwork_ID = sn.SNetwork_ID
        WHERE 1=1
    """
    params = []

    if data_inicio:
        query += " AND p.CreatedAt >= ?"
        params.append(data_inicio)
    if data_fim:
        query += " AND p.CreatedAt <= ?"
        params.append(data_fim)

    query += " GROUP BY sn.SNetwork_Name, sa.Sentiment_Label ORDER BY sn.SNetwork_Name, Total DESC"

    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()

    result = {}
    for fonte, sentimento, total in rows:
        if fonte not in result:
            result[fonte] = []
        result[fonte].append({"sentimento": sentimento, "total": total})

    return result


@router.get("/evolucao-temporal")
def get_evolucao_temporal(
    fonte: Optional[str] = Query(None),
    granularidade: str = Query("mes", description="dia, semana ou mes"),
):
    """Evolução do sentimento ao longo do tempo."""
    conn   = get_connection()
    cursor = conn.cursor()

    if granularidade == "dia":
        date_format = "DATE_FORMAT(p.CreatedAt, '%Y-%m-%d')"
    elif granularidade == "semana":
        # WEEK(..., 3) = semana ISO, como o DATEPART(ISO_WEEK) do SQL Server
        date_format = "CONCAT(YEAR(p.CreatedAt), '-W', WEEK(p.CreatedAt, 3))"
    else:
        date_format = "DATE_FORMAT(p.CreatedAt, '%Y-%m')"

    query = f"""
        SELECT {date_format} as Periodo, sa.Sentiment_Label, COUNT(*) as Total
        FROM SentimentAnalysis sa
        JOIN TextDocument td ON sa.TextDocument_ID = td.TextDocument_ID
        JOIN Post p ON td.Post_ID = p.Post_ID
        JOIN SocialNetwork sn ON p.SNetwork_ID = sn.SNetwork_ID
        WHERE p.CreatedAt IS NOT NULL
    """
    params = []

    if fonte:
        from api.database import FONTE_MAP
        query += " AND LOWER(sn.SNetwork_Name) = ?"
        params.append(FONTE_MAP.get(fonte.lower(), fonte.lower()))

    query += f" GROUP BY {date_format}, sa.Sentiment_Label ORDER BY Periodo"

    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()

    result = {}
    for periodo, sentimento, total in rows:
        if periodo not in result:
            result[periodo] = {}
        result[periodo][sentimento] = total

    return [{"periodo": k, **v} for k, v in result.items()]


@router.get("/score-medio")
def get_score_medio(
    fonte: Optional[str] = Query(None),
    topico_id: Optional[int] = Query(None),
):
    """Score médio de polaridade (positivo - negativo)."""
    conn   = get_connection()
    cursor = conn.cursor()

    query = """
        SELECT AVG(sa.Sentiment_Score) as Score_Medio,
               MIN(sa.Sentiment_Score) as Score_Min,
               MAX(sa.Sentiment_Score) as Score_Max
        FROM SentimentAnalysis sa
        JOIN TextDocument td ON sa.TextDocument_ID = td.TextDocument_ID
        JOIN Post p ON td.Post_ID = p.Post_ID
        JOIN SocialNetwork sn ON p.SNetwork_ID = sn.SNetwork_ID
    """
    params = []
    conditions = []

    if topico_id is not None:
        query += " JOIN TopicAssignment ta ON td.TextDocument_ID = ta.TextDocument_ID"
        conditions.append("ta.Topic_ID = ?")
        params.append(topico_id)
    if fonte:
        from api.database import FONTE_MAP
        conditions.append("LOWER(sn.SNetwork_Name) = ?")
        params.append(FONTE_MAP.get(fonte.lower(), fonte.lower()))

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    cursor.execute(query, params)
    row = cursor.fetchone()
    conn.close()

    return {
        "score_medio": round(row[0], 4) if row[0] else None,
        "score_min":   round(row[1], 4) if row[1] else None,
        "score_max":   round(row[2], 4) if row[2] else None,
    }
