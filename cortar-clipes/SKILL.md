---
name: cortar-clipes
description: Corta os melhores momentos de um vídeo longo (live gravada, VOD da Twitch/Kick, gameplay, podcast) em clipes verticais 9:16 com legenda palavra por palavra, título, legenda do post, hashtags e crédito. O Claude escolhe os momentos; scripts locais (faster-whisper + ffmpeg + yt-dlp) baixam, transcrevem e cortam. Use quando pedirem pra clipar, cortar uma live, fazer cortes, tirar clipes de um vídeo ou link, ou transformar vídeo longo em Reels, TikTok ou Shorts.
---

# Cortar clipes

Objetivo: de um link (ou arquivo) de vídeo longo, entregar de 3 a 8 clipes verticais prontos pra revisar e postar, cada um com um `.txt` de título, legenda, hashtags e crédito. Você (Claude) é quem escolhe os momentos. Os scripts só baixam, transcrevem, medem o volume e cortam.

Os scripts ficam na pasta `scripts/` ao lado deste arquivo. Abaixo, `SKILL_DIR` é a pasta onde este `SKILL.md` está; use o caminho completo nos comandos.

## 1. Antes de rodar
1. **Entrada.** Peça o link da gravação (YouTube, Twitch VOD, Kick ou outro que o yt-dlp abra) ou o caminho do arquivo. Live **ao vivo** não dá: tem que ser a gravação, depois que acabar.
2. **Direito de uso, em uma pergunta só, sem sermão:** "Esse vídeo é seu, ou o canal libera clipe (programa ou campanha de clipes)?". Se a pessoa disser que não sabe, avise em uma linha: canal que não libera costuma derrubar o clipe e dar strike; o seguro é a própria gameplay ou streamer que libera ou paga clipe. Depois disso, siga. O crédito (@ do canal) vai sempre no vídeo e na legenda do post.
3. **Requisitos.** Rode `ffmpeg -version` e `python -c "import faster_whisper, yt_dlp"`. Se faltar algo, mostre o comando e **pergunte antes de instalar**:
   - Python: `pip install faster-whisper yt-dlp`
   - ffmpeg: Windows `winget install Gyan.FFmpeg` · Mac `brew install ffmpeg` · Linux `sudo apt install ffmpeg`
4. **Tamanho.** Se o vídeo tiver mais de 1 h, ofereça analisar só um pedaço com `--trecho` (ex.: a parte mais animada que a pessoa lembra) ou avise o tempo: num notebook comum, sem placa de vídeo, a análise leva perto de 1/3 da duração no modelo padrão, e cada clipe leva perto de 1 min pra montar.

## 2. Analisar
```bash
python "SKILL_DIR/scripts/analisar.py" "LINK_OU_ARQUIVO" --saida NOME-DA-PASTA [--idioma pt] [--trecho 0:30:00-1:30:00] [--modelo base]
```
- Baixa **só o áudio** (leve), transcreve no computador da pessoa e mede o volume segundo a segundo.
- `--idioma pt` (ou en, es) deixa mais rápido e evita erro de detecção quando o vídeo começa com música.
- `--modelo base` é mais rápido e erra mais; `small` é o padrão. `medium` só se a legenda vier ruim.
- Rode em segundo plano se o vídeo for longo e avise a pessoa de quanto tempo deve levar.

Ao terminar, a pasta tem `resumo.md`, `transcricao.json`, `energia.json` e `fonte.json`.

## 3. Escolher os momentos (o seu trabalho)
Leia `NOME-DA-PASTA/resumo.md` inteiro: as pistas automáticas, os picos de volume, a transcrição com volume e a "ação sem fala". As pistas são só pista: confira cada uma na transcrição.

**O que prende**
- Gameplay e live de jogo: reação forte (grito, palavrão, "não acredito"), perseguição ou fuga, batida, explosão, morte boba, falha engraçada, jogada difícil que dá certo, virada no fim, conversa engraçada com o chat ou com outro jogador.
- Podcast, conversa ou aula: frase forte ou polêmica, história com começo, meio e fim, conselho prático que se entende sem contexto, número que surpreende, risada.
- Sem fala: "ação sem fala" com volume alto (explosão, música de vitória). Use se a transcrição em volta mostrar que é um momento (reação antes ou depois).

