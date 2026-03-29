"""Ferramenta: remotion_render — cria vídeos 9:16 animados para redes sociais."""

import json
import logging
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

logger = logging.getLogger(__name__)

REMOTION_DIR = Path("/home/ubuntu/remotion-videos")

DEFINITIONS = [{
    "name": "remotion_render",
    "description": (
        "Cria e renderiza um vídeo 9:16 animado para redes sociais (Reels, TikTok, Shorts). "
        "Use quando o usuário pedir para criar, montar ou gerar um vídeo animado. "
        "Você define as cenas criativa e autonomamente com base na ideia do usuário — "
        "escolha cores, estilo, emojis e duração sem precisar perguntar. "
        "Após renderizar, use send_telegram_file para enviar o vídeo ao usuário."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "output_name": {
                "type": "string",
                "description": "Nome do arquivo de saída sem extensão (ex: 'dicas-investimento')",
            },
            "scenes": {
                "type": "array",
                "description": "Lista de cenas do vídeo",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {
                            "type": "string",
                            "enum": ["title", "text", "list", "cta"],
                            "description": (
                                "Tipo da cena: "
                                "title=abertura com título+subtítulo+emoji, "
                                "text=frase de destaque com borda colorida, "
                                "list=lista de itens animados, "
                                "cta=call-to-action final"
                            ),
                        },
                        "title": {"type": "string", "description": "Título principal (title/list/cta)"},
                        "subtitle": {"type": "string", "description": "Subtítulo (title/cta)"},
                        "text": {"type": "string", "description": "Texto corrido (text)"},
                        "items": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Lista de itens (list)",
                        },
                        "emoji": {"type": "string", "description": "Emoji decorativo (title/cta)"},
                        "bgColor": {"type": "string", "description": "Cor de fundo hex (ex: '#0f0f0f')"},
                        "textColor": {"type": "string", "description": "Cor do texto hex (ex: '#ffffff')"},
                        "accentColor": {"type": "string", "description": "Cor de destaque hex (ex: '#facc15')"},
                        "durationInSeconds": {
                            "type": "number",
                            "description": "Duração da cena em segundos (padrão: 3)",
                        },
                    },
                    "required": ["type"],
                },
            },
        },
        "required": ["output_name", "scenes"],
    },
}]


def execute(inp: dict, *, config: dict) -> str:
    work_dir = Path(config["WORK_DIR"])
    output_name = inp.get("output_name", f"video-{int(time.time())}")
    scenes = inp.get("scenes", [])

    if not scenes:
        return "Erro: nenhuma cena fornecida."

    # Sanitiza nome do arquivo
    safe_name = "".join(c if c.isalnum() or c in "-_" else "-" for c in output_name)

    # Calcula duração total
    fps = 30
    total_frames = int(sum(s.get("durationInSeconds", 3) for s in scenes) * fps)

    # Grava JSON de props temporário
    props = {"scenes": scenes}
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", dir=REMOTION_DIR, delete=False, prefix="render-"
    ) as f:
        json.dump(props, f, ensure_ascii=False, indent=2)
        props_path = Path(f.name)

    out_path = REMOTION_DIR / "out" / f"{safe_name}.mp4"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        logger.info(f"[remotion] Renderizando {safe_name} ({total_frames} frames)")
        cmd = (
            f"cd {REMOTION_DIR} && npx remotion render SocialVideo "
            f'"{out_path}" '
            f'--props="{props_path}" '
            f"--concurrency=2 "
            f"--log=error"
        )
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=600
        )

        if result.returncode != 0:
            logger.error(f"[remotion] stderr: {result.stderr[:500]}")
            return f"Erro ao renderizar: {result.stderr[:400]}"

        if not out_path.exists():
            return "Erro: arquivo de saída não foi gerado."

        # Copia para o workspace do bot
        dest = work_dir / f"{safe_name}.mp4"
        shutil.copy2(str(out_path), str(dest))

        # Salva config JSON para reuso/edição futura
        config_dest = work_dir / f"{safe_name}-config.json"
        with open(config_dest, "w", encoding="utf-8") as f:
            json.dump(props, f, ensure_ascii=False, indent=2)

        size_kb = dest.stat().st_size // 1024
        duration_sec = total_frames // fps
        logger.info(f"[remotion] Renderizado: {dest} ({size_kb} KB)")

        return (
            f"✅ Vídeo renderizado: {dest.name} ({size_kb} KB, {duration_sec}s, 1080x1920)\n"
            f"Config salva em '{config_dest.name}' — use para re-renderizar com edições.\n"
            f"Use send_telegram_file com path='{dest.name}' para enviar ao usuário."
        )

    except subprocess.TimeoutExpired:
        return "Erro: timeout ao renderizar (>10 min). Tente reduzir o número de cenas."
    except Exception as e:
        logger.error(f"[remotion] Erro inesperado: {e}")
        return f"Erro inesperado: {e}"
    finally:
        props_path.unlink(missing_ok=True)
