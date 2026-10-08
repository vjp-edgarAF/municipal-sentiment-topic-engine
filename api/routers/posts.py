import math
from fastapi import APIRouter, Query, Depends
from typing import Optional
from api.database import get_connection
from api.auth import require_admin


def calcular_impacto_prioridade(sentiment_score, sentiment_label, likes, respostas):
    score = abs(sentiment_score) if sentiment_score else 0
    engagement = (likes or 0) + (respostas or 0) * 2
    engagement_norm = min(30, round(math.log1p(engagement) * 8))
    impacto = max(0, min(100, round(score * 70) + engagement_norm))

    if sentiment_label == "NEGATIVE":
        prioridade = "Alta"
    elif sentiment_label == "POSITIVE":
        prioridade = "Baixa"
    else:
        prioridade = "Média"

    return impacto, prioridade

router = APIRouter(prefix="/posts", tags=["Posts"])

@router.get("/")
def get_posts(
    fonte: Optional[str] = Query(None),
    sentimento: Optional[str] = Query(None, description="POSITIVE, NEGATIVE ou NEUTRAL"),
    emocao: Optional[str] = Query(None),
    topico_id: Optional[int] = Query(None),
    data_inicio: Optional[str] = Query(None),
    data_fim: Optional[str] = Query(None),
    source_name: Optional[str] = Query(None, description="Nome da fonte jornalística"),
    pagina: int = Query(1, ge=1),
    limite: int = Query(20, le=100),
):
    conn   = get_connection()
    cursor = conn.cursor()

    offset = (pagina - 1) * limite

    query = """
        SELECT
            p.Original_External_ID,
            p.Title,
            p.Content,
            p.CreatedAt,
            p.LikeCount,
            p.ReplyCount,
            p.Source_Name,
            p.URL,
            sn.SNetwork_Name,
            sa.Sentiment_Label,
            sa.Sentiment_Score,
            ea.Dominant_Emotion,
            ea.Active_Emotions,
            ta.Topic_ID,
            ta.Topic_Keywords
        FROM [dbo].[Post] p
        JOIN [dbo].[SocialNetwork] sn ON p.SNetwork_ID = sn.SNetwork_ID
        JOIN [dbo].[TextDocument] td ON td.Post_ID = p.Post_ID
        LEFT JOIN [dbo].[SentimentAnalysis] sa ON td.TextDocument_ID = sa.TextDocument_ID
        LEFT JOIN [dbo].[EmotionAnalysis] ea ON td.TextDocument_ID = ea.TextDocument_ID
        LEFT JOIN [dbo].[TopicAssignment] ta ON td.TextDocument_ID = ta.TextDocument_ID
        WHERE 1=1
    """
    params = []

    if fonte:
        from api.database import FONTE_MAP
        query += " AND LOWER(sn.SNetwork_Name) = ?"
        params.append(FONTE_MAP.get(fonte.lower(), fonte.lower()))
    if sentimento:
        query += " AND sa.Sentiment_Label = ?"
        params.append(sentimento.upper())
    if emocao:
        query += " AND ea.Dominant_Emotion = ?"
        params.append(emocao.upper())
    if topico_id is not None:
        query += " AND ta.Topic_ID = ?"
        params.append(topico_id)
    if data_inicio:
        query += " AND p.CreatedAt >= ?"
        params.append(data_inicio)
    if data_fim:
        query += " AND p.CreatedAt <= ?"
        params.append(data_fim)
    if source_name:
        query += " AND p.Source_Name LIKE ?"
        params.append(f"%{source_name}%")

    query += f" ORDER BY p.CreatedAt DESC OFFSET {offset} ROWS FETCH NEXT {limite} ROWS ONLY"

    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()

    return [
        {
            "platform_id":      r[0],
            "titulo":           r[1],
            "conteudo":         r[2],
            "data":             str(r[3]) if r[3] else None,
            "likes":            r[4],
            "respostas":        r[5],
            "fonte_jornalistica": r[6],
            "url":              r[7],
            "fonte":            r[8],
            "sentimento":       r[9],
            "sentiment_score":  round(r[10], 4) if r[10] else None,
            "emocao_dominante": r[11],
            "emocoes_ativas":   r[12].split(", ") if r[12] else [],
            "topico_id":        r[13],
            "topico_keywords":  r[14].split(", ") if r[14] else [],
            "impacto":          calcular_impacto_prioridade(r[10], r[9], r[4], r[5])[0],
            "prioridade":       calcular_impacto_prioridade(r[10], r[9], r[4], r[5])[1],
        }
        for r in rows
    ]


@router.get("/estatisticas")
def get_estatisticas():
    """Estatísticas gerais do corpus."""
    conn   = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            (SELECT COUNT(*) FROM [dbo].[Post]) as total_posts,
            (SELECT COUNT(*) FROM [dbo].[Comment]) as total_comentarios,
            (SELECT COUNT(*) FROM [dbo].[Keyword]) as total_keywords,
            (SELECT COUNT(*) FROM [dbo].[NamedEntity]) as total_entidades,
            (SELECT COUNT(DISTINCT Topic_ID) FROM [dbo].[TopicAssignment] WHERE Topic_ID != -1) as total_topicos,
            (SELECT MIN(CreatedAt) FROM [dbo].[Post] WHERE CreatedAt IS NOT NULL) as data_inicio,
            (SELECT MAX(CreatedAt) FROM [dbo].[Post] WHERE CreatedAt IS NOT NULL) as data_fim
    """)
    row = cursor.fetchone()

    cursor.execute("""
        SELECT sn.SNetwork_Name, COUNT(*) as Total
        FROM [dbo].[Post] p
        JOIN [dbo].[SocialNetwork] sn ON p.SNetwork_ID = sn.SNetwork_ID
        GROUP BY sn.SNetwork_Name
        ORDER BY Total DESC
    """)
    fontes = cursor.fetchall()
    conn.close()

    return {
        "total_posts":        row[0],
        "total_comentarios":  row[1],
        "total_keywords":     row[2],
        "total_entidades":    row[3],
        "total_topicos":      row[4],
        "data_inicio":        str(row[5]) if row[5] else None,
        "data_fim":           str(row[6]) if row[6] else None,
        "posts_por_fonte":    [{"fonte": r[0], "total": r[1]} for r in fontes],
    }


@router.get("/fontes-jornalisticas")
def get_fontes_jornalisticas(limite: int = Query(20)):
    """Fontes jornalísticas mais frequentes."""
    conn   = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT TOP (?) Source_Name, COUNT(*) as Total
        FROM [dbo].[Post]
        WHERE Source_Name IS NOT NULL
        GROUP BY Source_Name
        ORDER BY Total DESC
    """, limite)
    rows = cursor.fetchall()
    conn.close()

    return [{"fonte": r[0], "total": r[1]} for r in rows]
