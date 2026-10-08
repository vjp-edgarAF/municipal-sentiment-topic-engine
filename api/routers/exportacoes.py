import csv
import io
import json
import math
import zipfile
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
)

from api.database import get_connection

router = APIRouter(prefix="/exportacoes", tags=["Exportações"])

MUNICIPIO = "Câmara Municipal da Covilhã"


# ─────────────────────────────────────────────
# FUNÇÕES AUXILIARES
# ─────────────────────────────────────────────

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


def registar_exportacao(tipo: str, utilizador: str = "anonimo"):
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO dbo.Exportacoes (Tipo, Utilizador) VALUES (?, ?)",
            tipo, utilizador
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def get_posts_data(limite: int = 100, fonte: Optional[str] = None,
                   data_inicio: Optional[str] = None, data_fim: Optional[str] = None):
    conn = get_connection()
    cursor = conn.cursor()

    query = """
        SELECT TOP (?)
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
            ta.Topic_Keywords
        FROM [dbo].[Post] p
        JOIN [dbo].[SocialNetwork] sn ON p.SNetwork_ID = sn.SNetwork_ID
        JOIN [dbo].[TextDocument] td ON td.Post_ID = p.Post_ID
        LEFT JOIN [dbo].[SentimentAnalysis] sa ON td.TextDocument_ID = sa.TextDocument_ID
        LEFT JOIN [dbo].[EmotionAnalysis] ea ON td.TextDocument_ID = ea.TextDocument_ID
        LEFT JOIN [dbo].[TopicAssignment] ta ON td.TextDocument_ID = ta.TextDocument_ID
        WHERE 1=1
    """
    params = [limite]

    if fonte:
        query += " AND LOWER(sn.SNetwork_Name) = ?"
        params.append(fonte.lower())
    if data_inicio:
        query += " AND p.CreatedAt >= ?"
        params.append(data_inicio)
    if data_fim:
        query += " AND p.CreatedAt <= ?"
        params.append(data_fim)

    query += " ORDER BY p.CreatedAt DESC"

    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()
    return rows


# ─────────────────────────────────────────────
# ENDPOINTS
# ─────────────────────────────────────────────

