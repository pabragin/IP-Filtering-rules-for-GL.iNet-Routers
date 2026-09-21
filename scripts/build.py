#!/usr/bin/env python3
"""Сборка filter.txt для GL.iNet VPN Policy (Subscription URL).

Шаги:
1. Скачивает российские подсети IPv4/IPv6 из двух независимых источников:
   - ipverse: делегации RIR, где inetnum помечен country: RU;
   - BGP: префиксы, анонсируемые автономными системами с country: RU.
   Источники дополняют друг друга, ни один не является надмножеством другого:
   у организации записи inetnum и aut-num могут иметь разную страну. Так,
   46.243.177.0/24 (правительство СПб) записан на нидерландское юрлицо и в
   ipverse не попадает, но анонсируется российской AS203725 — его находит
   второй источник. Обратный случай: 51.250.128.0/17 (Yandex Cloud) помечен
   RU в RIR, но публично в BGP не анонсируется — его даёт только первый.
2. Добавляет ручные подсети из data/custom-subnets.txt и агрегирует всё.
3. Резолвит каждый домен из data/domains.txt, а также его www-поддомен:
   - все адреса (апекса И www) внутри итоговых подсетей -> домен покрыт,
     в filter.txt не пишется;
   - хотя бы один адрес снаружи (или домен не резолвится) -> домен остаётся.
   www проверяется отдельно, потому что у ряда сервисов апекс живёт на своих
   адресах, а www отдаётся сторонним CDN (например, apple.com против
   www.apple.com). Запись апекса матчится роутером по суффиксу и покрывает
   поддомены, поэтому исключать её можно, только когда www тоже покрыт.
4. Пишет filter.txt (домены + IPv4 + IPv6, по одной записи на строку),
   xray-routing.json (те же данные как routing-правила Xray для роутера,
   где поднят свой клиент) и report.md с результатами проверки покрытия.
"""

import ipaddress
import json
import socket
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RU_IPV4_URL = "https://raw.githubusercontent.com/ipverse/country-ip-blocks/master/country/ru/ipv4-aggregated.txt"
RU_IPV6_URL = "https://raw.githubusercontent.com/ipverse/country-ip-blocks/master/country/ru/ipv6-aggregated.txt"
RU_ASN_URL = "https://stat.ripe.net/data/country-resource-list/data.json?resource=RU"
BGP_TABLE_URL = "https://bgp.tools/table.jsonl"
# bgp.tools требует User-Agent с контактом, иначе отдаёт 429/403.
USER_AGENT = (
    "gl-inet-filter-build/1.0 "
    "(+https://github.com/pabragin/IP-Filtering-rules-for-GL.iNet-Routers)"
)
BGP_TABLE_TIMEOUT = 300
DNS_TIMEOUT = 5
DNS_WORKERS = 16


def fetch_lines(url: str) -> list[str]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        text = resp.read().decode()
    lines = [l.strip() for l in text.splitlines()]
    return [l for l in lines if l and not l.startswith("#")]


