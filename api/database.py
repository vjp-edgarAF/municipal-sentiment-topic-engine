from database.db_connection import connect


def get_connection():
    return connect()

FONTE_MAP = {
    "news":     "googlenews",
    "reddit":   "reddit",
    "bluesky":  "bluesky",
    "youtube":  "youtube",
    "facebook": "facebook",
}
