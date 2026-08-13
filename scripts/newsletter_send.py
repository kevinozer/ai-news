#!/usr/bin/env python3
"""
newsletter_send.py - tydenni prehled odberatelum newsletteru aicruz.cz.

Bere stejny podklad jako PDF pro sefa (digest/weekly_<end>.json), ale misto
prilohy posle HTML e-mail s odkazy. Kde k novince existuje clanek na
aicruz.cz, odkaz vede tam (parujeme podle zdrojove URL), jinak na puvodni
zdroj.

Rozesila se pres Resend Broadcast do segmentu, takze odhlasovaci odkaz
i evidenci odhlasenych resi Resend sam.

Spusteni:
  python3 scripts/newsletter_send.py --end 2026-08-17
  python3 scripts/newsletter_send.py --end 2026-08-17 --dry-run   # jen vypis
  python3 scripts/newsletter_send.py --end 2026-08-17 --test-to a@b.cz

Promenne prostredi: RESEND_API_KEY
"""
from __future__ import annotations
import argparse, json, os, re, sys, urllib.parse, urllib.request
from datetime import datetime
from pathlib import Path

SEGMENT = 'be1d7500-39d6-42f9-b6fb-694e8bf668fe'   # Newsletter aicruz.cz
ODESILATEL = 'CRUZ <newsletter@send.aicruz.cz>'
ODPOVED = 'vladimir.cruz@aicruz.cz'
WEB = 'https://aicruz.cz'
API = 'https://api.resend.com'
MAX_POLOZEK = 12          # kolik novinek jde do mailu (v PDF pro sefa jich je ~25)

MESIC = ['', 'ledna', 'února', 'března', 'dubna', 'května', 'června',
         'července', 'srpna', 'září', 'října', 'listopadu', 'prosince']


def datum_cesky(iso: str) -> str:
    d = datetime.strptime(iso, '%Y-%m-%d')
    return f'{d.day}. {MESIC[d.month]} {d.year}'


def esc(s) -> str:
    return (str(s or '').replace('&', '&amp;').replace('<', '&lt;')
            .replace('>', '&gt;').replace('"', '&quot;'))


def klic_url(u: str) -> str:
    """Normalizovana podoba URL pro parovani novinky s clankem na webu."""
    if not u:
        return ''
    p = urllib.parse.urlsplit(u.strip())
    host = (p.netloc or '').lower()
    if host.startswith('www.'):
        host = host[4:]
    cesta = re.sub(r'/+$', '', p.path or '')
    return f'{host}{cesta}'.lower()


def clanky_na_webu() -> dict:
    """Mapa zdrojova URL -> slug clanku na aicruz.cz."""
    try:
        with urllib.request.urlopen(f'{WEB}/articles/index.json', timeout=20) as r:
            data = json.loads(r.read().decode('utf-8'))
    except Exception as e:
        print(f'[newsletter] index.json se nepodarilo nacist: {e}', file=sys.stderr)
        return {}
    out = {}
    for a in data.get('articles', []):
        k = klic_url(a.get('source_url', ''))
        if k and k not in out:
            out[k] = a.get('slug', '')
    return out


def polozky(weekly: dict, mapa: dict) -> list:
    out = []
    for a in weekly.get('articles', [])[:MAX_POLOZEK]:
        slug = mapa.get(klic_url(a.get('url', '')))
        out.append({
            'nadpis': a.get('title_cs') or a.get('title_orig') or '',
            'popis': (a.get('summary_cs') or [''])[0] or a.get('why_matters') or '',
            'zdroj': a.get('source') or '',
            'odkaz': f"{WEB}/articles/{slug}/" if slug else (a.get('url') or WEB),
            'nasweb': bool(slug),
        })
    return out


def html_mail(datum: str, p: list, celkem: int) -> str:
    radky = []
    for i in p:
        pod = esc(i['zdroj']) if not i['nasweb'] else f"{esc(i['zdroj'])} &middot; čteme na aicruz.cz"
        radky.append(f"""
          <tr><td style="padding:0 0 22px 0;font-family:Arial,Helvetica,sans-serif">
            <div style="font-family:Arial,Helvetica,sans-serif;font-size:11px;line-height:16px;letter-spacing:1px;text-transform:uppercase;color:#8a8a8a;padding-bottom:6px">{pod}</div>
            <a href="{esc(i['odkaz'])}" style="font-family:Arial,Helvetica,sans-serif;font-size:16px;line-height:22px;font-weight:bold;color:#111111;text-decoration:none">{esc(i['nadpis'])}</a>
            <div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:21px;color:#555555;padding-top:6px">{esc(i['popis'])}</div>
          </td></tr>""")
    vic = ''
    if celkem > len(p):
        vic = (f'<tr><td style="padding:0 0 24px 0"><a href="{WEB}/komunita.html" '
               'style="font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:20px;color:#7C3AED;text-decoration:none">'
               f'Všech {celkem} novinek a denní přehled na aicruz.cz &rarr;</a></td></tr>')

    return f"""<!DOCTYPE html>
<html lang="cs"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="X-UA-Compatible" content="IE=edge">
<title>AI novinky k {esc(datum)}</title>
</head>
<body style="margin:0;padding:0;background-color:#f5f5f2">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#f5f5f2" style="background-color:#f5f5f2">
<tr><td align="center" style="padding-top:32px;padding-bottom:32px;padding-left:16px;padding-right:16px">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" bgcolor="#ffffff" style="width:600px;max-width:600px;background-color:#ffffff;border-radius:14px">
<tr><td style="padding-top:36px;padding-bottom:8px;padding-left:32px;padding-right:32px">
  <div style="font-family:Arial,Helvetica,sans-serif;font-size:11px;line-height:16px;letter-spacing:2px;text-transform:uppercase;color:#8a8a8a;padding-bottom:10px">AI novinky &middot; {esc(datum)}</div>
  <div style="font-family:Arial,Helvetica,sans-serif;font-size:21px;line-height:28px;font-weight:bold;color:#111111">Co se za týden stalo ve světě AI</div>
</td></tr>
<tr><td style="padding-top:12px;padding-bottom:24px;padding-left:32px;padding-right:32px;font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:24px;color:#333333">
  Výběr toho podstatného za uplynulý týden. Krátce, česky a bez balastu.
</td></tr>
<tr><td style="padding-left:32px;padding-right:32px">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
    {''.join(radky)}
    {vic}
  </table>
</td></tr>
<tr><td style="padding-top:20px;padding-bottom:32px;padding-left:32px;padding-right:32px;border-top:1px solid #e6e6e2;font-family:Arial,Helvetica,sans-serif;font-size:13px;line-height:20px;color:#8a8a8a">
  CRUZ, AI Transformation Company &middot; <a href="{WEB}" style="color:#8a8a8a;text-decoration:underline">aicruz.cz</a><br>
  Nechcete novinky dostávat? <a href="{{{{{{RESEND_UNSUBSCRIBE_URL}}}}}}" style="color:#8a8a8a;text-decoration:underline">Odhlaste se jedním kliknutím</a>.
</td></tr>
</table>
</td></tr></table>
</body></html>"""


