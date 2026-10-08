import json
from pathlib import Path
from datetime import datetime
from db_connection import get_connection

BASE_DIR = Path(__file__).parent.parent

MERGED_FILE = BASE_DIR / "data/merged/all_merged.json"
RAW_FILES = {
    "news":    BASE_DIR / "data/raw/news_posts.json",
    "reddit":  BASE_DIR / "data/raw/reddit_posts.json",
    "bluesky": BASE_DIR / "data/raw/bluesky_posts.json",
    "youtube": BASE_DIR / "data/raw/youtube_posts.json",
}

SNETWORK_MAP = {
    "news":     3,
    "reddit":   2,
    "bluesky":  1,
    "youtube":  4,
    "facebook": 5,
}


def load_json_file(path):
    path = Path(path)

    if not path.exists():
        print(f"AVISO: ficheiro não encontrado -> {path}")
        return []

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)



def normalize_platform_id(source, platform_id):
    if source == "youtube" and platform_id and not platform_id.startswith("http"):
        return f"https://www.youtube.com/watch?v={platform_id}"
    return platform_id


def parse_datetime(value):
    if not value:
        return None

    formats = [
        "%a, %d %b %Y %H:%M:%S %Z",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(value.strip(), fmt)
        except (ValueError, AttributeError):
            continue

    return None


def safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default



def get_or_create_user(cursor, handle, snetwork_id):
    if not handle:
        return None

    cursor.execute(
        "SELECT User_ID FROM UserSN WHERE Handle = ? AND SNetwork_ID = ?",
        handle, snetwork_id
    )
    row = cursor.fetchone()

    if row:
        return row[0]

    cursor.execute(
        "INSERT INTO UserSN (Handle, SNetwork_ID) VALUES (?, ?)",
        handle, snetwork_id
    )
    return cursor.lastrowid


def limpar_titulo_e_fonte(title):
    """Remove o nome da fonte do final do título e extrai o source_name."""
    if not title or " - " not in title:
        return title, None
    
    partes = title.rsplit(" - ", 1)
    titulo_limpo = partes[0].strip()
    source_name = partes[1].strip() if len(partes) > 1 else None
    
    return titulo_limpo, source_name


def insert_post(cursor, record, snetwork_id, user_id):
    platform_id = normalize_platform_id(
        record.get("source", ""),
        record.get("platform_id", "")
    )

    metrics = record.get("metrics", {}) or {}

    cursor.execute("""
        INSERT INTO Post (
            Original_External_ID,
            User_ID,
            SNetwork_ID,
            CreatedAt,
            Title,
            Content,
            URL,
            ViewCount,
            LikeCount,
            ReplyCount,
            Source_Name
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
        platform_id,
        user_id,
        snetwork_id,
        parse_datetime(record.get("created_at")),
        record.get("title", "")[:500] if record.get("title") else None,
        record.get("text", ""),
        record.get("url") or record.get("link"),
        safe_int(metrics.get("views", 0)),
        safe_int(metrics.get("likes", metrics.get("upvotes", 0))),
        safe_int(metrics.get("comments", metrics.get("replies", 0))),
        record.get("source_name") or None,
    )

    return cursor.lastrowid


def insert_comment(cursor, comment, post_id, snetwork_id):
    comment_text = (
        comment.get("comment_text")
        or comment.get("text", "")
    )

    author = (
        comment.get("comment_author")
        or comment.get("author", "")
    )

    cursor.execute("""
        INSERT INTO Comment (
            Post_ID,
            Author_Handle,
            Comment_Text,
            Likes_Upvotes,
            CreatedAt
        )
        VALUES (?, ?, ?, ?, ?)
    """,
        post_id,
        author or None,
        comment_text,
        safe_int(
            comment.get("likes_upvotes")
            or comment.get("comment_upvotes")
            or comment.get("likes", 0)
        ),
        parse_datetime(comment.get("created_at")),
    )

    return cursor.lastrowid


def insert_text_document(cursor, post_id, snetwork_id, original_text, clean_text, created_at):
    cursor.execute("""
        INSERT INTO TextDocument (
            Source_Type,
            Post_ID,
            SNetwork_ID,
            Original_Text,
            Clean_Text,
            Municipality,
            CreatedAt
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """,
        "POST",
        post_id,
        snetwork_id,
        original_text,
        clean_text,
        "Covilhã",
        parse_datetime(created_at),
    )

    return cursor.lastrowid


def insert_sentiment(cursor, text_document_id, merged):
    cursor.execute("""
        INSERT INTO SentimentAnalysis (
            TextDocument_ID,
            Sentiment_Label,
            Sentiment_Score,
            Negative,
            Neutral,
            Positive,
            Comments_Polarity,
            Model_Name,
            Model_Version
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
        text_document_id,
        merged.get("sentiment"),
        merged.get("sentiment_score"),
        merged.get("negative"),
        merged.get("neutral"),
        merged.get("positive"),
        merged.get("comments_polarity"),
        "cardiffnlp/twitter-xlm-roberta-base-sentiment",
        "1.0",
    )


def insert_emotion(cursor, text_document_id, merged):
    if not merged.get("dominant_emotion"):
        return

    active_emotions = merged.get("active_emotions", [])
    emotion_scores  = merged.get("emotion_scores", {})

    cursor.execute("""
        INSERT INTO EmotionAnalysis (
            TextDocument_ID,
            Dominant_Emotion,
            Confidence,
            Active_Emotions,
            Emotion_Scores,
            Model_Name,
            Model_Version
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """,
        text_document_id,
        merged.get("dominant_emotion"),
        merged.get("emotion_confidence"),
        ", ".join(active_emotions) if active_emotions else None,
        json.dumps(emotion_scores, ensure_ascii=False) if emotion_scores else None,
        "tabularisai/multilingual-emotion-classification",
        "1.0",
    )


def insert_keywords(cursor, text_document_id, keywords):
    for kw in keywords:
        cursor.execute("""
            INSERT INTO Keyword (
                TextDocument_ID,
                Keyword_Text,
                Score
            )
            VALUES (?, ?, ?)
        """,
            text_document_id,
            kw.get("keyword", "")[:500],
            kw.get("score"),
        )


def insert_entities(cursor, text_document_id, entities):
    for entity in entities:
        cursor.execute("""
            INSERT INTO NamedEntity (
                TextDocument_ID,
                Entity_Text,
                Entity_Label
            )
            VALUES (?, ?, ?)
        """,
            text_document_id,
            entity.get("text", "")[:500],
            entity.get("label", "MISC"),
        )


def insert_topic(cursor, text_document_id, merged):
    topic_id = merged.get("topic_id")

    if topic_id is None:
        return

    # Novo formato Zero-Shot usa topic_label em vez de topic_keywords
    topic_label = merged.get("topic_label", "")
    topic_keywords = merged.get("topic_keywords", [])

    # Usar topic_label se não houver topic_keywords
    if topic_label and not topic_keywords:
        topic_keywords_str = topic_label
    else:
        topic_keywords_str = ", ".join(topic_keywords) if topic_keywords else None

    cursor.execute("""
        DELETE FROM TopicAssignment
        WHERE TextDocument_ID = ?
    """, text_document_id)

    cursor.execute("""
        INSERT INTO TopicAssignment (
            TextDocument_ID,
            Topic_ID,
            Topic_Probability,
            Topic_Keywords,
            Model_Version
        )
        VALUES (?, ?, ?, ?, ?)
    """,
        text_document_id,
        topic_id,
        merged.get("topic_probability"),
        topic_keywords_str,
        "2.0",
    )


def get_existing_platform_ids(cursor):
    cursor.execute(
        "SELECT Original_External_ID FROM Post"
    )
    return {row[0] for row in cursor.fetchall()}


def main():
    print("A CARREGAR DADOS")

    merged_records = load_json_file(MERGED_FILE)
    print(f"Registos merged: {len(merged_records)}")

    raw_index = {}
    for source, path in RAW_FILES.items():
        records = load_json_file(path)
        for record in records:
            pid = normalize_platform_id(
                source,
                str(record.get("platform_id", ""))
            )
            raw_index[pid] = record

    print(f"Registos raw indexados: {len(raw_index)}")
    print("\nA LIGAR À BASE DE DADOS")

    conn = get_connection()

    if conn is None:
        print("ERRO: não foi possível ligar à BD.")
        return

    cursor = conn.cursor()

    existing_ids = get_existing_platform_ids(cursor)
    print(f"Posts já existentes na BD: {len(existing_ids)}")
    print("\nA INSERIR DADOS")

    inserted      = 0
    skipped       = 0
    errors        = 0

    for merged in merged_records:

        platform_id = normalize_platform_id(
            merged.get("source", ""),
            merged.get("platform_id", "")
        )

        if platform_id in existing_ids:
            skipped += 1

            topics_record = merged if merged.get("topic_id") is not None else None
            if topics_record:
                try:
                    cursor.execute("""
                        SELECT TextDocument_ID FROM TextDocument
                        WHERE Post_ID = (
                            SELECT Post_ID FROM Post
                            WHERE Original_External_ID = ?
                        )
                    """, platform_id)
                    row = cursor.fetchone()
                    if row:
                        insert_topic(cursor, row[0], merged)
                        conn.commit()
                except Exception:
                    conn.rollback()

            continue

        source      = merged.get("source", "")
        snetwork_id = SNETWORK_MAP.get(source)

        if not snetwork_id:
            print(f"AVISO: source desconhecido -> {source}")
            errors += 1
            continue

        raw_record = raw_index.get(platform_id, {})

        try:
            author  = raw_record.get("author") or None
            user_id = get_or_create_user(cursor, author, snetwork_id)

            titulo_limpo, source_name_do_titulo = limpar_titulo_e_fonte(raw_record.get("title", ""))
            raw_record["title"] = titulo_limpo
            raw_record["source_name"] = merged.get("source_name") or source_name_do_titulo

            post_id = insert_post(cursor, raw_record or merged, snetwork_id, user_id)
            comments = raw_record.get("comments", [])
            for comment in comments:
                insert_comment(cursor, comment, post_id, snetwork_id)
            original_text = raw_record.get("text", "")
            clean_text    = merged.get("title", "") or original_text

            text_document_id = insert_text_document(
                cursor,
                post_id,
                snetwork_id,
                original_text,
                clean_text,
                merged.get("created_at"),
            )

            insert_sentiment(cursor, text_document_id, merged)
            insert_emotion(cursor, text_document_id, merged)
            insert_keywords(cursor, text_document_id, merged.get("keywords", []))
            insert_entities(cursor, text_document_id, merged.get("entities", []))
            insert_topic(cursor, text_document_id, merged)

            conn.commit()

            existing_ids.add(platform_id)
            inserted += 1

            if inserted % 100 == 0:
                print(f"  Inseridos: {inserted}")

        except Exception as e:
            conn.rollback()
            print(f"\nERRO ao inserir {platform_id}:")
            print(e)
            errors += 1
            continue


    print(f"\nINSERÇÃO CONCLUÍDA")
    print(f"Inseridos:  {inserted}")
    print(f"Ignorados:  {skipped}")
    print(f"Erros:      {errors}")


    print("\nVALIDAÇÃO")
    cursor.execute("SELECT COUNT(*) FROM Post")
    print(f"Posts na BD:              {cursor.fetchone()[0]}")

    cursor.execute("SELECT COUNT(*) FROM Comment")
    print(f"Comentários na BD:        {cursor.fetchone()[0]}")

    cursor.execute("SELECT COUNT(*) FROM TextDocument")
    print(f"TextDocuments na BD:      {cursor.fetchone()[0]}")

    cursor.execute("SELECT COUNT(*) FROM SentimentAnalysis")
    print(f"Sentimentos na BD:        {cursor.fetchone()[0]}")

    cursor.execute("SELECT COUNT(*) FROM EmotionAnalysis")
    print(f"Emoções na BD:            {cursor.fetchone()[0]}")

    cursor.execute("SELECT COUNT(*) FROM Keyword")
    print(f"Keywords na BD:           {cursor.fetchone()[0]}")

    cursor.execute("SELECT COUNT(*) FROM NamedEntity")
    print(f"Entidades na BD:          {cursor.fetchone()[0]}")

    cursor.execute("SELECT COUNT(*) FROM TopicAssignment")
    print(f"Tópicos na BD:            {cursor.fetchone()[0]}")

    conn.close()

    print("\nDB INSERT TERMINADO")


if __name__ == "__main__":
    main()