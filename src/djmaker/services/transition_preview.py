"""Точный FFmpeg-preview наложения двух треков по музыкальной сетке."""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

from djmaker.domain.set_timeline import TransitionPlan


class TransitionPreviewError(RuntimeError):
    """Ошибка подготовки аудиопредпрослушивания перехода."""


def _seconds(milliseconds: int | float) -> str:
    return f"{milliseconds / 1000:.6f}".rstrip("0").rstrip(".")


def preview_cache_path(
    output_dir: Path,
    outgoing: Path,
    incoming: Path,
    plan: TransitionPlan,
) -> Path:
    """Строит устойчивое имя cache-файла с учётом изменения оригиналов."""
    digest = hashlib.sha256()
    for source in (outgoing, incoming):
        resolved = source.resolve()
        stat = resolved.stat()
        digest.update(str(resolved).encode("utf-8"))
        digest.update(f"|{stat.st_size}|{stat.st_mtime_ns}".encode())
    digest.update(repr(plan).encode())
    return output_dir / f"transition-{digest.hexdigest()[:20]}.wav"


def build_transition_preview_command(
    ffmpeg: Path,
    outgoing: Path,
    incoming: Path,
    output: Path,
    plan: TransitionPlan,
) -> list[str]:
    """Возвращает команду: B синхронизируется с BPM A и входит по fade."""
    before_ms = min(plan.outgoing_cue_ms, plan.preview_margin_ms)
    outgoing_start_ms = plan.outgoing_cue_ms - before_ms
    outgoing_duration_ms = before_ms + plan.overlap_ms
    incoming_output_ms = plan.overlap_ms + plan.preview_margin_ms
    incoming_source_ms = round(incoming_output_ms * plan.incoming_tempo)
    delay_ms = before_ms
    filter_graph = (
        f"[0:a]atrim=start={_seconds(outgoing_start_ms)}:"
        f"duration={_seconds(outgoing_duration_ms)},asetpts=PTS-STARTPTS,"
        "aresample=48000,aformat=sample_fmts=fltp:"
        "sample_rates=48000:channel_layouts=stereo,"
        f"afade=t=out:st={_seconds(before_ms)}:"
        f"d={_seconds(plan.overlap_ms)}[outgoing];"
        f"[1:a]atrim=start={_seconds(plan.incoming_cue_ms)}:"
        f"duration={_seconds(incoming_source_ms)},asetpts=PTS-STARTPTS,"
        f"atempo={plan.incoming_tempo:.8f},aresample=48000,"
        "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
        f"afade=t=in:st=0:d={_seconds(plan.overlap_ms)},"
        f"adelay={delay_ms}|{delay_ms}[incoming];"
        "[outgoing][incoming]amix=inputs=2:duration=longest:"
        "dropout_transition=0:normalize=0,alimiter=limit=0.95[mix]"
    )
    return [
        str(ffmpeg),
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-i",
        str(outgoing),
        "-i",
        str(incoming),
        "-filter_complex",
        filter_graph,
        "-map",
        "[mix]",
        "-c:a",
        "pcm_s16le",
        str(output),
    ]


def render_transition_preview(
    ffmpeg: Path,
    outgoing: Path,
    incoming: Path,
    output_dir: Path,
    plan: TransitionPlan,
) -> Path:
    """Рендерит cache атомарно; исходные файлы никогда не перезаписываются."""
    for source in (outgoing, incoming):
        if not source.is_file():
            raise TransitionPreviewError(f"Исходный файл не найден: {source}")
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        output = preview_cache_path(output_dir, outgoing, incoming, plan)
    except OSError as exc:
        raise TransitionPreviewError(f"Не удалось прочитать исходный файл: {exc}") from exc
    if output.is_file() and output.stat().st_size > 44:
        return output
    temporary = output.with_suffix(".part.wav")
    command = build_transition_preview_command(
        ffmpeg, outgoing, incoming, temporary, plan
    )
    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
            timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        temporary.unlink(missing_ok=True)
        raise TransitionPreviewError(f"Не удалось запустить FFmpeg: {exc}") from exc
    if completed.returncode != 0:
        temporary.unlink(missing_ok=True)
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise TransitionPreviewError(
            f"FFmpeg не смог собрать переход: {detail or 'неизвестная ошибка'}"
        )
    try:
        os.replace(temporary, output)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise TransitionPreviewError(f"Не удалось сохранить preview: {exc}") from exc
    return output
