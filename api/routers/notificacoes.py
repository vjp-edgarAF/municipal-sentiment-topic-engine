from fastapi import APIRouter
from pydantic import BaseModel
from api.database import get_connection

router = APIRouter(prefix="/notificacoes", tags=["Notificações"])


class CanaisUpdate(BaseModel):
    email: bool
    sms: bool
    painel: bool
    teams: bool


@router.get("/canais")
def get_canais():
    """Devolve as preferências de canais de notificação."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT Email, SMS, Painel, Teams
        FROM NotificacaoCanais
        WHERE Utilizador = 'global'
    """)
    row = cursor.fetchone()
    conn.close()

    return {
        "email": bool(row[0]),
        "sms": bool(row[1]),
        "painel": bool(row[2]),
        "teams": bool(row[3]),
    }


@router.put("/canais")
def atualizar_canais(body: CanaisUpdate):
    """Atualiza as preferências de canais de notificação."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE NotificacaoCanais
        SET Email = ?, SMS = ?, Painel = ?, Teams = ?, AtualizadaEm = NOW()
        WHERE Utilizador = 'global'
    """, int(body.email), int(body.sms), int(body.painel), int(body.teams))
    conn.commit()
    conn.close()

    return {
        "message": "Preferências atualizadas com sucesso.",
        "email": body.email,
        "sms": body.sms,
        "painel": body.painel,
        "teams": body.teams,
    }