import os
import sys
import boto3
import subprocess
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent

R2_FILES = [
    "data/raw/news_posts.json",
    "data/raw/reddit_posts.json",
    "data/raw/bluesky_posts.json",
    "data/raw/youtube_posts.json",
]

# Resultados do processamento (tudo em data/ exceto data/raw/).
# Ficam no R2 e não no Git: o all_merged.json ultrapassou o limite de
# 100 MB do GitHub e o git push passou a falhar.
OUTPUTS_PREFIX = "data/"
RAW_PREFIX = "data/raw/"

PIPELINE_STEPS = [
    {
        "name": "Limpeza de texto",
        "script": BASE_DIR / "analysis/text_cleaning.py",
    },
    {
        "name": "Extração de keywords",
        "script": BASE_DIR / "analysis/keywords_extraction.py",
    },
    {
        "name": "Análise de sentimentos",
        "script": BASE_DIR / "analysis/sentiment_analysis.py",
    },
    {
        "name": "Análise de emoções",
        "script": BASE_DIR / "analysis/emotion_analysis.py",
    },
    {
        "name": "Extração de entidades (NER)",
        "script": BASE_DIR / "analysis/ner_extraction.py",
    },
    {
        "name": "Classificação de tópicos (Zero-Shot)", 
        "script": BASE_DIR / "zeroshot_topics.py",     
    },
    {
        "name": "Cruzamento multimodal (merge)",
        "script": BASE_DIR / "merge.py",
    },
]


def get_r2_client():
    return boto3.client(
        "s3",
        endpoint_url=os.getenv("R2_ENDPOINT_URL"),
        aws_access_key_id=os.getenv("R2_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("R2_SECRET_ACCESS_KEY"),
        region_name="auto",
    )


def download_from_r2():
    print("=" * 60)
    print("A DESCARREGAR DADOS DO R2")
    print("=" * 60)

    client = get_r2_client()
    bucket = os.getenv("R2_BUCKET_NAME")

    downloaded = 0
    skipped = 0

    for file_path in R2_FILES:
        local_path = BASE_DIR / file_path

        local_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            response = client.head_object(
                Bucket=bucket,
                Key=file_path,
            )
            r2_size = response["ContentLength"]

            if local_path.exists():
                local_size = local_path.stat().st_size
                if local_size == r2_size:
                    print(f"SEM ALTERAÇÕES: {file_path}")
                    skipped += 1
                    continue

            client.download_file(
                bucket,
                file_path,
                str(local_path),
            )
            print(f"DOWNLOAD OK: {file_path}")
            downloaded += 1

        except Exception as e:
            if "404" in str(e) or "NoSuchKey" in str(e):
                print(f"AVISO: {file_path} não encontrado no R2")
            else:
                print(f"ERRO ao descarregar {file_path}: {e}")

    print(f"\nDescarregados: {downloaded}")
    print(f"Sem alterações: {skipped}")

    return downloaded


def download_outputs_from_r2():
    """Repõe os resultados da última execução, para o processamento
    incremental não recomeçar do zero. Se ainda não houver nada no R2
    (primeira execução), mantêm-se os ficheiros que vieram do Git."""
    print("=" * 60)
    print("A DESCARREGAR RESULTADOS ANTERIORES DO R2")
    print("=" * 60)

    client = get_r2_client()
    bucket = os.getenv("R2_BUCKET_NAME")

    total = 0
    paginator = client.get_paginator("list_objects_v2")

    for page in paginator.paginate(Bucket=bucket, Prefix=OUTPUTS_PREFIX):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.startswith(RAW_PREFIX) or key.endswith("/"):
                continue

            local_path = BASE_DIR / key
            local_path.parent.mkdir(parents=True, exist_ok=True)
            client.download_file(bucket, key, str(local_path))
            print(f"DOWNLOAD OK: {key}")
            total += 1

    if total == 0:
        print("Nenhum resultado no R2 — a usar os ficheiros do repositório.")

    return total


def upload_outputs_to_r2():
    print("=" * 60)
    print("A ENVIAR RESULTADOS PARA O R2")
    print("=" * 60)

    client = get_r2_client()
    bucket = os.getenv("R2_BUCKET_NAME")

    total = 0
    for local_path in sorted((BASE_DIR / "data").rglob("*")):
        if not local_path.is_file():
            continue

        key = local_path.relative_to(BASE_DIR).as_posix()
        if key.startswith(RAW_PREFIX):
            continue

        client.upload_file(str(local_path), bucket, key)
        print(f"UPLOAD OK: {key}")
        total += 1

    print(f"\nEnviados: {total}")
    return total


def run_script(name, script_path):
    print(f"A CORRER: {name}")

    if not script_path.exists():
        print(f"ERRO: script não encontrado -> {script_path}")
        return False

    result = subprocess.run(
        [sys.executable, str(script_path)],
        cwd=str(BASE_DIR),
    )

    if result.returncode != 0:
        print(f"ERRO: {name} falhou com código {result.returncode}")
        return False

    print(f"OK: {name} concluído")
    return True


def main():
    print("\n")
    print("MUNICIPAL SENTIMENT PIPELINE")

    downloaded = download_from_r2()

    if downloaded == 0:
        print("\nNenhum ficheiro novo no R2.")
        print("Pipeline terminado sem processamento.")
        return


    print(f"\n{downloaded} ficheiro(s) novo(s) — a iniciar pipeline")

    download_outputs_from_r2()

    failed = []

    for step in PIPELINE_STEPS:
        success = run_script(step["name"], step["script"])
        if not success:
            failed.append(step["name"])


    print("\n")
    print("PIPELINE CONCLUÍDO")

    total = len(PIPELINE_STEPS)
    succeeded = total - len(failed)

    print(f"Passos concluídos: {succeeded}/{total}")

    if failed:
        print(f"Passos com erro:")
        for step in failed:
            print(f"  - {step}")
        # Não se enviam resultados parciais para o R2: o servidor
        # continua com os da última execução completa.
        print("\nResultados NÃO enviados para o R2. Verifica os erros acima.")
        sys.exit(1)

    upload_outputs_to_r2()

    print("\nTodos os passos concluídos com sucesso.")
    print("O servidor descarrega os resultados do R2 na próxima atualização.")


if __name__ == "__main__":
    main()