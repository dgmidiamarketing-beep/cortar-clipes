#!/usr/bin/env python3
"""
cortar.py - passo 2 da skill cortar-clipes.

Lê a pasta criada pelo analisar.py + o momentos.json (os momentos escolhidos)
e gera os clipes verticais 1080x1920 com legenda queimada, palavra por palavra.

Se a fonte foi um link, baixa SÓ os trechos escolhidos (não o vídeo inteiro).

Uso:
  python cortar.py meus-clipes
  python cortar.py meus-clipes --layout quadrado        # jogo maior na tela
  python cortar.py meus-clipes --so 2,4                 # refaz só os clipes 2 e 4

momentos.json (o Claude escreve):
{
  "credito": "@canal_do_streamer",
  "clipes": [
    {"inicio": "12:03.5", "fim": "12:41", "titulo": "Ele não viu o caminhão",
     "gancho": "NÃO OLHOU PRO LADO", "motivo": "...", "nota": 9,
     "legenda_post": "...", "hashtags": ["#gta", "#clips"]}
  ]
}

Na pasta 'clipes/' sai: 01-titulo.mp4, 01-titulo.txt (título, legenda do post,
hashtags e crédito), painel.jpg (todos os clipes lado a lado).
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
import unicodedata

L, A = 1080, 1920  # largura e altura do vertical

# posições (y do centro) de cada elemento, por layout
LAYOUTS = {
    # vídeo inteiro no meio, fundo desfocado em volta
    "desfocado": {"gancho": 470, "credito": 1300, "legenda": 1430},
    # recorte quadrado do centro (jogo maior), fundo desfocado
    "quadrado": {"gancho": 250, "credito": 372, "legenda": 1330},
    # recorte 9:16 do centro (tela cheia; corta as laterais)
    "centro": {"gancho": 330, "credito": 430, "legenda": 1360},
}


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


def para_segundos(txt):
    if isinstance(txt, (int, float)):
        return float(txt)
    txt = str(txt).strip().replace(",", ".")
    s = 0.0
    for p in txt.split(":"):
        s = s * 60 + float(p)
    return s


def hms(seg):
    seg = max(0.0, seg)
    h, m, s = int(seg // 3600), int((seg % 3600) // 60), int(seg % 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def slug(txt, n=40):
    t = unicodedata.normalize("NFKD", txt).encode("ascii", "ignore").decode()
    t = re.sub(r"[^a-zA-Z0-9]+", "-", t).strip("-").lower()
    return t[:n].strip("-") or "clipe"


def ass_tempo(t):
    t = max(0.0, t)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def ass_texto(t):
    return t.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def grupos_de_palavras(palavras, max_palavras=3, max_chars=20):
    """Junta as palavras em blocos curtos (o que cabe numa linha no celular)."""
    grupos, atual = [], []
    for i, p in enumerate(palavras):
        atual.append(p)
        texto = " ".join(x["w"] for x in atual)
        prox = palavras[i + 1] if i + 1 < len(palavras) else None
        pausa = prox is not None and prox["s"] - p["e"] > 0.6
        pontuacao = p["w"][-1:] in ".!?,;:"
        if len(atual) >= max_palavras or len(texto) >= max_chars or pausa or pontuacao or prox is None:
            grupos.append(atual)
            atual = []
    return grupos


def montar_ass(caminho, clipe, palavras, ini, fim, layout, credito, maiusculas, com_gancho):
    pos = LAYOUTS[layout]
    dur = fim - ini
    linhas = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {L}",
        f"PlayResY: {A}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding",
        "Style: Legenda,Arial,74,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,6,2,5,60,60,0,1",
        "Style: Gancho,Arial,60,&H00FFFFFF,&H00FFFFFF,&H00000000,&HC8000000,-1,0,0,0,100,100,0,0,3,18,0,5,90,90,0,1",
        "Style: Credito,Arial,34,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,0,0,0,0,100,100,0,0,1,3,0,5,60,60,0,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    gancho = (clipe.get("gancho") or clipe.get("titulo") or "").strip()
    if com_gancho and gancho:
        g = ass_texto(gancho.upper() if maiusculas else gancho)
        linhas.append(f"Dialogue: 1,{ass_tempo(0)},{ass_tempo(dur)},Gancho,,0,0,0,,"
                      f"{{\\pos({L // 2},{pos['gancho']})\\fad(150,0)}}{g}")
    if credito:
        linhas.append(f"Dialogue: 1,{ass_tempo(0)},{ass_tempo(dur)},Credito,,0,0,0,,"
                      f"{{\\pos({L // 2},{pos['credito']})}}{ass_texto(credito)}")

    dentro = [dict(p) for p in palavras if p["e"] > ini and p["s"] < fim]
    for p in dentro:
        p["s"] = max(0.0, p["s"] - ini)
        p["e"] = min(dur, p["e"] - ini)
        p["w"] = ass_texto(p["w"].upper() if maiusculas else p["w"])
    grupos = grupos_de_palavras(dentro)
    for g, grupo in enumerate(grupos):
        # o próximo bloco nunca pode aparecer por cima deste
        prox_inicio = grupos[g + 1][0]["s"] if g + 1 < len(grupos) else dur
        for i, p in enumerate(grupo):
            comeco = p["s"]
            if i + 1 < len(grupo):
                final = grupo[i + 1]["s"]
            else:
                final = min(p["e"] + 0.35, prox_inicio, dur)
            if final - comeco < 0.05:
                final = comeco + 0.05
            partes = []
            for j, q in enumerate(grupo):
                if j == i:
                    partes.append("{\\c&H00F0FF&}" + q["w"] + "{\\c&HFFFFFF&}")  # palavra falada em amarelo
                else:
                    partes.append(q["w"])
            linhas.append(f"Dialogue: 0,{ass_tempo(comeco)},{ass_tempo(final)},Legenda,,0,0,0,,"
                          f"{{\\pos({L // 2},{pos['legenda']})}}" + " ".join(partes))
    with open(caminho, "w", encoding="utf-8") as fh:
        fh.write("\n".join(linhas) + "\n")


def filtro_video(layout):
    fundo = (f"scale={L}:{A}:force_original_aspect_ratio=increase,crop={L}:{A},"
             f"gblur=sigma=28,eq=brightness=-0.12:saturation=1.1")
    if layout == "centro":
        return f"[0:v]scale=-2:{A},crop={L}:{A},setsar=1,ass=legenda.ass[v]"
    if layout == "quadrado":
        frente = f"crop='min(iw,ih)':'min(iw,ih)',scale={L}:{L}"
        oy = 420
    else:
        frente = f"scale={L}:-2"
        oy = "(H-h)/2"
    return (f"[0:v]split[a][b];[a]{fundo}[fd];[b]{frente}[fr];"
            f"[fd][fr]overlay=(W-w)/2:{oy},setsar=1,ass=legenda.ass[v]")


def opcoes_ytdlp_base():
    opts = {"quiet": True, "no_warnings": True, "noprogress": True, "noplaylist": True}
    runtimes = {nome: {} for nome in ("deno", "node", "bun") if shutil.which(nome)}
    if runtimes:
        opts["js_runtimes"] = runtimes
    return opts


def baixar_trecho(link, ini, fim, pasta):
    """Baixa só [ini, fim] do link, até 1080p, com corte exato."""
    import yt_dlp
    from yt_dlp.utils import download_range_func

    opts = opcoes_ytdlp_base()
    opts.update({
        "format": "bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b",
        "merge_output_format": "mp4",
        "outtmpl": os.path.join(pasta, "trecho.%(ext)s"),
        "download_ranges": download_range_func(None, [(ini, fim)]),
        "force_keyframes_at_cuts": True,
    })
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([link])
    arqs = [f for f in os.listdir(pasta) if f.startswith("trecho.") and not f.endswith(".part")]
    if not arqs:
        erro("não consegui baixar o trecho do vídeo. Confira se o link abre sem login.")
    return os.path.join(pasta, arqs[0])


def duracao(caminho):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nk=1:nw=1", caminho], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def link_com_tempo(fonte, seg):
    link = fonte.get("link") or ""
    s = int(seg)
    if "youtube.com" in link or "youtu.be" in link:
        return link + ("&" if "?" in link else "?") + f"t={s}s"
    if "twitch.tv" in link:
        return link + ("&" if "?" in link else "?") + f"t={s // 3600}h{(s % 3600) // 60}m{s % 60}s"
    return f"{link} (a partir de {hms(seg)})" if link else hms(seg)


def renderizar(origem, ss, dur, pasta_trab, saida, layout, qualidade):
    fade = max(0.0, dur - 0.25)
    cmd = ["ffmpeg", "-v", "error", "-y"]
    if ss is not None:
        cmd += ["-ss", f"{ss:.3f}"]
    cmd += ["-t", f"{dur:.3f}", "-i", origem,
            "-filter_complex", filtro_video(layout) + ";"
            f"[0:a]loudnorm=I=-14:TP=-1.5:LRA=11,afade=t=in:d=0.15,afade=t=out:st={fade:.2f}:d=0.25,"
            "aresample=48000[a]",
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "medium", "-crf", str(qualidade), "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-ac", "2", "-movflags", "+faststart", saida]
    r = subprocess.run(cmd, cwd=pasta_trab, capture_output=True, text=True)
    if r.returncode != 0:
        erro("o ffmpeg falhou ao montar o clipe:\n" + r.stderr[-1200:])


def painel(clipes_mp4, destino):
    """Um quadro de cada clipe lado a lado (até 4 por linha)."""
    if not clipes_mp4:
        return
    with tempfile.TemporaryDirectory() as tmp:
        for i, c in enumerate(clipes_mp4, 1):
            t = duracao(c) * 0.4
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", c, "-frames:v", "1",
                            "-vf", "scale=360:640", os.path.join(tmp, f"q{i:02d}.png")], capture_output=True)
        n = len(clipes_mp4)
        col = min(n, 4)
        lin = math.ceil(n / col)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-framerate", "1", "-i", os.path.join(tmp, "q%02d.png"),
                        "-vf", f"tile={col}x{lin}:padding=12:margin=12:color=0x111111", "-frames:v", "1",
                        "-q:v", "3", destino], capture_output=True)


def quadros_do_clipe(mp4, destino, n=5):
    """5 quadros do clipe numa linha: pra conferir se a imagem bate com o título."""
    d = duracao(mp4)
    if d <= 0:
        return
    passo = d / n
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{passo / 2:.2f}", "-i", mp4,
                    "-vf", f"fps=1/{passo:.3f},scale=216:384,tile={n}x1:padding=6:color=0x111111",
                    "-frames:v", "1", "-q:v", "4", destino], capture_output=True)


def main():
    ap = argparse.ArgumentParser(description="Corta os momentos escolhidos em clipes verticais com legenda.")
    ap.add_argument("pasta", help="pasta criada pelo analisar.py (com momentos.json)")
    ap.add_argument("--momentos", default=None, help="outro arquivo de momentos (padrão: <pasta>/momentos.json)")
    ap.add_argument("--layout", choices=list(LAYOUTS), default="desfocado",
                    help="desfocado (padrão, vídeo inteiro), quadrado (jogo maior) ou centro (tela cheia)")
    ap.add_argument("--so", default=None, help="refaz só alguns clipes, ex.: 1,3")
    ap.add_argument("--sem-gancho", action="store_true", help="não escreve o gancho no topo")
    ap.add_argument("--sem-credito", action="store_true", help="não escreve o crédito na tela")
    ap.add_argument("--normal", action="store_true", help="legenda em minúsculas (padrão: MAIÚSCULAS)")
    ap.add_argument("--qualidade", type=int, default=20, help="CRF do H.264 (menor = melhor; padrão 20)")
    a = ap.parse_args()

    if not shutil.which("ffmpeg"):
        erro("ffmpeg não encontrado. Instale o ffmpeg e confira com: ffmpeg -version")
    pasta = os.path.abspath(a.pasta)
    try:
        fonte = json.load(open(os.path.join(pasta, "fonte.json"), encoding="utf-8"))
        trans = json.load(open(os.path.join(pasta, "transcricao.json"), encoding="utf-8"))
    except FileNotFoundError:
        erro("essa pasta não tem fonte.json e transcricao.json. Rode o analisar.py antes.")
    arq_mom = a.momentos or os.path.join(pasta, "momentos.json")
    if not os.path.isfile(arq_mom):
        erro(f"não achei {arq_mom}. Escolha os momentos e salve nesse arquivo.")
    momentos = json.load(open(arq_mom, encoding="utf-8"))
    lista = momentos.get("clipes") if isinstance(momentos, dict) else momentos
    if not lista:
        erro("momentos.json está sem clipes.")

    credito = None
    if not a.sem_credito:
        c = (momentos.get("credito") if isinstance(momentos, dict) else None) or fonte.get("canal")
        if c:
            credito = c if c.startswith("@") else f"clipe de: {c}"

    palavras = [p for f in trans["frases"] for p in f["palavras"]]
    so = {int(x) for x in a.so.split(",")} if a.so else None
    saida_dir = os.path.join(pasta, "clipes")
    os.makedirs(saida_dir, exist_ok=True)
    feitos = []
    t0 = time.time()

    for n, clipe in enumerate(lista, 1):
        if so and n not in so:
            continue
        ini, fim = para_segundos(clipe["inicio"]), para_segundos(clipe["fim"])
        dur = fim - ini
        if dur <= 3:
            print(f"  [pulei] clipe {n}: fim antes do início ou curto demais")
            continue
        if dur > 90:
            print(f"  [aviso] clipe {n} tem {dur:.0f} s; Reels/TikTok/Shorts rendem mais até ~60 s")
        if ini < fonte.get("trecho_inicio", 0) - 1 or fim > fonte.get("trecho_fim", 1e9) + 1:
            print(f"  [aviso] clipe {n} sai do trecho analisado: parte dele fica sem legenda")
        titulo = clipe.get("titulo") or f"Clipe {n}"
        nome = f"{n:02d}-{slug(titulo)}"
        print(f"{n}/{len(lista)} {hms(ini)}-{hms(fim)} ({dur:.0f} s) {titulo}", flush=True)
        tc = time.time()
        with tempfile.TemporaryDirectory() as trab:
            montar_ass(os.path.join(trab, "legenda.ass"), clipe, palavras, ini, fim, a.layout, credito,
                       not a.normal, not a.sem_gancho)
            if fonte.get("tipo") == "link":
                origem = baixar_trecho(fonte["link"], ini, fim, trab)
                dur = min(dur, duracao(origem) or dur)
                ss = None
            else:
                origem, ss = fonte["arquivo"], ini
            mp4 = os.path.join(saida_dir, nome + ".mp4")
            renderizar(origem, ss, dur, trab, mp4, a.layout, a.qualidade)
        feitos.append(mp4)
        os.makedirs(os.path.join(saida_dir, "conferir"), exist_ok=True)
        quadros_do_clipe(mp4, os.path.join(saida_dir, "conferir", nome + ".jpg"))

        tags = clipe.get("hashtags") or []
        txt = [f"TÍTULO: {titulo}", ""]
        txt.append("LEGENDA DO POST:")
        txt.append((clipe.get("legenda_post") or titulo).strip())
        if fonte.get("canal"):
            txt.append("")
            txt.append(f"Créditos: {momentos.get('credito') if isinstance(momentos, dict) and momentos.get('credito') else fonte['canal']}"
                       + (f" · {fonte['link']}" if fonte.get("link") else ""))
        if tags:
            txt.append("")
            txt.append(" ".join(tags))
        txt += ["", "---", f"Nota: {clipe.get('nota', '-')}/10 · Motivo: {clipe.get('motivo', '-')}",
                f"Trecho original: {link_com_tempo(fonte, ini)} ({hms(ini)}-{hms(fim)})"]
        with open(os.path.join(saida_dir, nome + ".txt"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(txt) + "\n")
        print(f"   ok em {time.time() - tc:.0f} s -> clipes/{nome}.mp4", flush=True)

    todos = sorted(os.path.join(saida_dir, f) for f in os.listdir(saida_dir) if f.endswith(".mp4"))
    painel(todos, os.path.join(saida_dir, "painel.jpg"))
    print(f"\nPronto: {len(feitos)} clipe(s) em {(time.time() - t0) / 60:.1f} min.")
    print(f"Pasta: {curto(saida_dir)}")
    print("Assista cada um antes de postar. painel.jpg mostra todos lado a lado.")


if __name__ == "__main__":
    main()