def fetch_bgp_ru_prefixes() -> tuple[list, list]:
    """Префиксы, анонсируемые в BGP автономными системами с country: RU.

    Ловит блоки, у которых aut-num помечен RU, а inetnum — нет (записан на
    иностранное юрлицо), поэтому в списках по делегациям RIR их не видно.
    Таблица bgp.tools — это ~1,5 млн строк на весь интернет (около 75 МБ),
    читаем её потоком и оставляем только нужные AS.

    Возвращает (ipv4, ipv6). При недоступности любого из двух сервисов —
    ([], []): сборка продолжается на одном источнике, чтобы сбой внешнего
    API не ломал ежедневное обновление.
    """
    try:
        req = urllib.request.Request(RU_ASN_URL, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.load(resp)
        ru_asns = {int(a) for a in payload["data"]["resources"]["asn"]}
    except Exception as exc:  # noqa: BLE001 — любой сбой сети/формата не критичен
        print(f"BGP-источник пропущен: не удалось получить список RU-AS ({exc})", file=sys.stderr)
        return [], []

    if len(ru_asns) < 1000:
        print(f"BGP-источник пропущен: подозрительно мало RU-AS ({len(ru_asns)})", file=sys.stderr)
        return [], []

    v4, v6 = [], []
    try:
        req = urllib.request.Request(BGP_TABLE_URL, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=BGP_TABLE_TIMEOUT) as resp:
            for raw in resp:
                try:
                    row = json.loads(raw)
                    if row["ASN"] not in ru_asns:
                        continue
                    net = ipaddress.ip_network(row["CIDR"])
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue
                (v6 if net.version == 6 else v4).append(net)
    except Exception as exc:  # noqa: BLE001
        print(f"BGP-источник пропущен: не удалось скачать таблицу ({exc})", file=sys.stderr)
        return [], []

    print(f"BGP-источник: {len(ru_asns)} RU-AS, префиксов v4={len(v4)}, v6={len(v6)}")
    return v4, v6


def read_list(path: Path) -> list[str]:
    lines = [l.strip() for l in path.read_text().splitlines()]
    return [l for l in lines if l and not l.startswith("#")]


def resolve(domain: str) -> list[ipaddress._BaseAddress] | None:
    """A- и AAAA-записи домена; None — домен не резолвится."""
    socket.setdefaulttimeout(DNS_TIMEOUT)
    try:
        infos = socket.getaddrinfo(domain, None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, socket.timeout, OSError):
        return None
    return sorted({ipaddress.ip_address(i[4][0]) for i in infos}, key=str)


def resolve_with_www(domain: str) -> tuple[list | None, list | None]:
    """Адреса апекса и его www-поддомена.

    Роутер матчит домены по суффиксу, поэтому запись апекса покрывает и www.
    Значит, исключать апекс из filter.txt допустимо, только если и www-адреса
    попадают в подсети: иначе трафик на www уйдёт мимо правила.
    Для доменов, которые сами начинаются с www., поддомен не запрашивается.
    """
    apex = resolve(domain)
    if domain.startswith("www."):
        return apex, None
    return apex, resolve("www." + domain)


def main() -> int:
    ru_v4 = [ipaddress.ip_network(l) for l in fetch_lines(RU_IPV4_URL)]
    ru_v6 = [ipaddress.ip_network(l) for l in fetch_lines(RU_IPV6_URL)]
    if len(ru_v4) < 1000 or len(ru_v6) < 100:
        print(f"Подозрительно короткие списки: v4={len(ru_v4)}, v6={len(ru_v6)}", file=sys.stderr)
        return 1

    bgp_v4, bgp_v6 = fetch_bgp_ru_prefixes()

    custom = [ipaddress.ip_network(l) for l in read_list(ROOT / "data" / "custom-subnets.txt")]
    nets_v4 = sorted(ipaddress.collapse_addresses(
        ru_v4 + bgp_v4 + [n for n in custom if n.version == 4]))
    nets_v6 = sorted(ipaddress.collapse_addresses(
        ru_v6 + bgp_v6 + [n for n in custom if n.version == 6]))

    # Сколько адресов добавил BGP-источник поверх ipverse+custom: видно,
    # работает ли он вообще и насколько источники расходятся.
    base_v4 = sorted(ipaddress.collapse_addresses(
        ru_v4 + [n for n in custom if n.version == 4]))
    added_v4 = sum(n.num_addresses for n in nets_v4) - sum(n.num_addresses for n in base_v4)

    def covered(ip: ipaddress._BaseAddress) -> bool:
        nets = nets_v4 if ip.version == 4 else nets_v6
        return any(ip in n for n in nets)

    domains = read_list(ROOT / "data" / "domains.txt")
    with ThreadPoolExecutor(DNS_WORKERS) as pool:
        resolved = dict(zip(domains, pool.map(resolve_with_www, domains)))

    kept, dropped, unresolved = [], [], []
    for d in domains:
        ips, www_ips = resolved[d]
        if ips is None:
            unresolved.append(d)
            kept.append(d)
        elif all(covered(ip) for ip in ips) and all(covered(ip) for ip in www_ips or []):
            dropped.append((d, ips))
        else:
            kept.append(d)

    out = kept + [str(n) for n in nets_v4] + [str(n) for n in nets_v6]
    (ROOT / "filter.txt").write_text("\n".join(out) + "\n")

    # Те же данные в виде routing-правил Xray: роутер скачивает файл и
    # подставляет его в конфиг целиком, без разбора списков на своей стороне.
    routing = {
        "rules": [
            {
                "type": "field",
                "domain": [f"domain:{d}" for d in kept],
                "outboundTag": "direct",
            },
            {
                "type": "field",
                "ip": [str(n) for n in nets_v4] + [str(n) for n in nets_v6],
                "outboundTag": "direct",
            },
        ]
    }
    (ROOT / "xray-routing.json").write_text(
        json.dumps(routing, ensure_ascii=False, separators=(",", ":")) + "\n"
    )

    report = [
        "# Отчёт сборки filter.txt",
        "",
        f"- Подсетей IPv4: **{len(nets_v4)}**, IPv6: **{len(nets_v6)}**",
        f"- Источники подсетей: ipverse (RIR) + BGP по RU-AS"
        f"{'' if bgp_v4 else ' — BGP недоступен, собрано только по ipverse'}",
        f"- BGP добавил поверх ipverse: **{added_v4:,}** адресов IPv4".replace(",", " "),
        f"- Доменов в источнике: **{len(domains)}**, оставлено в filter.txt: **{len(kept)}**,"
        f" покрыто подсетями (исключено): **{len(dropped)}**",
        "",
        "## Исключённые домены (все адреса уже внутри подсетей)",
        "",
    ]
    report += [f"- `{d}` — {', '.join(map(str, ips))}" for d, ips in dropped] or ["_нет_"]
    report += ["", "## Оставленные домены (адреса вне подсетей — иностранный хостинг/CDN)", ""]
    report += [f"- `{d}`" for d in kept if d not in unresolved]
    report += ["", "## Не резолвятся (оставлены на всякий случай)", ""]
    report += [f"- `{d}`" for d in unresolved] or ["_нет_"]
    (ROOT / "report.md").write_text("\n".join(report) + "\n")

    print(f"filter.txt: {len(out)} строк ({len(kept)} доменов, {len(nets_v4)} IPv4, {len(nets_v6)} IPv6)")
    print(f"xray-routing.json: {len(kept)} доменов, {len(nets_v4) + len(nets_v6)} подсетей")
    print(f"Исключено доменов, покрытых подсетями: {len(dropped)}; не резолвятся: {len(unresolved)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