**Regras do corte**
1. De 3 a 8 clipes, de 20 a 60 s (ideal 25 a 45 s). Menos clipes bons > muitos fracos. Se não houver 3 momentos bons, entregue os que houver e diga.
2. Começa no gancho: no máximo 2 s antes da primeira frase que dá vontade de ver o resto. Nunca no meio de palavra, nunca com "então", "é...", "bom". Use o início de uma linha da transcrição.
3. Termina depois do fecho: a reação, a risada ou a conclusão, mais 1 a 2 s. Nunca no meio de frase.
4. Cada clipe se entende sozinho, sem ter visto o resto. Clipes não se sobrepõem.
5. Não invente o que não está na transcrição. Se o momento depende da imagem e a fala não deixa claro, diga isso no motivo e dê nota menor.

**Pra cada clipe escreva**
- `titulo`: até 8 palavras, diz o que acontece, sem prometer o que o clipe não mostra.
- `gancho`: até 6 palavras, vai escrito no topo do vídeo o tempo todo (ex.: "ELE NÃO VIU O CAMINHÃO").
- `nota` de 0 a 10 e `motivo` em uma linha (por que prende).
- `legenda_post`: 1 ou 2 linhas no idioma do público, sem crédito (o script põe).
- `hashtags`: 3 a 5 (jogo ou tema, #clips ou #cortes, nicho). Sem hashtag enganosa (não use #gta6 em clipe que não é GTA 6).

Salve em `NOME-DA-PASTA/momentos.json`, com os tempos do vídeo original (os mesmos da transcrição):
```json
{
  "credito": "@canal_do_criador",
  "clipes": [
    {"inicio": "12:03.5", "fim": "12:41", "titulo": "Ele não viu o caminhão", "gancho": "NÃO OLHOU PRO LADO",
     "nota": 9, "motivo": "batida do nada + reação engraçada", "legenda_post": "Ninguém tava preparado pra isso",
     "hashtags": ["#gta", "#clips", "#gameplay"]}
  ]
}
```
`credito`: o @ do canal se a pessoa souber; se não, deixe sem e o script usa o nome do canal do `fonte.json`.

Mostre à pessoa uma tabela curta (nº, trecho, título, nota, motivo) e já siga pro corte, a não ser que ela tenha pedido pra aprovar antes.

## 4. Cortar
```bash
python "SKILL_DIR/scripts/cortar.py" NOME-DA-PASTA [--layout desfocado|quadrado|centro] [--so 2,4]
```
- Link: baixa **só os trechos escolhidos**, até 1080p. Arquivo: corta direto.
- `desfocado` (padrão): vídeo inteiro no meio, fundo desfocado, gancho em cima, legenda embaixo. Bom pra tudo.
- `quadrado`: recorte quadrado do centro, jogo maior. Bom pra gameplay sem câmera no canto.
- `centro`: tela cheia 9:16, corta as laterais. Bom quando a ação está no meio (ou a pessoa está no meio da tela).
- `--so 2,4` refaz só esses clipes depois de ajustar o `momentos.json`.
- `--normal` deixa a legenda em minúsculas; `--sem-gancho` e `--sem-credito` tiram esses textos.

## 5. Conferir e entregar
1. Abra cada `NOME-DA-PASTA/clipes/conferir/NN-....jpg` (5 quadros do clipe, do começo ao fim). Você escolheu pela fala sem ver a imagem; agora confira:
   - a imagem bate com o título e o gancho? Clipe que passa a maior parte em menu, lobby, tela preta ou carregando: ajuste `inicio`/`fim` no `momentos.json` e refaça com `--so N`, ou tire o clipe.
   - legenda legível, gancho inteiro, nada importante escondido atrás do texto.
2. Abra `NOME-DA-PASTA/clipes/painel.jpg` (um quadro de cada clipe lado a lado) pra visão geral.
3. Entregue: a pasta `clipes/`, a tabela final dos clipes (com o que você ajustou na conferência) e o lembrete: assistir cada um antes de postar, e postar com o crédito do `.txt`.

Nada é postado nem enviado por esta skill. A internet só é usada pra baixar o link que a pessoa passou e, no primeiro uso, o modelo de transcrição (uma vez só).
