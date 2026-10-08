"""
Descarrega do R2 os ficheiros de que o database/db_insert.py precisa:
os dados raw e o resultado do pipeline (all_merged.json).

Corre no servidor, a partir do update_db.ps1, em vez do antigo
download_raw.py. O all_merged.json deixou de vir pelo git pull porque
passou o limite de 100 MB do GitHub; o pipeline envia-o para o R2.
"""
import os
import sys
import boto3
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent

R2_FILES = [
    "data/raw/news_posts.json",
    "data/raw/reddit_posts.json",
    "data/raw/bluesky_posts.json",
    "data/raw/youtube_posts.json",
    "data/merged/all_merged.json",
]


def get_r2_client():
    return boto3.client(
        "s3",
        endpoint_url=os.getenv("R2_ENDPOINT_URL"),
        aws_access_key_id=os.getenv("R2_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("R2_SECRET_ACCESS_KEY"),
        region_name="auto",
    )


def main():
    print("A DESCARREGAR FICHEIROS DO R2")

    client = get_r2_client()
    bucket = os.getenv("R2_BUCKET_NAME")

    errors = 0
    for file_path in R2_FILES:
        local_path = BASE_DIR / file_path
        local_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            client.download_file(bucket, file_path, str(local_path))
            print(f"Download OK: {file_path}")
        except Exception as e:
            print(f"AVISO: {file_path} não descarregado -> {e}")
            errors += 1

    print("DOWNLOAD CONCLUÍDO" if errors == 0 else f"DOWNLOAD COM {errors} ERRO(S)")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
