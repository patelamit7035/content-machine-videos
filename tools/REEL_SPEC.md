# Reel renderer: how to use

```
python3 tools/make_reel.py spec.json reels/<file>.mp4
```

It needs Python, `numpy`, `playwright` (Chromium) and `ffmpeg`. If `numpy` is missing: `pip install --break-system-packages numpy`.
Output: 1080x1920 MP4 at 30 fps, -14 LUFS, plus `<file>_qa.jpg` (5 frames side by side) for the quality check.
Exit code 2 = some text runs off screen: fix the words (shorter) and run again.

## spec.json

```json
{
  "creator": "Amit Patel",
  "max_seconds": 40,
  "music": "auto",
  "lines": [
    {"text": "Hook line, exactly as spoken.", "wav": "l1.wav"},
    {"text": "Point one ...", "wav": "l2.wav"}
  ],
  "scenes": [
    {"type": "hook", "pills": ["Meta", "OpenAI"], "icon": "bot", "headline": "AI jo *sone ke baad* bhi kaam kare"},
    {"type": "phone", "cue": "Pehli baat", "chip": "POINT 1", "icon": "bot", "title": "Aapka AI agent",
     "cards": [{"icon": "mail", "text": "3 naye emails padhe", "meta": "background mein"}]}
  ]
}
```

- `lines`: one entry per voice WAV, `text` = the exact words spoken in that WAV (captions come from this).
- `scenes`: the first scene starts at 0. Every other scene needs `cue`: 2 or 3 words copied exactly from the script; the scene starts when those words are spoken.
- Change scene every 3 to 6 seconds. Use 6 to 9 scenes for a 35 to 40 second reel. Never two scenes of the same type in a row.
- `*word*` makes a word green. `chip` shows a green label on top (POINT 1, POINT 2 ...).
- `icon`: any Lucide icon name (tools/icons.json), e.g. bot, mail, calendar, bell, rocket, clock, brain, wallet, indian-rupee, trending-up, users, message-circle, smartphone, zap, target, shield-check, list-checks, bookmark, moon-star, sparkles, chart-line, briefcase, store, phone, video, camera. Unknown names stop the render with suggestions.
- `music`: "auto" (built-in soft bed, ducked under the voice), "none", or a path to a royalty-free file.

## Scene types

| type | fields | use it for |
|---|---|---|
| hook | pills[], icon, headline | first 3 to 5 seconds: names, big icon, headline |
| big | icon, text, sub | one strong idea or keyword |
| phone | icon, title, cards[{icon,text,meta}] | apps, notifications, messages, what AI does on a phone |
| approval | icon, title, question, yes, no, done | permission / control / human-in-loop moments |
| checklist | icon, title, items[] | steps, lists, "roz ke kaam" |
| flow | title, nodes[{icon,label}], sub | systems, A to B to C, automation chains (2 to 4 nodes) |
| compare | title, left{title,items[]}, right{title,items[]} | before vs after, wrong vs right (left = bad, right = good) |
| stat | icon, value, prefix, suffix, label | one real, verified number (counts up) |
| cta | icon, text, sub | last scene: save / follow / share |

Rules: short text on screen (max ~6 words per line), no fake numbers, keep text Hinglish in Latin script.
