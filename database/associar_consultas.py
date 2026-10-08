import sys
import unicodedata
from collections import Counter
from db_connection import get_connection


def normalize(text):
    text = (text or "").strip().lower()
    text = "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")
    return text


def run(dry_run):
    conn = get_connection()
    if conn is None:
        print("ERRO: nao foi possivel ligar a BD.")
        return
    cursor = conn.cursor()

    print("A carregar consultas existentes...")
    cursor.execute("SELECT Consulta_ID, Nome FROM Consultas")
    consulta_by_norm = {}
    for consulta_id, nome in cursor.fetchall():
        consulta_by_norm[normalize(nome)] = consulta_id
    print(f"  {len(consulta_by_norm)} consultas especificas.")

    print("\nA carregar temas atribuidos pela IA...")
    cursor.execute("""
        SELECT td.Post_ID, ta.Topic_ID, ta.Topic_Keywords
        FROM TopicAssignment ta
        JOIN TextDocument td ON ta.TextDocument_ID = td.TextDocument_ID
        WHERE td.Source_Type = 'POST' AND td.Post_ID IS NOT NULL
          AND ta.Topic_ID IS NOT NULL AND ta.Topic_ID != -1
    """)
    topic_id_by_post = {}
    topic_labels = {}
    for post_id, topic_id, keywords in cursor.fetchall():
        topic_id_by_post[post_id] = topic_id
        if keywords:
            topic_labels.setdefault(topic_id, Counter())[keywords.strip()] += 1

    print(f"  {len(topic_labels)} temas distintos encontrados.")

    print("\nA garantir uma consulta para cada tema...")
    consulta_by_topic_id = {}
    created_topic_names = []
    for topic_id, label_counts in topic_labels.items():
        nome = label_counts.most_common(1)[0][0]
        norm = normalize(nome)
        if norm in consulta_by_norm:
            consulta_by_topic_id[topic_id] = consulta_by_norm[norm]
            continue
        if dry_run:
            new_id = -1000 - topic_id
        else:
            cursor.execute("""
                INSERT INTO Consultas (Nome, Estado)
                VALUES (?, 'ativa')
            """, nome)
            new_id = cursor.lastrowid
            conn.commit()
        consulta_by_norm[norm] = new_id
        consulta_by_topic_id[topic_id] = new_id
        created_topic_names.append(nome)

    print(f"  {len(created_topic_names)} consultas de tema criadas:")
    for nome in created_topic_names:
        print(f"  - {nome}")

    print("\nA carregar entidades nomeadas por noticia...")
    cursor.execute("""
        SELECT td.Post_ID, ne.Entity_Text
        FROM NamedEntity ne
        JOIN TextDocument td ON ne.TextDocument_ID = td.TextDocument_ID
        WHERE td.Source_Type = 'POST' AND td.Post_ID IS NOT NULL
    """)
    entities_by_post = {}
    for post_id, text in cursor.fetchall():
        entities_by_post.setdefault(post_id, []).append(text)

    cursor.execute("SELECT Post_ID FROM Post")
    all_post_ids = [r[0] for r in cursor.fetchall()]
    print(f"  {len(all_post_ids)} noticias no total.")

    print("\nA associar cada noticia (entidades especificas + tema geral)...")
    associations = []
    matched_count = 0
    for post_id in all_post_ids:
        entities = entities_by_post.get(post_id, [])
        matched_ids = {
            consulta_by_norm[norm]
            for norm in (normalize(e) for e in entities)
            if norm in consulta_by_norm
        }
        topic_id = topic_id_by_post.get(post_id)
        if topic_id in consulta_by_topic_id:
            matched_ids.add(consulta_by_topic_id[topic_id])

        if matched_ids:
            matched_count += 1
        for consulta_id in matched_ids:
            associations.append((post_id, consulta_id))

    print(f"\nNoticias com pelo menos uma consulta: {matched_count} de {len(all_post_ids)} "
          f"({matched_count / len(all_post_ids) * 100:.1f}%)")
    print(f"Associacoes calculadas (total): {len(associations)}")

    if dry_run:
        print("\nMODO DE TESTE: nada foi gravado na base de dados.")
        conn.close()
        return

    print("\nA gravar associacoes na BD...")
    cursor.execute("DELETE FROM PostConsulta")
    for post_id, consulta_id in associations:
        cursor.execute(
            "INSERT INTO PostConsulta (Post_ID, Consulta_ID) VALUES (?, ?)",
            post_id, consulta_id
        )
    conn.commit()
    conn.close()
    print("\nASSOCIACAO DE CONSULTAS TERMINADA")


if __name__ == "__main__":
    dry_run = "--apply" not in sys.argv
    run(dry_run)
