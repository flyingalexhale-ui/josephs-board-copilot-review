#!/usr/bin/env python3
"""Make one WAV clip with a Piper voice for Alex's channel (jb-anchor). Runs on the Mac, free, no internet.
Usage: jb-piper.py VOICE_DIR OUT.wav SPEED < text      (the channel calls this)
       jb-piper.py --test                               (install: checks each voice and how fast this Mac makes speech)
If it fails, the channel falls back to the Mac's own voice (Samantha)."""
import sys, os, glob, wave, array

def test():
    import json, time
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    vroot = os.path.join(root, 'voices')
    names = sorted(n for n in os.listdir(vroot) if glob.glob(os.path.join(vroot, n, '*.onnx'))) if os.path.isdir(vroot) else []
    if not names:
        print('Natural voices: none downloaded, so the channel keeps the Mac voice.')
        return
    text = 'Good evening, Alex. Nasdaq futures are up about one and a half percent tonight, and Nvidia is leading the way.'
    worst, good = 0.0, []
    for n in names:
        out = os.path.join('/tmp', 'jb-piper-test.wav')
        t0 = time.time()
        try:
            sys.stdin = __import__('io').StringIO(text)
            sys.argv = [sys.argv[0], os.path.join(vroot, n), out, '1.0']
            main()
            with wave.open(out) as w:
                secs = w.getnframes() / float(w.getframerate())
            took = time.time() - t0
            worst = max(worst, took / secs)
            good.append(n)
            print(f'  {n}: OK ({secs:.1f} s of speech made in {took:.1f} s)')
        except Exception as e:
            print(f'  {n}: did not work ({str(e)[:80]})')
    cfg_path = os.path.join(root, 'data', 'channel_voices.json')
    try:
        cfg = json.load(open(cfg_path))
    except Exception:
        cfg = {}
    cfg['piper_rtf'] = round(worst, 2) if good else None
    with open(cfg_path, 'w') as f:
        json.dump(cfg, f)
    if not good:
        print('Natural voices: none worked on this Mac, so the channel keeps the Mac voice.')
    elif worst > 0.8:
        print('Natural voices: they work but this Mac makes them slowly, so the channel starts on the Mac voice.')
        print('You can still try them on the Voices page (channel-voices.html).')
    else:
        print('Natural voices: OK. The channel now uses them (change them on the Voices page, channel-voices.html).')


def main():
    if sys.argv[1:2] == ['--test']:
        return test()
    vdir, out, speed = sys.argv[1], sys.argv[2], float(sys.argv[3] if len(sys.argv) > 3 else 1.0)
    text = sys.stdin.read().strip()
    import sherpa_onnx
    model = [m for m in glob.glob(os.path.join(vdir, '*.onnx'))][0]
    cfg = sherpa_onnx.OfflineTtsConfig(
        model=sherpa_onnx.OfflineTtsModelConfig(
            vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                model=model, tokens=os.path.join(vdir, 'tokens.txt'),
                data_dir=os.path.join(vdir, 'espeak-ng-data')),
            num_threads=2, provider='cpu'),
        max_num_sentences=1)
    tts = sherpa_onnx.OfflineTts(cfg)
    # Paragraph breaks become short silences, like a reader taking a breath.
    rate, pcm = None, array.array('h')
    for i, part in enumerate(p.strip() for p in text.split('\n\n')):
        if not part:
            continue
        a = tts.generate(part, sid=0, speed=speed)
        rate = a.sample_rate
        if i:
            pcm.extend([0] * int(rate * 0.45))
        pcm.extend(int(max(-1.0, min(1.0, s)) * 32000) for s in a.samples)
    if rate is None:
        sys.exit('no text')
    with wave.open(out, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(pcm.tobytes())

if __name__ == '__main__':
    main()
