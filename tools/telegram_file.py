"""Ferramenta: send_telegram_file — coloca arquivo na fila de envio ao usuário via Telegram."""

from pathlib import Path

DEFINITIONS = [{
    "name": "send_telegram_file",
    "description": (
        "Envia um arquivo do workspace para o usuário via Telegram. "
        "Use sempre que gerar um CSV, planilha, relatório, script ou qualquer arquivo "
        "que o usuário deva receber. O arquivo deve estar dentro do WORK_DIR."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Caminho do arquivo a enviar (relativo ao WORK_DIR ou absoluto dentro dele)",
            },
            "caption": {
                "type": "string",
                "description": "Legenda opcional exibida com o arquivo no Telegram",
            },
        },
        "required": ["path"],
    },
}]


def execute(inp: dict, *, user_id: int, config: dict) -> str:
    from security import resolve_safe_path

    work_dir = config["WORK_DIR"]
    raw_path = inp.get("path", "")
    caption = inp.get("caption", "")

    try:
        safe_path = resolve_safe_path(raw_path, work_dir)
    except Exception as e:
        return f"Erro: caminho inválido — {e}"

    p = Path(safe_path)
    if not p.exists():
        return f"Erro: arquivo não encontrado: {p}"
    if not p.is_file():
        return f"Erro: '{p}' não é um arquivo"

    size = p.stat().st_size
    if size > 50 * 1024 * 1024:
        return f"Erro: arquivo muito grande ({size // 1024 // 1024} MB). Limite Telegram: 50 MB"

    pending: dict | None = config.get("pending_files")
    if pending is None:
        return "Erro: sistema de arquivos pendentes não disponível"

    pending.setdefault(user_id, []).append({"path": str(p), "caption": caption})
    return f"✅ Arquivo '{p.name}' ({size // 1024} KB) adicionado à fila — será enviado ao usuário após esta resposta."
