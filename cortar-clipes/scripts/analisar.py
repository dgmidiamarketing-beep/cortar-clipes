#!/usr/bin/env python3
"""
analisar.py - passo 1 da skill cortar-clipes.

Recebe um link (YouTube, Twitch VOD, Kick...) ou um arquivo de vídeo,
baixa SÓ o áudio, transcreve no seu computador (faster-whisper, CPU) e mede
os momentos fortes (volume, reação, fala rápida). Escreve na pasta de saída:

  fonte.json       de onde veio o vídeo (título, canal, link, licença)
  transcricao.json frases e palavras com tempo (usado no corte e na legenda)
  energia.json     volume por segundo
  resumo.md        o que o Claude lê pra escolher os momentos

Uso:
  python analisar.py "https://www.youtube.com/watch?v=..." --saida meus-clipes
  python analisar.py gravacao.mp4 --idioma pt
  python analisar.py "https://www.twitch.tv/videos/..." --trecho 1:00:00-1:30:00

Nada é enviado pra fora: a internet só é usada pra baixar o link que você
passou (pelo yt-dlp) e, no 1º uso, o modelo de transcrição (uma vez só).
"""
import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

TAXA = 16000  # Hz, o que o Whisper usa

PALAVRAS_REACAO = [
    # português
    "meu deus", "caramba", "caraca", "nossa", "não acredito", "nao acredito", "que isso",
    "socorro", "corre", "morri", "morreu", "mataram", "acabou", "olha isso", "olha só",
    "mano", "véi", "vei", "pqp", "puta", "porra", "cacete", "misericórdia", "jesus",
    "kkk", "hahaha", "rsrs", "boa", "isso aí", "vamo", "bora", "uau", "eita", "vixe",
    "foge", "cuidado", "explodiu", "bateu", "capotou", "perdi", "ganhei", "ganhamos",
    # inglês
    "oh my god", "omg", "no way", "what the", "let's go", "lets go", "holy", "wow",
    "dude", "bro", "run", "help", "dead", "i died", "insane", "crazy", "haha",
    # espanhol
    "dios mío", "dios mio", "madre mía", "no puede ser", "vamos", "corre",
]


def saida_utf8():
    """No Windows o terminal do Claude Code costuma ser cp1252: sem isto, título com
    emoji ou letra de outro alfabeto derruba o script no print."""
    for fluxo in (sys.stdout, sys.stderr):
        try:
            fluxo.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def curto(caminho):
    """Caminho relativo à pasta atual (não mostra a pasta do usuário no terminal)."""
    try:
        r = os.path.relpath(caminho)
        return caminho if r.startswith("..") else r
    except ValueError:
        return caminho


def erro(msg):
    print(f"\n[ERRO] {msg}\n", file=sys.stderr)
    sys.exit(1)


def conferir_requisitos(precisa_link):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        erro("ffmpeg não encontrado. Instale o ffmpeg e confira com: ffmpeg -version")
    try:
        import faster_whisper  # noqa: F401
        import numpy  # noqa: F401
    except ImportError:
        erro("faltou o faster-whisper. Rode: pip install faster-whisper")
    if precisa_link:
        try:
            import yt_dlp  # noqa: F401
        except ImportError:
            erro("pra usar link, instale o yt-dlp: pip install yt-dlp  (ou passe um arquivo de vídeo)")


def para_segundos(txt):
    """'90', '1:30', '01:02:03', '75.5' -> segundos"""
    txt = str(txt).strip().replace(",", ".")
    if not txt:
        return None
    partes = txt.split(":")
    s = 0.0
    for p in partes:
        s = s * 60 + float(p)
    return s