def text_mail(datum: str, p: list, celkem: int) -> str:
    radky = '\n\n'.join(
        f"{i['nadpis']}\n{i['popis']}\n{i['odkaz']}" for i in p)
    vic = f'\n\nVšech {celkem} novinek a denní přehled: {WEB}/komunita.html' if celkem > len(p) else ''
    return (f"Co se za týden stalo ve světě AI ({datum})\n\n"
            f"Výběr toho podstatného za uplynulý týden. Krátce, česky a bez balastu.\n\n"
            f"{radky}{vic}\n\n"
            f"CRUZ, AI Transformation Company, {WEB}\n"
            "Odhlášení: {{{RESEND_UNSUBSCRIBE_URL}}}")


def posli(cesta: str, telo: dict, klic: str) -> dict:
    req = urllib.request.Request(
        f'{API}{cesta}',
        data=json.dumps(telo, ensure_ascii=False).encode('utf-8'),
        headers={'Authorization': f'Bearer {klic}', 'Content-Type': 'application/json'},
        method='POST')
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode('utf-8') or '{}')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--ai-news', default='.')
    ap.add_argument('--end', required=True, help='stejny datum jako u tydenniho PDF')
    ap.add_argument('--dry-run', action='store_true', help='jen vypis, nic neodesilat')
    ap.add_argument('--test-to', default='', help='poslat jen na tuhle adresu misto celeho seznamu')
    args = ap.parse_args()

    cesta = Path(args.ai_news).resolve() / 'digest' / f'weekly_{args.end}.json'
    if not cesta.exists():
        print(f'❌ chybi {cesta}', file=sys.stderr)
        return 3
    weekly = json.loads(cesta.read_text(encoding='utf-8'))
    celkem = len(weekly.get('articles', []))

    p = polozky(weekly, clanky_na_webu())
    if not p:
        print('❌ zadne polozky k odeslani', file=sys.stderr)
        return 3
    na_webu = sum(1 for i in p if i['nasweb'])
    print(f'[newsletter] {len(p)} z {celkem} novinek, z toho {na_webu} odkazuje na aicruz.cz')

    datum = datum_cesky(args.end)
    predmet = f'AI novinky k {datum}'
    html, text = html_mail(datum, p, celkem), text_mail(datum, p, celkem)

    if args.dry_run:
        print('[newsletter] dry-run, neodesilam. Nahled textove verze:\n')
        print(text)
        return 0

    klic = os.environ.get('RESEND_API_KEY')
    if not klic:
        print('❌ chybi RESEND_API_KEY', file=sys.stderr)
        return 2

    if args.test_to:
        # test jde jako obycejny e-mail, at se nepocita mezi kampane
        r = posli('/emails', {
            'from': ODESILATEL, 'to': [args.test_to], 'reply_to': ODPOVED,
            'subject': f'[TEST] {predmet}',
            'html': html.replace('{{{RESEND_UNSUBSCRIBE_URL}}}', f'{WEB}/odhlasit'),
            'text': text.replace('{{{RESEND_UNSUBSCRIBE_URL}}}', f'{WEB}/odhlasit'),
        }, klic)
        print(f'[newsletter] ✓ testovaci mail na {args.test_to}: {r.get("id")}')
        return 0

    b = posli('/broadcasts', {
        'name': f'Tydenni AI novinky {args.end}',
        'segment_id': SEGMENT,
        'from': ODESILATEL,
        'reply_to': [ODPOVED],   # nazvy poli dle dokumentace: segment_id, reply_to
        'subject': predmet,
        'html': html,
        'text': text,
    }, klic)
    bid = b.get('id')
    if not bid:
        print(f'❌ broadcast se nezalozil: {b}', file=sys.stderr)
        return 2
    posli(f'/broadcasts/{bid}/send', {}, klic)
    print(f'[newsletter] ✓ rozeslano odberatelum, broadcast {bid}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
