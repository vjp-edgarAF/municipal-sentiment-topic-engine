from fastapi import APIRouter, Query
from typing import Optional
from api.database import get_connection

router = APIRouter(prefix="/keywords", tags=["Keywords"])

@router.get("/mais-frequentes")
def get_mais_frequentes(
    limite: int = Query(20),
    fonte: Optional[str] = Query(None),
    topico_id: Optional[int] = Query(None),
):
    conn   = get_connection()
    cursor = conn.cursor()

    query = """
        SELECT k.Keyword_Text, COUNT(*) as Total
        FROM Keyword k
        JOIN TextDocument td ON k.TextDocument_ID = td.TextDocument_ID
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

    query += " GROUP BY k.Keyword_Text ORDER BY Total DESC LIMIT ?"
    params.append(limite)

    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()

    return [{"keyword": r[0], "total": r[1]} for r in rows]


@router.get("/por-topico")
def get_keywords_por_topico(limite: int = Query(10)):
    conn   = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT ta.Topic_ID, k.Keyword_Text, COUNT(*) as Total
        FROM Keyword k
        JOIN TextDocument td ON k.TextDocument_ID = td.TextDocument_ID
        JOIN TopicAssignment ta ON td.TextDocument_ID = ta.TextDocument_ID
        WHERE ta.Topic_ID != -1
        GROUP BY ta.Topic_ID, k.Keyword_Text
        ORDER BY ta.Topic_ID, Total DESC
    """)
    rows = cursor.fetchall()
    conn.close()

    result = {}
    for topic_id, keyword, total in rows:
        if topic_id not in result:
            result[topic_id] = []
        if len(result[topic_id]) < limite:
            result[topic_id].append({"keyword": keyword, "total": total})

    return result
