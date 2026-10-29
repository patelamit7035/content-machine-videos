#!/usr/bin/env python3
"""make_reel.py: turn a reel spec (script lines + voice WAVs + scene list) into a finished 9:16 MP4.

Usage:
  python3 tools/make_reel.py spec.json out.mp4

What it does:
  1. Joins the voice WAVs (one per line), optional speed-up to fit max_seconds.
  2. Word timings: proportional inside each line (or from "segments" if given).
  3. Renders 1080x1920 frames from tools/reel_template.html with Playwright (Chromium):
     moving navy/green background, icon scenes (hook, phone, approval, checklist, flow,
     compare, stat, big, cta), word-by-word captions above the bottom 20%, progress bar.
  4. Audio: voice + generated music bed (ducked under the voice) + whoosh on each scene,
     loudness normalised to -14 LUFS, true peak -1.5 dB.
  5. QA: overflow check per scene, 5 frames in <out>_qa.jpg, prints loudness. Exit 2 if text overflows.

Spec format: see tools/REEL_SPEC.md.
"""
import json, math, os, re, subprocess, sys, tempfile, wave
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SR = 48000
FPS = 30


def sh(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        sys.stderr.write(r.stderr[-3000:])
        raise SystemExit(f"command failed: {' '.join(cmd[:4])} ...")
    return r


def read_wav(path):
    tmp = tempfile.mktemp(suffix=".wav")
    sh(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", path, "-ac", "1", "-ar", str(SR), "-c:a", "pcm_s16le", tmp])
    w = wave.open(tmp)
    a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    os.remove(tmp)
    return a


def write_wav(path, a):
    a = np.clip(a, -1, 1)
    w = wave.open(path, "wb")
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
    w.writeframes((a * 32767).astype(np.int16).tobytes())
    w.close()


def trim(a, thr=0.01):
    idx = np.where(np.abs(a) > thr)[0]
    if len(idx) == 0:
        return a
    s = max(0, idx[0] - int(0.03 * SR)); e = min(len(a), idx[-1] + int(0.08 * SR))
    return a[s:e]


def word_weight(w):
    return len(re.sub(r"[^\w]", "", w)) + 2


def assign_words(text, s, e, words, line_idx):
    toks = text.split()
    if not toks:
        return
    wts = [word_weight(t) for t in toks]
    tot = sum(wts); cur = s
    for t, wt in zip(toks, wts):
        d = (e - s) * wt / tot
        words.append({"w": t, "s": round(cur, 3), "e": round(cur + d, 3), "line": line_idx})
        cur += d


def make_chunks(words, max_words=5):
    chunks, cur = [], []
    for i, w in enumerate(words):
        cur.append(i)
        end_punct = re.search(r"[.,!?:;]$", w["w"])
        nxt_line = i + 1 < len(words) and words[i + 1]["line"] != w["line"]
        if len(cur) >= max_words or (end_punct and len(cur) >= 3) or nxt_line or i == len(words) - 1:
            if len(cur) < 3 and chunks and not nxt_line and i != len(words) - 1:
                continue
            chunks.append({"w": cur, "start": words[cur[0]]["s"], "end": words[cur[-1]]["e"]})
            cur = []
    if cur:
        chunks.append({"w": cur, "start": words[cur[0]]["s"], "end": words[cur[-1]]["e"]})
    return chunks


def music_bed(dur, bpm=96, seed=7):
    """Soft generated bed: pad chords + light kick + hats. No outside files needed."""
    rng = np.random.default_rng(seed)
    n = int(dur * SR); t = np.arange(n) / SR
    out = np.zeros(n, np.float32)
    beat = 60 / bpm; bar = beat * 4
    prog = [[60, 64, 67, 72], [55, 59, 62, 67], [57, 60, 64, 69], [53, 57, 60, 65]]  # C G Am F
    f = lambda m: 440 * 2 ** ((m - 69) / 12)
    nbars = int(dur / bar) + 1
    for b in range(nbars):
        s = int(b * bar * SR); e = min(n, int((b + 1) * bar * SR))
        if s >= n:
            break
        tt = np.arange(e - s) / SR
        env = np.minimum(1, tt / 0.6) * np.minimum(1, (bar - tt) / 0.4)
        chord = prog[b % 4]
        sig = sum(np.sin(2 * np.pi * f(m) * tt) * 0.5 + np.sin(2 * np.pi * f(m) * 2 * tt) * 0.12 for m in chord)
        bass = np.sin(2 * np.pi * f(chord[0] - 24) * tt) * 0.9
        out[s:e] += (sig * 0.07 + bass * 0.12) * env
    nb = int(dur / beat) + 1
    for k in range(nb):
        s = int(k * beat * SR)
        if s >= n:
            break
        if k % 2 == 0:
            L = min(int(0.25 * SR), n - s); tt = np.arange(L) / SR
            out[s:s + L] += np.sin(2 * np.pi * (55 + 60 * np.exp(-tt * 30)) * tt) * np.exp(-tt * 14) * 0.35
        for off in (0, beat / 2):
            s2 = int((k * beat + off) * SR)
            if s2 >= n:
                continue
            L = min(int(0.05 * SR), n - s2)
            hn = rng.standard_normal(L).astype(np.float32)
            hn = np.diff(np.concatenate([[0], hn]))
            out[s2:s2 + L] += hn * np.exp(-np.arange(L) / SR * 90) * 0.035
    fade = np.minimum(1, t / 1.0) * np.minimum(1, (dur - t) / 1.5)
    return (out * fade).astype(np.float32)


def sfx_track(dur, times):
    n = int(dur * SR); out = np.zeros(n, np.float32)
    rng = np.random.default_rng(3)
    L = int(0.4 * SR); tt = np.arange(L) / SR
    noise = rng.standard_normal(L).astype(np.float32)
    # simple band sweep: moving average of varying length
    sweep = np.zeros(L, np.float32); acc = 0.0
    for i in range(L):
        a = 0.02 + 0.5 * (i / L)
        acc = acc + a * (noise[i] - acc); sweep[i] = acc
    env = np.sin(np.pi * np.clip(tt / 0.4, 0, 1)) ** 2
    wh = sweep * env * 0.5
    for t0 in times:
        s = int(max(0, t0 - 0.2) * SR)
        e = min(n, s + L)
        out[s:e] += wh[: e - s]
    return out


def main():
    spec_path, out = sys.argv[1], sys.argv[2]
    spec = json.load(open(spec_path))
    base = os.path.dirname(os.path.abspath(spec_path))
    work = tempfile.mkdtemp(prefix="reel_")
    words = []
    gap = float(spec.get("gap", 0.22))

    if spec.get("audio"):  # pre-mixed audio + timed segments
        voice = read_wav(os.path.join(base, spec["audio"]))
        for i, sg in enumerate(spec["segments"]):
            assign_words(sg["text"], sg["start"], sg["end"], words, i)
        premixed = True
    else:
        parts, t = [], 0.25
        lead = np.zeros(int(0.25 * SR), np.float32)
        parts.append(lead)
        bounds = []
        for i, ln in enumerate(spec["lines"]):
            a = trim(read_wav(os.path.join(base, ln["wav"])))
            bounds.append((t, t + len(a) / SR))
            parts.append(a); parts.append(np.zeros(int(gap * SR), np.float32))
            t += len(a) / SR + gap
        voice = np.concatenate(parts)
        speed = 1.0
        mx = spec.get("max_seconds")
        if mx and len(voice) / SR > mx - 0.6:
            speed = min(1.15, (len(voice) / SR) / (mx - 0.6))
        if speed > 1.001:
            vin = os.path.join(work, "v_in.wav"); vout = os.path.join(work, "v_out.wav")
            write_wav(vin, voice)
            sh(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", vin, "-af", f"atempo={speed:.4f}", vout])
            voice = read_wav(vout)
            bounds = [(s / speed, e / speed) for s, e in bounds]
        for i, (ln, (s, e)) in enumerate(zip(spec["lines"], bounds)):
            assign_words(ln["text"], s, e, words, i)
        premixed = False

    dur = round(len(voice) / SR + 0.6, 2)
    chunks = make_chunks(words)

    # resolve scene starts from cues
    flat = " ".join(w["w"] for w in words).lower()
    def cue_time(cue):
        cw = cue.lower().split()
        ws = [re.sub(r"[^\w]", "", w["w"].lower()) for w in words]
        cw = [re.sub(r"[^\w]", "", c) for c in cw]
        for i in range(len(ws) - len(cw) + 1):
            if ws[i:i + len(cw)] == cw:
                return max(0.0, words[i]["s"] - 0.12)
        raise SystemExit(f"cue not found in script: {cue!r}")
    scenes = spec["scenes"]
    for i, sc in enumerate(scenes):
        if i == 0:
            sc["start"] = 0.0
        elif "cue" in sc:
            sc["start"] = round(cue_time(sc["cue"]), 3)
        elif "start" not in sc:
            raise SystemExit(f"scene {i} needs cue or start")
    starts = [s["start"] for s in scenes]
    if starts != sorted(starts):
        raise SystemExit(f"scene starts out of order: {starts}")

    icons_all = json.load(open(os.path.join(HERE, "icons.json")))
    bad = []
    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "icon" and v not in icons_all:
                    bad.append(v)
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(scenes)
    if bad:
        import difflib
        raise SystemExit("unknown icons: " + ", ".join(f"{b} (try {difflib.get_close_matches(b, icons_all, 3)})" for b in bad))
    page_spec = {"creator": spec.get("creator", ""), "duration": dur, "scenes": scenes, "words": words, "chunks": chunks}

    # ---------- frames ----------
    from playwright.sync_api import sync_playwright
    vid = os.path.join(work, "video.mp4")
    nframes = int(dur * FPS)
    ff = subprocess.Popen(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "image2pipe", "-framerate", str(FPS), "-c:v", "mjpeg", "-i", "-",
                           "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p", "-r", str(FPS), vid], stdin=subprocess.PIPE)
    problems = []
    with sync_playwright() as p:
        br = p.chromium.launch()
        pg = br.new_page(viewport={"width": 1080, "height": 1920})
        pg.goto("file://" + os.path.join(HERE, "reel_template.html"))
        icons = json.load(open(os.path.join(HERE, "icons.json")))
        pg.evaluate("([s,i])=>init(s,i)", [page_spec, icons])
        pg.evaluate("document.fonts.ready")
        checks = {}
        for i, sc in enumerate(scenes):
            end = scenes[i + 1]["start"] if i + 1 < len(scenes) else dur
            checks[int(min(end - 0.3, sc["start"] + max(1.6, (end - sc["start"]) * 0.8)) * FPS)] = i
        if os.environ.get("PREVIEW"):
            for tq in os.environ["PREVIEW"].split(","):
                pg.evaluate(f"render({float(tq)})")
                pg.screenshot(path=f"{os.path.splitext(out)[0]}_p{tq}.jpg", type="jpeg", quality=80)
            br.close(); return
        for f in range(nframes):
            t = f / FPS
            pg.evaluate(f"render({t})")
            ff.stdin.write(pg.screenshot(type="jpeg", quality=90))
            if f in checks:
                ov = pg.evaluate("overflow()")
                if ov:
                    problems.append(f"scene {checks[f]} ({scenes[checks[f]]['type']}): {ov[:4]}")
        br.close()
    ff.stdin.close(); ff.wait()

    # ---------- audio ----------
    n = int(dur * SR)
    v = np.zeros(n, np.float32); v[: min(n, len(voice))] = voice[:n]
    vpath = os.path.join(work, "voice.wav"); write_wav(vpath, v)
    sfx = sfx_track(dur, [s["start"] for s in scenes[1:]])
    spath = os.path.join(work, "sfx.wav"); write_wav(spath, sfx * (0.5 if premixed else 1.0))
    mix = os.path.join(work, "mix.wav")
    if premixed or spec.get("music") == "none":
        fc = "[0:a][1:a]amix=inputs=2:normalize=0[m]"
        ins = ["-i", vpath, "-i", spath]
    else:
        mpath = os.path.join(work, "music.wav")
        if spec.get("music") and spec["music"] != "auto":
            mus = read_wav(os.path.join(base, spec["music"]))
            mus = np.resize(mus, n) if len(mus) < n else mus[:n]
            fade = np.minimum(1, np.arange(n) / SR / 1.0) * np.minimum(1, (dur - np.arange(n) / SR) / 1.5)
            mus = mus * fade
        else:
            mus = music_bed(dur)
        mus = mus / (np.max(np.abs(mus)) + 1e-9) * 0.5
        write_wav(mpath, mus)
        fc = ("[2:a]volume=-17dB[mu];[0:a]asplit=2[v1][v2];"
              "[mu][v2]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=350[duck];"
              "[v1][duck][1:a]amix=inputs=3:normalize=0[m]")
        ins = ["-i", vpath, "-i", spath, "-i", mpath]
    pre = os.path.join(work, "pre.wav")
    sh(["ffmpeg", "-nostdin", "-v", "error", "-y", *ins, "-filter_complex", fc, "-map", "[m]", pre])
    r = subprocess.run(["ffmpeg", "-nostdin", "-i", pre, "-af", "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"], capture_output=True, text=True)
    js = json.loads(r.stderr[r.stderr.rindex("{"): r.stderr.rindex("}") + 1])
    ln = ("loudnorm=I=-14:TP=-1.5:LRA=11:measured_I={input_i}:measured_TP={input_tp}:measured_LRA={input_lra}:"
          "measured_thresh={input_thresh}:offset={target_offset}:linear=true").format(**js)
    sh(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", pre, "-af", ln + ",aresample=48000", mix])
    sh(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", vid, "-i", mix, "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", out])

    # ---------- QA ----------
    qa = os.path.splitext(out)[0] + "_qa.jpg"
    times = [round(dur * k, 2) for k in (0.06, 0.25, 0.5, 0.72, 0.92)]
    frames = []
    for i, tq in enumerate(times):
        fp = os.path.join(work, f"q{i}.jpg")
        sh(["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", str(tq), "-i", out, "-frames:v", "1", "-vf", "scale=360:-1", fp])
        frames.append(fp)
    sh(["ffmpeg", "-nostdin", "-v", "error", "-y", *sum([["-i", f] for f in frames], []), "-filter_complex", "hstack=5", qa])
    r = subprocess.run(["ffmpeg", "-nostdin", "-i", out, "-af", "loudnorm=print_format=json", "-f", "null", "-"], capture_output=True, text=True)
    lj = json.loads(r.stderr[r.stderr.rindex("{"): r.stderr.rindex("}") + 1])
    print(json.dumps({"out": out, "duration": dur, "lufs": float(lj["input_i"]), "true_peak": float(lj["input_tp"]),
                      "qa_frames": qa, "scenes": [(s["type"], s["start"]) for s in scenes], "overflow": problems}, indent=1))
    if problems:
        sys.exit(2)


if __name__ == "__main__":
    main()
