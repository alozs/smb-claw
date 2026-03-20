"""Ferramenta schedule: agendamentos de notificações proativas."""

import uuid
from datetime import datetime

DEFINITIONS = [{
    "name": "schedule",
    "description": (
        "Gerencia agendamentos de notificações proativas. "
        "O bot pode enviar mensagens automaticamente em horários definidos. "
        "Exemplos: 'todo dia às 9h, liste meus PRs', 'me lembre às 15h de fazer deploy'. "
        "Sempre forneça name (nome curto legível) e description (o que o agendamento faz)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action":      {"type": "string", "enum": ["list", "add", "remove"]},
            "hour":        {"type": "integer", "description": "Hora (0-23)"},
            "minute":      {"type": "integer", "description": "Minuto (0-59), default 0"},
            "weekdays":      {"type": "string", "description": "'all' ou 'mon,tue,wed,thu,fri,sat,sun'"},
            "day_of_month":  {"type": "integer", "description": "Dia do mês (1-31). 0 = todo dia (padrão)"},
            "message":       {"type": "string", "description": "Prompt que será processado no horário"},
            "name":          {"type": "string", "description": "Nome curto do agendamento (ex: 'Briefing IA')"},
            "description":   {"type": "string", "description": "Descrição do que o agendamento faz"},
            "schedule_id": {"type": "string", "description": "ID do agendamento (para remove)"},
        },
        "required": ["action"],
    },
}]


def execute(inp: dict, *, user_id: int, db) -> str:
    action = inp["action"]

    if action == "list":
        schedules = db.schedule_list()
        if not schedules:
            return "(nenhum agendamento)"
        lines = []
        for s in schedules:
            name_str = f" [{s['name']}]" if s.get("name") else ""
            desc_str = f" — {s['description']}" if s.get("description") else ""
            lines.append(
                f"[{s['id']}]{name_str} {s['hour']:02d}:{s['minute']:02d} "
                f"({s['weekdays']}) → {s['message'][:60]}{desc_str}"
            )
        return "\n".join(lines)

    if action == "add":
        hour = inp.get("hour", 0)
        minute = inp.get("minute", 0)
        weekdays = inp.get("weekdays", "all")
        day_of_month = inp.get("day_of_month", 0)
        message = inp.get("message", "")
        name = inp.get("name", "")
        description = inp.get("description", "")
        if not message:
            return "Erro: message obrigatório"
        sid = str(uuid.uuid4())[:8]
        db.schedule_add(sid, user_id, hour, minute, weekdays, message, day_of_month,
                        name=name, description=description)
        dom_str = f" dia {day_of_month}" if day_of_month else ""
        name_str = f" ({name})" if name else ""
        return f"✅ Agendamento `{sid}`{name_str} criado: {hour:02d}:{minute:02d} ({weekdays}{dom_str})"

    if action == "remove":
        sid = inp.get("schedule_id", "")
        if not sid:
            return "Erro: schedule_id obrigatório"
        db.schedule_remove(sid)
        return f"✅ Agendamento `{sid}` removido"

    return f"Ação schedule desconhecida: {action}"