@router.get("/csv")
def exportar_csv(
    limite: int = Query(1000, description="Número máximo de posts"),
    fonte: Optional[str] = Query(None),
    data_inicio: Optional[str] = Query(None),
    data_fim: Optional[str] = Query(None),
):
    """Exporta posts para CSV."""
    rows = get_posts_data(limite, fonte, data_inicio, data_fim)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Data", "Conteúdo", "Origem", "Fonte Jornalística", "Tema", "Sentimento", "Prioridade", "Impacto"])

    for r in rows:
        titulo = r[0] or ""
        conteudo = r[1] or ""
        texto = titulo if titulo else conteudo[:100]
        data = str(r[2])[:16] if r[2] else ""
        fonte_jorn = r[5] or r[7]
        tema = r[11] or ""
        sentimento = r[8] or ""
        impacto, prioridade = calcular_impacto_prioridade(r[9], r[8], r[3], r[4])

        writer.writerow([data, texto, fonte_jorn, r[5] or "", tema, sentimento, prioridade, impacto])

    registar_exportacao("CSV")

    output.seek(0)
    filename = f"clipper-mencoes-{datetime.now().strftime('%Y%m%d')}.csv"
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode("utf-8-sig")),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@router.get("/pdf")
def exportar_pdf(
    limite: int = Query(100, description="Número máximo de posts no relatório"),
    fonte: Optional[str] = Query(None),
    data_inicio: Optional[str] = Query(None),
    data_fim: Optional[str] = Query(None),
):
    """Exporta relatório executivo em PDF."""
    rows = get_posts_data(limite, fonte, data_inicio, data_fim)

    # Estatísticas
    total = len(rows)
    negativos = sum(1 for r in rows if r[8] == "NEGATIVE")
    positivos = sum(1 for r in rows if r[8] == "POSITIVE")
    reputacao = round(positivos / total * 100) if total > 0 else 0

    # Contar entidades
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM dbo.NamedEntity")
    total_entidades = cursor.fetchone()[0]
    conn.close()

    # Tópico mais frequente
    temas = {}
    for r in rows:
        tema = r[11] or "Sem tema"
        temas[tema] = temas.get(tema, 0) + 1
    tema_principal = max(temas, key=temas.get) if temas else "N/A"
    tema_count = temas.get(tema_principal, 0)

    # Gerar PDF
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4,
                            rightMargin=2*cm, leftMargin=2*cm,
                            topMargin=2*cm, bottomMargin=2*cm)

    styles = getSampleStyleSheet()
    story = []

    # Cabeçalho
    header_style = ParagraphStyle("header", fontSize=18, fontName="Helvetica-Bold",
                                  textColor=colors.HexColor("#1a7a5e"))
    story.append(Paragraph("Relatório Executivo — Clipper", header_style))
    story.append(Paragraph(MUNICIPIO, styles["Normal"]))
    story.append(Paragraph(datetime.now().strftime("%d/%m/%Y"), styles["Normal"]))
    story.append(Spacer(1, 0.5*cm))

    # Indicadores
    indicadores = [
        ["COBERTURA", "REPUTAÇÃO", "RISCO ATIVO", "ARQUIVO"],
        [str(total), f"{reputacao}%", str(negativos), str(total_entidades)],
        ["menções neste relatório", "sentimento positivo", "menções negativas", "entidades no corpus"]
    ]
    t = Table(indicadores, colWidths=[4*cm, 4*cm, 4*cm, 4*cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0f9f6")),
        ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 1), (-1, 1), 20),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ("FONTSIZE", (0, 2), (-1, 2), 8),
        ("TEXTCOLOR", (0, 2), (-1, 2), colors.grey),
    ]))
    story.append(t)
    story.append(Spacer(1, 0.5*cm))

    # Resumo executivo
    story.append(Paragraph("<b>RESUMO EXECUTIVO</b>", styles["Heading2"]))
    resumo = (f"O relatório analisa {total} menções, com o tema \"{tema_principal}\" "
              f"a liderar com {tema_count} menções. Sentimento positivo em {reputacao}% das menções, "
              f"com {negativos} menções negativas identificadas.")
    story.append(Paragraph(resumo, styles["Normal"]))
    story.append(Spacer(1, 0.5*cm))

    # Tabela de menções
    story.append(Paragraph("<b>MENÇÕES EM DESTAQUE</b>", styles["Heading2"]))

    table_data = [["CONTEÚDO", "ORIGEM", "TEMA", "SENTIMENTO", "PRIORIDADE"]]
    for r in rows[:50]:
        titulo = r[0] or ""
        conteudo = r[1] or ""
        texto = titulo if titulo else conteudo[:80]
        if len(texto) > 80:
            texto = texto[:77] + "..."
        data_str = str(r[2])[:16] if r[2] else ""
        origem = r[5] or r[7] or ""
        tema = r[11] or ""
        sentimento = r[8] or ""
        _, prioridade = calcular_impacto_prioridade(r[9], r[8], r[3], r[4])

        table_data.append([
            f"{texto}\n{data_str}",
            origem,
            tema,
            sentimento,
            prioridade
        ])

    col_widths = [7*cm, 3*cm, 3*cm, 2.5*cm, 2*cm]
    table = Table(table_data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0f9f6")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f9f9f9")]),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#eeeeee")),
    ]))
    story.append(table)

    doc.build(story)
    registar_exportacao("PDF")

    buffer.seek(0)
    filename = f"clipper-relatorio-{datetime.now().strftime('%Y%m%d')}.pdf"
    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@router.get("/bi")