def hms(seg, decimos=False):
    seg = max(0.0, seg)
    h = int(seg // 3600)
    m = int((seg % 3600) // 60)
    s = seg % 60
    base = f"{h}:{m:02d}:" if h else f"{m:02d}:"
    return base + (f"{s:04.1f}" if decimos else f"{int(s):02d}")


def eh_link(entrada):
    return bool(re.match(r"^https?://", entrada.strip(), re.I))


def opcoes_ytdlp_base():
    """Opções comuns do yt-dlp. Usa node ou deno se existir (o YouTube às vezes exige)."""
    opts = {"quiet": True, "no_warnings": True, "noprogress": True, "noplaylist": True}
    runtimes = {}
    for nome in ("deno", "node", "bun"):
        if shutil.which(nome):
            runtimes[nome] = {}
    if runtimes:
        opts["js_runtimes"] = runtimes
    return opts


def baixar_audio(link, pasta):
    """Baixa só a faixa de áudio (leve, e o downloader normal do yt-dlp é rápido).
    Mesmo com --trecho baixa o áudio inteiro: pedir só um pedaço passa pelo ffmpeg
    e costuma ser bem mais lento. O corte do trecho é feito depois, no seu PC."""
    import yt_dlp

    opts = opcoes_ytdlp_base()
    opts.update({
        "format": "bestaudio[abr<=96]/bestaudio/best",
        "outtmpl": os.path.join(pasta, "audio-original.%(ext)s"),
        "concurrent_fragment_downloads": 4,
    })
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(link, download=False)
        if info.get("is_live"):
            erro("esse link é uma live AO VIVO. Espere acabar e use o link da gravação (VOD).")
        ydl.process_ie_result(info, download=True)
    duracao = info.get("duration") or 0
    arquivos = [f for f in os.listdir(pasta) if f.startswith("audio-original.")]
    if not arquivos:
        erro("o download do áudio falhou. Confira se o link abre no navegador sem login.")
    fonte = {
        "tipo": "link",
        "link": info.get("webpage_url") or link,
        "titulo": info.get("title"),
        "canal": info.get("channel") or info.get("uploader"),
        "canal_link": info.get("channel_url") or info.get("uploader_url"),
        "plataforma": info.get("extractor_key"),
        "licenca": info.get("license"),
        "duracao": duracao,
    }
    return os.path.join(pasta, arquivos[0]), fonte


def duracao_arquivo(caminho):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nk=1:nw=1", caminho],
        capture_output=True, text=True,
    )
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def para_wav(origem, wav, inicio=None, fim=None):
    cmd = ["ffmpeg", "-v", "error", "-y"]
    if inicio:
        cmd += ["-ss", f"{inicio:.3f}"]
    if fim:
        cmd += ["-to", f"{fim:.3f}"]
    cmd += ["-i", origem, "-vn", "-ac", "1", "-ar", str(TAXA), "-c:a", "pcm_s16le", wav]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(wav):
        erro("o ffmpeg não conseguiu ler o áudio desse arquivo.\n" + r.stderr[-800:])


def ler_wav(wav):
    import numpy as np
    import wave

    with wave.open(wav, "rb") as w:
        dados = w.readframes(w.getnframes())
    return np.frombuffer(dados, dtype=np.int16).astype(np.float32) / 32768.0


def energia_por_segundo(audio):
    """Volume (dB RMS) por segundo e 'sobressalto' (quanto subiu de repente)."""
    import numpy as np

    n = len(audio) // TAXA
    if n == 0:
        return []
    blocos = audio[: n * TAXA].reshape(n, TAXA)
    rms = np.sqrt(np.mean(blocos ** 2, axis=1) + 1e-12)
    db = 20 * np.log10(rms + 1e-9)
    mediana = float(np.median(db))
    iqr = float(np.percentile(db, 75) - np.percentile(db, 25)) or 1.0
    saida = []
    for i in range(n):
        antes = db[max(0, i - 4): i]
        subida = float(db[i] - (antes.mean() if len(antes) else db[i]))
        saida.append({
            "t": i,
            "db": round(float(db[i]), 1),
            "z": round((float(db[i]) - mediana) / iqr, 2),
            "subida": round(subida, 1),
        })
    return saida


def transcrever(audio, modelo, idioma, deslocamento):
    from faster_whisper import WhisperModel

    threads = max(1, (os.cpu_count() or 4) - 1)
    print(f"  carregando o modelo '{modelo}' (a 1ª vez baixa o modelo, uma vez só)...", flush=True)
    m = WhisperModel(modelo, device="cpu", compute_type="int8", cpu_threads=threads)
    # passa o áudio já lido (numpy): não depende do decodificador interno do faster-whisper
    segs, info = m.transcribe(
        audio,
        language=idioma,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
        beam_size=1,
        condition_on_previous_text=False,
    )
    frases = []
    ultimo_print = time.time()
    for s in segs:
        palavras = [
            {"w": p.word.strip(), "s": round(p.start + deslocamento, 2), "e": round(p.end + deslocamento, 2)}
            for p in (s.words or []) if p.word.strip()
        ]
        frases.append({
            "s": round(s.start + deslocamento, 2),
            "e": round(s.end + deslocamento, 2),
            "texto": s.text.strip(),
            "palavras": palavras,
        })
        if time.time() - ultimo_print > 15:
            print(f"  ...transcrito até {hms(s.end + deslocamento)}", flush=True)
            ultimo_print = time.time()
    return frases, info.language


def contar_reacoes(texto):
    t = texto.lower()
    n = sum(t.count(p) for p in PALAVRAS_REACAO)
    n += texto.count("!") + len(re.findall(r"\b(?:ha|ah|kk)+\b", t))
    return n


def candidatos(energia, frases, deslocamento, total, janela=40, passo=5, quantos=12):
    """Janelas de ~40 s com mais pico de volume, reação e fala. É só uma pista pro Claude."""
    if not energia:
        return []
    z = {e["t"] + int(deslocamento): e for e in energia}
    reacao = {}
    fala = {}
    for f in frases:
        seg = int(f["s"])
        reacao[seg] = reacao.get(seg, 0) + contar_reacoes(f["texto"])
        for p in f["palavras"]:
            fala[int(p["s"])] = fala.get(int(p["s"]), 0) + 1
    ini0 = int(deslocamento)
    fim0 = int(deslocamento + total)
    janelas = []
    for a in range(ini0, max(ini0 + 1, fim0 - janela + 1), passo):
        b = a + janela
        zs = sorted((z[t]["z"] for t in range(a, b) if t in z), reverse=True)
        if not zs:
            continue
        pico = sum(zs[:5]) / min(5, len(zs))
        sub = max((z[t]["subida"] for t in range(a, b) if t in z), default=0)
        rea = sum(reacao.get(t, 0) for t in range(a, b))
        pal = sum(fala.get(t, 0) for t in range(a, b)) / janela
        janelas.append([a, b, pico, sub, rea, pal])
    if not janelas:
        return []

    def norm(i):
        vals = [j[i] for j in janelas]
        lo, hi = min(vals), max(vals)
        return [(v - lo) / (hi - lo) if hi > lo else 0 for v in vals]

    npico, nsub, nrea, npal = norm(2), norm(3), norm(4), norm(5)
    for k, j in enumerate(janelas):
        j.append(0.40 * npico[k] + 0.20 * nsub[k] + 0.25 * nrea[k] + 0.15 * npal[k])
    janelas.sort(key=lambda j: j[-1], reverse=True)
    escolhidas = []
    for j in janelas:
        if all(j[1] <= e[0] or j[0] >= e[1] for e in escolhidas):
            escolhidas.append(j)
        if len(escolhidas) >= quantos:
            break
    escolhidas.sort(key=lambda j: j[0])
    return [
        {"inicio": j[0], "fim": j[1], "nota_auto": round(j[-1] * 10, 1), "pico_volume": round(j[2], 1),
         "subida_db": round(j[3], 1), "reacoes": j[4], "palavras_por_s": round(j[5], 1)}
        for j in escolhidas
    ]


def limites_volume(energia):
    """Cortes de volume pelo próprio vídeo: top 25%, 15%, 8%, 4% e 1%."""
    dbs = sorted(e["db"] for e in energia) or [0]
    return [dbs[min(len(dbs) - 1, int(len(dbs) * q))] for q in (0.75, 0.85, 0.92, 0.96, 0.99)]


def barra(db, limites):
    n = sum(1 for x in limites if db >= x)
    return "#" * n if n else "."


def escrever_resumo(pasta, fonte, frases, energia, cands, deslocamento, idioma, tempos):
    picos = sorted(energia, key=lambda e: e["db"], reverse=True)[: max(5, len(energia) // 50)]
    picos_t = sorted({int(p["t"] + deslocamento) for p in picos})
    dbmap = {e["t"] + int(deslocamento): e["db"] for e in energia}
    limites = limites_volume(energia)

    L = []
    L.append("# Resumo pra escolher os momentos\n")
    L.append(f"- **Vídeo:** {fonte.get('titulo') or os.path.basename(fonte.get('arquivo', ''))}")
    if fonte.get("canal"):
        L.append(f"- **Canal (crédito):** {fonte['canal']}" + (f" · {fonte['canal_link']}" if fonte.get("canal_link") else ""))
    if fonte.get("link"):
        L.append(f"- **Link:** {fonte['link']}")
    if fonte.get("licenca"):
        L.append(f"- **Licença informada pela plataforma:** {fonte['licenca']}")
    L.append(f"- **Trecho analisado:** {hms(deslocamento)} até {hms(deslocamento + tempos['duracao_analisada'])} "
             f"({tempos['duracao_analisada'] / 60:.1f} min) · idioma detectado: {idioma}")
    L.append(f"- **Tempo da análise:** download {tempos['download']:.0f} s · transcrição {tempos['transcricao']:.0f} s")
    L.append("")
    L.append("## Pistas automáticas (janelas de ~40 s com mais volume, reação e fala)")
    L.append("Pista, não decisão: confira na transcrição e ajuste início e fim pra começar no gancho e terminar no fecho.\n")
    L.append("| # | Trecho | Nota auto | Pico de volume | Subida (dB) | Reações | Palavras/s |")
    L.append("|---|---|---|---|---|---|---|")
    for i, c in enumerate(cands, 1):
        L.append(f"| {i} | {hms(c['inicio'])}-{hms(c['fim'])} | {c['nota_auto']} | {c['pico_volume']} | "
                 f"{c['subida_db']} | {c['reacoes']} | {c['palavras_por_s']} |")
    L.append("")
    L.append("## Picos de volume (segundos mais altos do vídeo)")
    L.append(", ".join(hms(t) for t in picos_t[:60]) or "nenhum")
    L.append("")
    L.append("## Transcrição com volume")
    L.append("Formato: `[início] volume | fala`. Volume comparado ao resto do vídeo: `.` normal, "
             "`#` top 25%, `##` top 15%, `###` top 8%, `####` top 4%, `#####` top 1%.\n")
    for f in frases:
        dbs = [dbmap[t] for t in range(int(f["s"]), int(f["e"]) + 1) if t in dbmap]
        L.append(f"[{hms(f['s'])}] {barra(max(dbs), limites) if dbs else '.':5} | {f['texto']}")
    # volume alto sem ninguém falando = ação (explosão, tiroteio, música de vitória)
    L.append("")
    L.append("## Ação sem fala (volume no top 15% e ninguém falando por 5 s ou mais)")
    falando = set()
    for f in frases:
        falando.update(range(int(f["s"]), int(f["e"]) + 1))
    blocos, atual, folga = [], None, 0
    for e in energia:
        t = e["t"] + int(deslocamento)
        if t not in falando and e["db"] >= limites[1]:
            atual = [t, t] if atual is None else [atual[0], t]
            folga = 0
        elif atual is not None and t not in falando and folga < 2:
            folga += 1  # aceita até 2 s mais baixos no meio da ação
        else:
            if atual and atual[1] - atual[0] >= 5:
                blocos.append(atual)
            atual, folga = None, 0
    if atual and atual[1] - atual[0] >= 5:
        blocos.append(atual)
    L.append(", ".join(f"{hms(a)}-{hms(b)}" for a, b in blocos[:40]) or "nenhum")
    L.append("")
    with open(os.path.join(pasta, "resumo.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))


def main():
    saida_utf8()
    ap = argparse.ArgumentParser(description="Analisa um vídeo longo pra achar os melhores momentos.")
    ap.add_argument("entrada", help="link (YouTube, Twitch VOD, Kick...) ou caminho do arquivo de vídeo")
    ap.add_argument("--saida", default=None, help="pasta de saída (padrão: clipes-<nome>)")
    ap.add_argument("--modelo", default="small",
                    help="modelo do Whisper: tiny, base, small (padrão), medium. Maior = melhor e mais lento")
    ap.add_argument("--idioma", default=None, help="pt, en, es... (padrão: detecta sozinho)")
    ap.add_argument("--trecho", default=None,
                    help="analisa só um pedaço, ex.: 0:00-30:00 ou 1:10:00-1:40:00 (bom pra live longa)")
    a = ap.parse_args()

    link = eh_link(a.entrada)
    conferir_requisitos(link)

    inicio = fim = None
    if a.trecho:
        if "-" not in a.trecho:
            erro("use --trecho INICIO-FIM, ex.: 10:00-40:00")
        p1, p2 = a.trecho.split("-", 1)
        inicio, fim = para_segundos(p1), para_segundos(p2)
        if fim is not None and inicio is not None and fim <= inicio:
            erro("no --trecho o fim tem que ser depois do início")

    if a.saida:
        pasta = a.saida
    else:
        base = re.sub(r"[^\w-]+", "-", os.path.splitext(os.path.basename(a.entrada.rstrip("/")))[0])[:40].strip("-")
        pasta = f"clipes-{base or 'video'}"
    os.makedirs(pasta, exist_ok=True)
    pasta = os.path.abspath(pasta)

    t0 = time.time()
    print("1/3 Pegando o áudio...", flush=True)
    with tempfile.TemporaryDirectory() as tmp:
        if link:
            original, fonte = baixar_audio(a.entrada, tmp)
            wav = os.path.join(pasta, "audio.wav")
            para_wav(original, wav, inicio, fim)
        else:
            if not os.path.isfile(a.entrada):
                erro(f"arquivo não encontrado: {a.entrada}")
            fonte = {
                "tipo": "arquivo",
                "arquivo": os.path.abspath(a.entrada),
                "titulo": os.path.splitext(os.path.basename(a.entrada))[0],
                "duracao": duracao_arquivo(a.entrada),
            }
            wav = os.path.join(pasta, "audio.wav")
            para_wav(a.entrada, wav, inicio, fim)
    t_download = time.time() - t0

    deslocamento = inicio or 0.0
    audio = ler_wav(wav)
    dur = len(audio) / TAXA
    if dur < 20:
        erro("o áudio tem menos de 20 segundos. Confira o link ou o --trecho.")

    print(f"2/3 Transcrevendo {dur / 60:.1f} min no seu computador (modelo {a.modelo})...", flush=True)
    t1 = time.time()
    frases, idioma = transcrever(audio, a.modelo, a.idioma, deslocamento)
    t_trans = time.time() - t1

    print("3/3 Medindo volume e achando os picos...", flush=True)
    energia = energia_por_segundo(audio)
    cands = candidatos(energia, frases, deslocamento, dur)

    fonte.update({"trecho_inicio": deslocamento, "trecho_fim": deslocamento + dur, "idioma": idioma,
                  "modelo": a.modelo})
    with open(os.path.join(pasta, "fonte.json"), "w", encoding="utf-8") as fh:
        json.dump(fonte, fh, ensure_ascii=False, indent=2)
    with open(os.path.join(pasta, "transcricao.json"), "w", encoding="utf-8") as fh:
        json.dump({"idioma": idioma, "frases": frases}, fh, ensure_ascii=False)
    with open(os.path.join(pasta, "energia.json"), "w", encoding="utf-8") as fh:
        json.dump({"deslocamento": deslocamento, "por_segundo": energia}, fh)
    escrever_resumo(pasta, fonte, frases, energia, cands, deslocamento, idioma,
                    {"download": t_download, "transcricao": t_trans, "duracao_analisada": dur})
    os.remove(wav)

    total = time.time() - t0
    print(f"\nPronto em {total / 60:.1f} min ({dur / 60:.1f} min de vídeo, {len(frases)} frases).")
    print(f"Leia: {curto(os.path.join(pasta, 'resumo.md'))}")
    print("Próximo passo: escolher os momentos e salvar momentos.json na mesma pasta.")


if __name__ == "__main__":
    main()
