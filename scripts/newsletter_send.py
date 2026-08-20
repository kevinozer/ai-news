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
import argparse, json, os, re, sys, urllib.error, urllib.parse, urllib.request
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
    """
    Vizual podle konceptu newsletter-koncept-vizualu.html: tmava hlavicka
    s logem, hero, hlavni zprava tydne, cislovane novinky a tmavy blok
    s dalsimi titulky. Vse tabulkove a s inline styly kvuli Outlooku.
    """
    hlavni = p[0] if p else None
    cislovane = p[1:]
    minut = max(2, round(len(p) * 0.4))

    def odkaz_text(i):
        return 'Přečíst na aicruz.cz' if i['nasweb'] else 'Přečíst u zdroje'

    hl = ''
    if hlavni:
        hl = f"""
          <tr><td class="pad" style="padding:26px 34px 18px;">
            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#f1edff;border-radius:18px;">
              <tr>
                <td width="6" style="width:6px;background:#7c3aed;font-size:0;line-height:0;">&nbsp;</td>
                <td style="padding:24px;">
                  <div style="font-family:Arial,Helvetica,sans-serif;font-size:11px;line-height:16px;letter-spacing:1.2px;color:#6d28d9;font-weight:bold;text-transform:uppercase;">Hlavní zpráva týdne &middot; {esc(hlavni['zdroj'])}</div>
                  <div style="font-family:Arial,Helvetica,sans-serif;font-size:25px;line-height:29px;font-weight:bold;color:#111111;padding-top:9px;">{esc(hlavni['nadpis'])}</div>
                  <div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:22px;color:#4d4d57;padding-top:10px;">{esc(hlavni['popis'])}</div>
                  <table role="presentation" cellspacing="0" cellpadding="0" border="0" style="margin-top:16px;">
                    <tr><td bgcolor="#111111" style="background:#111111;border-radius:999px;padding:10px 15px;"><a href="{esc(hlavni['odkaz'])}" style="color:#ffffff;text-decoration:none;font-family:Arial,Helvetica,sans-serif;font-size:13px;font-weight:bold;">{odkaz_text(hlavni)} &rarr;</a></td></tr>
                  </table>
                </td>
              </tr>
            </table>
          </td></tr>"""

    radky = []
    for n, i in enumerate(cislovane, 1):
        radky.append(f"""
          <tr><td class="pad" style="padding:16px 34px;">
            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
              <tr>
                <td width="54" valign="top" style="width:54px;font-family:Arial,Helvetica,sans-serif;font-size:28px;line-height:28px;color:#d7d7db;font-weight:bold;padding-top:4px;">{n:02d}</td>
                <td valign="top">
                  <div style="font-family:Arial,Helvetica,sans-serif;font-size:10px;line-height:14px;letter-spacing:1.1px;color:#8a8a92;font-weight:bold;text-transform:uppercase;">{esc(i['zdroj'])}</div>
                  <div style="font-family:Arial,Helvetica,sans-serif;font-size:18px;line-height:23px;font-weight:bold;color:#111111;padding-top:5px;">{esc(i['nadpis'])}</div>
                  <div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:21px;color:#5f5f68;padding-top:7px;">{esc(i['popis'])}</div>
                  <div style="padding-top:8px;"><a href="{esc(i['odkaz'])}" style="font-family:Arial,Helvetica,sans-serif;font-size:12px;color:#6d28d9;text-decoration:none;font-weight:bold;">{odkaz_text(i)} &rarr;</a></div>
                </td>
              </tr>
            </table>
          </td></tr>
          <tr><td class="pad" style="padding:0 34px;"><div style="height:1px;background:#ececef;font-size:0;line-height:0;">&nbsp;</div></td></tr>""")

    return f"""<!DOCTYPE html>
<html lang="cs"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="X-UA-Compatible" content="IE=edge">
<meta name="x-apple-disable-message-reformatting">
<title>AI novinky k {esc(datum)}</title>
<style>
@media only screen and (max-width:680px){{
  .shell{{width:100%!important;}}
  .pad{{padding-left:22px!important;padding-right:22px!important;}}
  .hero-title{{font-size:32px!important;line-height:34px!important;}}
}}
</style>
</head>
<body style="margin:0;padding:0;background:#f2f2ef;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" bgcolor="#f2f2ef" style="background:#f2f2ef;">
<tr><td align="center" style="padding:34px 12px;">
<table role="presentation" class="shell" width="640" cellspacing="0" cellpadding="0" border="0" bgcolor="#ffffff" style="width:640px;max-width:640px;background:#ffffff;border-radius:24px;">

  <tr><td class="pad" bgcolor="#0d0d0f" style="padding:22px 34px;background:#0d0d0f;border-radius:24px 24px 0 0;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
      <tr>
        <td valign="middle"><img src="{WEB}/cruz-wordmark-white-sm.png" width="96" height="18" border="0" alt="CRUZ" style="display:block;width:96px;height:18px;color:#ffffff;font-family:Arial,Helvetica,sans-serif;font-size:17px;font-weight:bold;letter-spacing:1px;"></td>
        <td align="right" valign="middle" style="font-family:Arial,Helvetica,sans-serif;font-size:12px;line-height:18px;color:#9f9fa7;">AI NOVINKY<br><span style="color:#ffffff;">{esc(datum.upper())}</span></td>
      </tr>
    </table>
  </td></tr>

  <tr><td class="pad" bgcolor="#0d0d0f" style="padding:30px 34px;background:#0d0d0f;">
    <div style="font-family:Arial,Helvetica,sans-serif;font-size:11px;line-height:16px;letter-spacing:1.6px;color:#a78bfa;font-weight:bold;text-transform:uppercase;padding-bottom:16px;">Týdenní výběr &middot; bez balastu</div>
    <div class="hero-title" style="font-family:Arial,Helvetica,sans-serif;font-size:40px;line-height:42px;color:#ffffff;font-weight:bold;">Co se za týden stalo ve světě AI.</div>
    <div style="font-family:Arial,Helvetica,sans-serif;font-size:16px;line-height:26px;color:#b7b7bf;padding-top:18px;">To nejdůležitější z modelů, produktů a firem. Krátce, česky a s odkazem na celý kontext.</div>
    <table role="presentation" cellspacing="0" cellpadding="0" border="0" style="margin-top:24px;">
      <tr>
        <td bgcolor="#ffffff" style="background:#ffffff;border-radius:999px;padding:9px 13px;font-family:Arial,Helvetica,sans-serif;font-size:12px;font-weight:bold;color:#111111;">{len(p)} hlavních zpráv</td>
        <td width="8" style="width:8px;">&nbsp;</td>
        <td style="border:1px solid #34343a;border-radius:999px;padding:9px 13px;font-family:Arial,Helvetica,sans-serif;font-size:12px;color:#c9c9cf;">asi {minut} min čtení</td>
      </tr>
    </table>
  </td></tr>
{hl}
  <tr><td class="pad" style="padding:10px 34px 0;">
    <div style="font-family:Arial,Helvetica,sans-serif;font-size:11px;line-height:16px;letter-spacing:1.4px;color:#777780;font-weight:bold;text-transform:uppercase;">Další důležité novinky</div>
  </td></tr>
{''.join(radky)}
  <tr><td class="pad" style="padding:8px 34px 34px;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="border:1px solid #e7e7ea;border-radius:18px;">
      <tr><td style="padding:22px;">
        <div style="font-family:Arial,Helvetica,sans-serif;font-size:20px;line-height:25px;font-weight:bold;color:#111111;">Chcete celý přehled?</div>
        <div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:21px;color:#66666f;padding-top:6px;">Na aicruz.cz najdete všech {celkem} novinek a průběžně doplňovaný přehled ze světa AI.</div>
        <table role="presentation" cellspacing="0" cellpadding="0" border="0" style="margin-top:15px;">
          <tr><td bgcolor="#7c3aed" style="background:#7c3aed;border-radius:999px;padding:11px 16px;"><a href="{WEB}/komunita.html" style="color:#ffffff;text-decoration:none;font-family:Arial,Helvetica,sans-serif;font-size:13px;font-weight:bold;">Otevřít aicruz.cz &rarr;</a></td></tr>
        </table>
      </td></tr>
    </table>
  </td></tr>

  <tr><td class="pad" bgcolor="#f7f7f4" style="padding:24px 34px 28px;background:#f7f7f4;border-top:1px solid #ececef;border-radius:0 0 24px 24px;">
    <div style="font-family:Arial,Helvetica,sans-serif;font-size:12px;line-height:19px;color:#777780;">CRUZ &middot; AI Transformation Company &middot; <a href="{WEB}" style="color:#55555c;text-decoration:underline;">aicruz.cz</a></div>
    <div style="font-family:Arial,Helvetica,sans-serif;font-size:11px;line-height:18px;color:#9898a0;padding-top:8px;">Nechcete novinky dostávat? <a href="{{{{{{RESEND_UNSUBSCRIBE_URL}}}}}}" style="color:#777780;text-decoration:underline;">Odhlaste se jedním kliknutím</a>.</div>
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
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode('utf-8') or '{}')
    except urllib.error.HTTPError as e:
        # Bez tela odpovedi je v logu jen "HTTP Error 403: Forbidden" a hada se,
        # jestli je spatne klic, workspace, domena nebo tarif. Resend posila duvod
        # v JSONu, tak ho vypsat.
        try:
            duvod = e.read().decode('utf-8', 'replace')[:600]
        except Exception:
            duvod = '(telo odpovedi se nepodarilo precist)'
        print(f'❌ Resend {e.code} na POST {cesta}: {duvod}', file=sys.stderr)
        raise


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
    if klic:
        # diagnostika bez prozrazeni klice: kratky paste nebo prazdno je hned videt
        print(f'[newsletter] klic: prefix={klic[:3]!r}, delka={len(klic)}')
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