def exportar_bi(
    fonte: Optional[str] = Query(None),
    data_inicio: Optional[str] = Query(None),
    data_fim: Optional[str] = Query(None),
):
    """Exporta snapshot JSON estruturado para Power BI."""
    conn = get_connection()
    cursor = conn.cursor()

    # Sentimentos
    cursor.execute("""
        SELECT sa.Sentiment_Label, COUNT(*) as total
        FROM dbo.SentimentAnalysis sa
        GROUP BY sa.Sentiment_Label
    """)
    sentimentos = {r[0]: r[1] for r in cursor.fetchall()}

    # Tópicos
    cursor.execute("""
        SELECT ta.Topic_Keywords, COUNT(*) as total
        FROM dbo.TopicAssignment ta
        WHERE ta.Topic_Keywords IS NOT NULL
        GROUP BY ta.Topic_Keywords
        ORDER BY total DESC
    """)
    topicos = [{"topico": r[0], "total": r[1]} for r in cursor.fetchall()]

    # Fontes
    cursor.execute("""
        SELECT sn.SNetwork_Name, COUNT(*) as total
        FROM dbo.Post p
        JOIN dbo.SocialNetwork sn ON p.SNetwork_ID = sn.SNetwork_ID
        GROUP BY sn.SNetwork_Name
    """)
    fontes = [{"fonte": r[0], "total": r[1]} for r in cursor.fetchall()]

    # Evolução temporal
    cursor.execute("""
        SELECT FORMAT(p.CreatedAt, 'yyyy-MM') as mes,
               sa.Sentiment_Label, COUNT(*) as total
        FROM dbo.Post p
        JOIN dbo.TextDocument td ON p.Post_ID = td.Post_ID
        JOIN dbo.SentimentAnalysis sa ON td.TextDocument_ID = sa.TextDocument_ID
        WHERE p.CreatedAt IS NOT NULL
        GROUP BY FORMAT(p.CreatedAt, 'yyyy-MM'), sa.Sentiment_Label
        ORDER BY mes
    """)
    evolucao = [{"mes": r[0], "sentimento": r[1], "total": r[2]} for r in cursor.fetchall()]

    conn.close()
    registar_exportacao("BI")

    data = {
        "gerado_em": datetime.now().isoformat(),
        "municipio": MUNICIPIO,
        "sentimentos": sentimentos,
        "topicos": topicos,
        "fontes": fontes,
        "evolucao_temporal": evolucao,
    }

    buffer = io.BytesIO(json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))
    filename = f"clipper-bi-{datetime.now().strftime('%Y%m%d')}.json"
    return StreamingResponse(
        buffer,
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@router.get("/zip")
def exportar_zip(
    limite: int = Query(100),
    fonte: Optional[str] = Query(None),
    data_inicio: Optional[str] = Query(None),
    data_fim: Optional[str] = Query(None),
):
    """Exporta arquivo ZIP com PDF + CSV + JSON BI."""
    rows = get_posts_data(limite, fonte, data_inicio, data_fim)

    # CSV
    csv_output = io.StringIO()
    writer = csv.writer(csv_output)
    writer.writerow(["Data", "Conteúdo", "Origem", "Fonte Jornalística", "Tema", "Sentimento", "Prioridade", "Impacto"])
    for r in rows:
        titulo = r[0] or ""
        conteudo = r[1] or ""
        texto = titulo if titulo else conteudo[:100]
        data = str(r[2])[:16] if r[2] else ""
        impacto, prioridade = calcular_impacto_prioridade(r[9], r[8], r[3], r[4])
        writer.writerow([data, texto, r[5] or r[7], r[5] or "", r[11] or "", r[8] or "", prioridade, impacto])

    # BI JSON
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT sa.Sentiment_Label, COUNT(*) FROM dbo.SentimentAnalysis sa GROUP BY sa.Sentiment_Label")
    sentimentos = {r[0]: r[1] for r in cursor.fetchall()}
    cursor.execute("SELECT ta.Topic_Keywords, COUNT(*) FROM dbo.TopicAssignment ta WHERE ta.Topic_Keywords IS NOT NULL GROUP BY ta.Topic_Keywords ORDER BY COUNT(*) DESC")
    topicos = [{"topico": r[0], "total": r[1]} for r in cursor.fetchall()]
    cursor.execute("SELECT sn.SNetwork_Name, COUNT(*) FROM dbo.Post p JOIN dbo.SocialNetwork sn ON p.SNetwork_ID = sn.SNetwork_ID GROUP BY sn.SNetwork_Name")
    fontes = [{"fonte": r[0], "total": r[1]} for r in cursor.fetchall()]
    conn.close()

    bi_data = {
        "gerado_em": datetime.now().isoformat(),
        "municipio": MUNICIPIO,
        "sentimentos": sentimentos,
        "topicos": topicos,
        "fontes": fontes,
    }

    # Criar ZIP em memória
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"clipper-mencoes-{datetime.now().strftime('%Y%m%d')}.csv",
                    csv_output.getvalue().encode("utf-8-sig"))
        zf.writestr(f"clipper-bi-{datetime.now().strftime('%Y%m%d')}.json",
                    json.dumps(bi_data, ensure_ascii=False, indent=2).encode("utf-8"))

    registar_exportacao("ZIP")

    zip_buffer.seek(0)
    filename = f"clipper-evidencias-{datetime.now().strftime('%Y%m%d')}.zip"
    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@router.get("/estatisticas")
def get_estatisticas():
    """Estatísticas de exportações realizadas."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM dbo.Exportacoes")
    total = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM dbo.Exportacoes WHERE Tipo = 'PDF'")
    pdfs = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM dbo.Exportacoes WHERE Tipo = 'CSV'")
    csvs = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM dbo.Exportacoes WHERE Tipo = 'ZIP'")
    zips = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM dbo.Exportacoes WHERE Tipo = 'BI'")
    bis = cursor.fetchone()[0]

    # Datasets disponíveis — número de tópicos distintos
    cursor.execute("SELECT COUNT(DISTINCT Topic_Keywords) FROM dbo.TopicAssignment WHERE Topic_Keywords IS NOT NULL")
    datasets = cursor.fetchone()[0]

    conn.close()

    return {
        "total_exportacoes": total,
        "pdfs_gerados": pdfs,
        "csvs_exportados": csvs,
        "zips_gerados": zips,
        "bi_exportados": bis,
        "datasets_disponiveis": datasets,
    }


@router.get("/historico")
def get_historico(limite: int = Query(50)):
    """Histórico de exportações realizadas."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT TOP (?) Exportacao_ID, Tipo, Utilizador, CriadaEm
        FROM dbo.Exportacoes
        ORDER BY CriadaEm DESC
    """, limite)
    rows = cursor.fetchall()
    conn.close()

    return [
        {
            "id": r[0],
            "tipo": r[1],
            "utilizador": r[2],
            "data": str(r[3]) if r[3] else None,
        }
        for r in rows
    ]